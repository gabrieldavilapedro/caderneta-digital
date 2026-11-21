"""Regras de negócio do estoque.

O estoque de um produto é a soma dos seus movimentos não cancelados e NUNCA
pode ficar negativo, nem hoje nem em nenhum ponto do histórico: vendas, perdas
(inclusive com data retroativa), ajustes e cancelamentos que deixariam o
estoque abaixo de zero são recusados.

Todas as operações rodam em transação e bloqueiam os produtos envolvidos
(SELECT ... FOR UPDATE), sempre na mesma ordem (por id), para que duas vendas
simultâneas não vendam o mesmo item duas vezes.
"""
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from caderneta.excecoes import RegraDeNegocioError

from .models import ZERO_QTD, MovimentoEstoque, Produto

CENTAVO = Decimal("0.01")
MILESIMO = Decimal("0.001")
ENTRADA = MovimentoEstoque.Tipo.ENTRADA
VENDA = MovimentoEstoque.Tipo.VENDA
PERDA = MovimentoEstoque.Tipo.PERDA
AJUSTE = MovimentoEstoque.Tipo.AJUSTE


@dataclass
class Item:
    """Um produto e a quantidade vendida."""

    produto: Produto
    quantidade: Decimal

    @property
    def subtotal(self):
        return (self.quantidade * self.produto.preco_venda).quantize(CENTAVO, ROUND_HALF_UP)

    def __str__(self):
        return f"{self.produto.formatar(self.quantidade)} {self.produto.nome}"


# --- Consultas ---------------------------------------------------------------

def estoque_do_produto(produto_id):
    return Produto.objects.com_estoque().values_list("quantidade", flat=True).get(pk=produto_id)


def produtos_com_estoque_baixo(limite=None):
    """Produtos ativos com estoque igual ou abaixo do mínimo (inclui os zerados)."""
    produtos = (
        Produto.objects.com_estoque()
        .filter(ativo=True, quantidade__lte=F("estoque_minimo"))
        .order_by("quantidade", "nome")
    )
    return produtos[:limite] if limite else produtos


def resumo_estoque():
    produtos = list(Produto.objects.com_estoque().filter(ativo=True))
    com_saldo = [p for p in produtos if p.quantidade > 0]
    return {
        "produtos_ativos": len(produtos),
        "estoque_baixo": sum(1 for p in produtos if p.quantidade <= p.estoque_minimo),
        "sem_estoque": sum(1 for p in produtos if p.quantidade <= 0),
        "valor_venda": sum((p.quantidade * p.preco_venda for p in com_saldo), Decimal(0)).quantize(CENTAVO),
        "valor_custo": sum(
            (p.quantidade * p.preco_custo for p in com_saldo if p.preco_custo is not None), Decimal(0)
        ).quantize(CENTAVO),
    }


def historico_do_produto(produto):
    """Movimentos do mais recente ao mais antigo, com o estoque após cada um."""
    movimentos = list(produto.movimentos.select_related("venda_a_prazo__cliente").order_by("data", "id"))
    estoque = ZERO_QTD
    for movimento in movimentos:
        if movimento.cancelado:
            movimento.estoque_apos = None
            continue
        estoque += movimento.quantidade
        movimento.estoque_apos = estoque
    movimentos.reverse()
    return movimentos


def total_dos_itens(itens):
    return sum((item.subtotal for item in itens), Decimal("0.00"))


# --- Validações internas -------------------------------------------------------

def _bloquear(produto_ids):
    produtos = Produto.objects.select_for_update().filter(pk__in=list(produto_ids)).order_by("pk")
    return {produto.pk: produto for produto in produtos}


def _bloquear_um(produto):
    produto_id = produto.pk if isinstance(produto, Produto) else produto
    return _bloquear([produto_id])[produto_id]


def _validar_quantidade(produto, quantidade, campo="quantidade"):
    quantidade = Decimal(quantidade)
    if quantidade <= 0:
        raise RegraDeNegocioError("Informe uma quantidade maior que zero.", campo)
    if not produto.fracionado and quantidade != quantidade.to_integral_value():
        raise RegraDeNegocioError(
            f"{produto.nome} é controlado por {produto.sigla}: informe uma quantidade inteira.", campo
        )
    return quantidade.quantize(MILESIMO)


def _validar_data(data):
    hoje = timezone.localdate()
    data = data or hoje
    if data > hoje:
        raise RegraDeNegocioError("A data não pode estar no futuro.", "data")
    return data


def _exigir_ativo(produto, campo=None):
    if not produto.ativo:
        raise RegraDeNegocioError(
            f"O produto {produto.nome} está inativo. Reative o cadastro para registrar entradas e vendas.", campo
        )


def _historico_fica_negativo(produto_id, *, data=None, quantidade=None, sem_movimento=None):
    """Simula o histórico do produto (mesma ordem da tela: data e registro) e diz se
    o estoque fica abaixo de zero em algum ponto a partir da mudança.

    - data + quantidade: inclui um movimento novo naquela data (depois dos do mesmo dia);
    - sem_movimento: retira um movimento existente (cancelamento).
    """
    movimentos = list(
        MovimentoEstoque.objects.filter(produto_id=produto_id, cancelado=False)
        .order_by("data", "id")
        .values_list("pk", "data", "quantidade")
    )
    if sem_movimento is not None:
        inicio = next((i for i, m in enumerate(movimentos) if m[0] == sem_movimento), len(movimentos))
        movimentos = [m for m in movimentos if m[0] != sem_movimento]
    else:
        inicio = sum(1 for m in movimentos if m[1] <= data)
        movimentos.insert(inicio, (None, data, quantidade))

    estoque = sum((m[2] for m in movimentos[:inicio]), ZERO_QTD)
    for _, _, variacao in movimentos[inicio:]:
        estoque += variacao
        if estoque < 0:
            return True
    return False


def _exigir_disponivel(produto, quantidade, campo, data):
    disponivel = estoque_do_produto(produto.pk)
    if quantidade > disponivel:
        raise RegraDeNegocioError(
            f"Estoque insuficiente de {produto.nome}: disponível {produto.formatar(disponivel)}, "
            f"solicitado {produto.formatar(quantidade)}.",
            campo,
        )
    if _historico_fica_negativo(produto.pk, data=data, quantidade=-quantidade):
        raise RegraDeNegocioError(
            f"Estoque insuficiente de {produto.nome} em {data:%d/%m/%Y}: nessa data o produto ainda "
            "não tinha essa quantidade em estoque. Use uma data igual ou posterior à entrada do produto.",
            campo,
        )


def _recalcular_custo_apos_cancelar(produto, entrada):
    """Desfaz a atualização do preço de custo feita por uma entrada cancelada.

    Só mexe no custo se ele ainda for o desta entrada e se nenhuma entrada mais
    recente (com custo) o tiver substituído. Volta para o custo da entrada válida
    anterior; se não houver, o custo fica em branco (desconhecido).
    """
    if entrada.tipo != ENTRADA or entrada.preco_unitario is None:
        return
    if produto.preco_custo != entrada.preco_unitario:
        return  # o custo foi alterado depois (edição manual ou outra entrada)
    entradas_com_custo = MovimentoEstoque.objects.filter(
        produto=produto, tipo=ENTRADA, cancelado=False, preco_unitario__isnull=False
    )
    if entradas_com_custo.filter(pk__gt=entrada.pk).exists():
        return
    produto.preco_custo = entradas_com_custo.order_by("-pk").values_list("preco_unitario", flat=True).first()
    produto.save(update_fields=["preco_custo", "atualizado_em"])


def _marcar_cancelado(movimento, motivo):
    movimento.cancelado = True
    movimento.cancelado_em = timezone.now()
    movimento.motivo_cancelamento = motivo[:200]
    movimento.save(update_fields=["cancelado", "cancelado_em", "motivo_cancelamento"])


# --- Operações -----------------------------------------------------------------

@transaction.atomic
def registrar_entrada(*, produto, quantidade, custo_unitario=None, data=None, observacao=""):
    """Reposição de estoque (compra do fornecedor). Atualiza o preço de custo, se informado."""
    produto = _bloquear_um(produto)
    _exigir_ativo(produto)
    quantidade = _validar_quantidade(produto, quantidade)
    data = _validar_data(data)
    if custo_unitario is not None:
        produto.preco_custo = custo_unitario
        produto.save(update_fields=["preco_custo", "atualizado_em"])
    return MovimentoEstoque.objects.create(
        produto=produto,
        tipo=ENTRADA,
        quantidade=quantidade,
        preco_unitario=custo_unitario,
        data=data,
        observacao=(observacao or "").strip(),
    )


@transaction.atomic
def preparar_itens(itens, data=None):
    """Agrupa produtos repetidos, bloqueia os produtos e confere o estoque na data da venda.

    Recebe uma lista de Item (ou de pares (produto, quantidade)) e devolve
    uma lista de Item pronta para `baixar_itens`. Deve ser chamada dentro da
    mesma transação que grava a venda.
    """
    data = _validar_data(data)
    if not itens:
        raise RegraDeNegocioError("Adicione pelo menos um produto.", "itens")
    quantidades = {}
    for item in itens:
        produto, quantidade = (item.produto, item.quantidade) if isinstance(item, Item) else item
        quantidades[produto.pk] = quantidades.get(produto.pk, Decimal(0)) + Decimal(quantidade)

    produtos = _bloquear(quantidades)
    preparados = []
    for produto_id, quantidade in quantidades.items():
        produto = produtos[produto_id]
        _exigir_ativo(produto, "itens")
        quantidade = _validar_quantidade(produto, quantidade, "itens")
        item = Item(produto, quantidade)
        if item.subtotal <= 0:
            raise RegraDeNegocioError(
                f"A quantidade de {produto.nome} é pequena demais: o valor fica abaixo de R$ 0,01.", "itens"
            )
        _exigir_disponivel(produto, quantidade, "itens", data)
        preparados.append(item)
    return preparados


@transaction.atomic
def baixar_itens(itens, *, data=None, observacao="", venda_a_prazo=None):
    """Grava a saída (venda) dos itens já conferidos por `preparar_itens`."""
    data = _validar_data(data)
    return [
        MovimentoEstoque.objects.create(
            produto=item.produto,
            tipo=VENDA,
            quantidade=-item.quantidade,
            preco_unitario=item.produto.preco_venda,
            data=data,
            observacao=(observacao or "").strip(),
            venda_a_prazo=venda_a_prazo,
        )
        for item in itens
    ]


@transaction.atomic
def registrar_venda_a_vista(itens, *, data=None, observacao=""):
    """Venda paga na hora: só baixa o estoque (não entra na caderneta)."""
    data = _validar_data(data)
    return baixar_itens(preparar_itens(itens, data=data), data=data, observacao=observacao)


@transaction.atomic
def registrar_perda(*, produto, quantidade, motivo, data=None):
    """Produto vencido, quebrado, estragado ou consumido."""
    produto = _bloquear_um(produto)
    quantidade = _validar_quantidade(produto, quantidade)
    data = _validar_data(data)
    motivo = (motivo or "").strip()
    if not motivo:
        raise RegraDeNegocioError("Informe o motivo da perda.", "observacao")
    _exigir_disponivel(produto, quantidade, "quantidade", data)
    return MovimentoEstoque.objects.create(
        produto=produto, tipo=PERDA, quantidade=-quantidade, data=data, observacao=motivo
    )


@transaction.atomic
def ajustar_estoque(*, produto, quantidade_contada, observacao=""):
    """Acerta o estoque pela contagem física. Retorna None se não houver diferença."""
    produto = _bloquear_um(produto)
    contada = Decimal(quantidade_contada)
    if contada < 0:
        raise RegraDeNegocioError("A quantidade contada não pode ser negativa.", "quantidade")
    if not produto.fracionado and contada != contada.to_integral_value():
        raise RegraDeNegocioError(
            f"{produto.nome} é controlado por {produto.sigla}: informe uma quantidade inteira.", "quantidade"
        )
    diferenca = contada.quantize(MILESIMO) - estoque_do_produto(produto.pk)
    if diferenca == 0:
        return None
    return MovimentoEstoque.objects.create(
        produto=produto,
        tipo=AJUSTE,
        quantidade=diferenca,
        observacao=(observacao or "").strip() or "Contagem de estoque",
    )


@transaction.atomic
def cancelar_movimento(movimento, motivo):
    """Cancela um movimento registrado por engano (ele continua no histórico)."""
    movimento_id = movimento.pk if isinstance(movimento, MovimentoEstoque) else movimento
    produto_id = MovimentoEstoque.objects.values_list("produto_id", flat=True).get(pk=movimento_id)
    produto = _bloquear([produto_id])[produto_id]
    movimento = MovimentoEstoque.objects.select_for_update().get(pk=movimento_id)
    motivo = (motivo or "").strip()

    if movimento.cancelado:
        raise RegraDeNegocioError("Este movimento já foi cancelado.")
    if movimento.venda_a_prazo_id:
        raise RegraDeNegocioError(
            "Este item faz parte de uma venda a prazo. Para devolvê-lo ao estoque, "
            "cancele a venda no histórico do cliente."
        )
    if not motivo:
        raise RegraDeNegocioError("Informe o motivo do cancelamento.", "motivo")
    if movimento.entra and _historico_fica_negativo(produto_id, sem_movimento=movimento.pk):
        raise RegraDeNegocioError(
            "Não é possível cancelar: parte desta quantidade já saiu do estoque e ele ficaria negativo. "
            "Se a contagem estiver errada, use o ajuste de estoque."
        )
    _marcar_cancelado(movimento, motivo)
    _recalcular_custo_apos_cancelar(produto, movimento)
    return movimento


@transaction.atomic
def devolver_itens_da_venda(venda_a_prazo, motivo):
    """Chamada ao cancelar uma venda a prazo: os produtos voltam ao estoque."""
    produto_ids = venda_a_prazo.itens.filter(cancelado=False).values_list("produto_id", flat=True)
    _bloquear(set(produto_ids))  # mesma ordem de bloqueio das outras operações (por id)
    itens = list(venda_a_prazo.itens.select_for_update().filter(cancelado=False))
    for item in itens:
        _marcar_cancelado(item, f"Venda a prazo cancelada: {motivo}")
    return itens
