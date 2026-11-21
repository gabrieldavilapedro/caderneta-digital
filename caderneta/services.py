"""Regras de negócio da caderneta.

Toda gravação de venda, pagamento ou cancelamento passa por estas funções.
Elas rodam dentro de uma transação e bloqueiam o registro do cliente
(SELECT ... FOR UPDATE), de modo que o saldo nunca fique inconsistente, mesmo
que duas abas registrem lançamentos ao mesmo tempo (RNF002 – Integridade).

As views apenas chamam estas funções; assim as regras ficam em um só lugar
e podem ser testadas isoladamente (RNF008 e RNF010).
"""
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q, Sum, Value
from django.db.models.functions import Coalesce, TruncMonth
from django.utils import timezone

from estoque import services as estoque_services

from .excecoes import RegraDeNegocioError
from .models import CAMPO_DINHEIRO, ZERO, Cliente, Movimentacao
from .utils import CENTAVO, formatar_moeda

__all__ = ["RegraDeNegocioError"]  # mantém "from caderneta.services import RegraDeNegocioError"

VENDA = Movimentacao.Tipo.VENDA
PAGAMENTO = Movimentacao.Tipo.PAGAMENTO
# Movimentacao.valor tem 10 dígitos (8 inteiros): o maior valor gravável é 99.999.999,99.
LIMITE_VALOR = Decimal("100000000")


MESES = [
    "janeiro", "fevereiro", "março", "abril", "maio", "junho",
    "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
]


@dataclass
class Fatura:
    """A fatura de um cliente em um mês: as compras a prazo daquele mês e o que já foi pago delas.

    Os pagamentos são descontados das faturas mais antigas primeiro, não importa
    em que mês foram feitos: um pagamento feito em novembro pode quitar outubro.
    """

    mes: date  # primeiro dia do mês
    compras: Decimal
    quantidade_compras: int
    pago: Decimal = ZERO
    # Pagamentos descontados desta fatura: [(pagamento, valor descontado)].
    # Um pagamento pode ser dividido entre duas faturas.
    pagamentos: list = field(default_factory=list)

    @property
    def em_aberto(self):
        return self.compras - self.pago

    @property
    def quitada(self):
        return self.em_aberto <= 0

    @property
    def parcial(self):
        return self.pago > 0 and self.em_aberto > 0

    @property
    def atual(self):
        return self.mes == timezone.localdate().replace(day=1)

    @property
    def nome(self):
        """'outubro/2026'"""
        return f"{MESES[self.mes.month - 1]}/{self.mes.year}"

    @property
    def parametro(self):
        """Valor do parâmetro ?mes= da URL do extrato: '2026-11'."""
        return f"{self.mes:%Y-%m}"


@dataclass
class OpcaoPagamento:
    """Uma escolha da tela de pagamento: pagar até a fatura de `fatura.mes`."""

    fatura: Fatura
    acumulado: Decimal  # em aberto desta fatura + faturas anteriores ainda em aberto
    descricao: str

    @property
    def parametro(self):
        return self.fatura.parametro

    @property
    def valor_campo(self):
        return f"{self.acumulado:.2f}".replace(".", ",")

    @property
    def em_aberto_centavos(self):
        return int(self.fatura.em_aberto * 100)


@dataclass
class HistoricoDoMes:
    """Lançamentos de um mês do histórico, pela data em que aconteceram."""

    mes: date  # primeiro dia do mês
    movimentacoes: list = field(default_factory=list)  # do mais recente para o mais antigo

    @property
    def compras(self):
        return sum((m.valor for m in self.movimentacoes if m.eh_venda and not m.cancelada), ZERO)

    @property
    def pagamentos(self):
        return sum((m.valor for m in self.movimentacoes if not m.eh_venda and not m.cancelada), ZERO)

    @property
    def atual(self):
        return self.mes == timezone.localdate().replace(day=1)


def proximo_mes(dia):
    return (dia.replace(day=28) + timedelta(days=4)).replace(day=1)


# --- Consultas ---------------------------------------------------------------

def saldo_do_cliente(cliente_id):
    return Cliente.objects.com_saldo().values_list("saldo", flat=True).get(pk=cliente_id)


def historico_do_cliente(cliente):
    """Lançamentos do cliente, do mais recente para o mais antigo.

    Cada item recebe `saldo_apos`: o saldo acumulado logo após aquele
    lançamento, como em uma caderneta de papel. Lançamentos cancelados ficam
    na lista, mas não alteram o saldo (saldo_apos = None).
    """
    movimentacoes = list(cliente.movimentacoes.prefetch_related("itens__produto").order_by("data", "id"))
    saldo = ZERO
    for movimentacao in movimentacoes:
        if movimentacao.cancelada:
            movimentacao.saldo_apos = None
            continue
        saldo += movimentacao.valor if movimentacao.eh_venda else -movimentacao.valor
        movimentacao.saldo_apos = saldo
    movimentacoes.reverse()
    return movimentacoes


def historico_por_mes(cliente):
    """Histórico do cliente separado por mês (do mais recente para o mais antigo).

    Cada lançamento fica no mês da sua data e mantém o `saldo_apos`. Lançamentos
    cancelados aparecem no histórico, mas não entram nos totais do mês.
    """
    grupos = []
    for movimentacao in historico_do_cliente(cliente):
        mes = movimentacao.data.replace(day=1)
        if not grupos or grupos[-1].mes != mes:
            grupos.append(HistoricoDoMes(mes=mes))
        grupos[-1].movimentacoes.append(movimentacao)
    return grupos


def faturas_do_cliente(cliente):
    """Faturas do cliente (meses com compras a prazo), da mais recente para a mais antiga.

    - Fatura de um mês = compras a prazo daquele mês (pela data da compra);
      lançamentos cancelados não contam.
    - Cada pagamento, em ordem de data, é descontado da fatura mais antiga ainda
      em aberto; o que sobrar passa para a seguinte. Assim, um pagamento feito em
      novembro "para outubro" aparece descontado na fatura de outubro.
    """
    compras_por_mes = (
        cliente.movimentacoes.filter(tipo=VENDA, cancelada=False)
        .annotate(mes=TruncMonth("data"))
        .values("mes")
        .annotate(total=Sum("valor"), quantidade=Count("id"))
        .order_by("mes")
    )
    faturas = [
        Fatura(mes=linha["mes"], compras=linha["total"], quantidade_compras=linha["quantidade"])
        for linha in compras_por_mes
    ]

    indice = 0  # fatura mais antiga ainda em aberto
    for pagamento in cliente.movimentacoes.filter(tipo=PAGAMENTO, cancelada=False).order_by("data", "id"):
        restante = pagamento.valor
        while restante > 0 and indice < len(faturas):
            fatura = faturas[indice]
            desconto = min(restante, fatura.em_aberto)
            fatura.pago += desconto
            fatura.pagamentos.append((pagamento, desconto))
            restante -= desconto
            if fatura.quitada:
                indice += 1

    faturas.reverse()
    return faturas


def meses_em_aberto(cliente):
    """Opções da tela de pagamento: faturas em aberto, da mais antiga para a mais recente.

    Como as faturas mais antigas são pagas primeiro, pagar um mês inclui os
    anteriores ainda em aberto (`acumulado`). A última opção quita todo o saldo.
    """
    abertas = [fatura for fatura in reversed(faturas_do_cliente(cliente)) if fatura.em_aberto > 0]
    opcoes = []
    acumulado = ZERO
    for indice, fatura in enumerate(abertas):
        acumulado += fatura.em_aberto
        if indice == 0:
            descricao = f"Pagamento da fatura de {fatura.nome}"
        else:
            descricao = f"Pagamento das faturas de {abertas[0].nome} a {fatura.nome}"
        opcoes.append(OpcaoPagamento(fatura=fatura, acumulado=acumulado, descricao=descricao))
    return opcoes


def fatura_do_mes(cliente, mes):
    """A fatura de um mês e as compras dela em ordem de data (inclui as canceladas, para o histórico).

    Retorna (fatura, compras) ou (None, []) se o cliente não comprou a prazo naquele mês.
    """
    fatura = next((f for f in faturas_do_cliente(cliente) if f.mes == mes), None)
    if fatura is None:
        return None, []
    compras = list(
        cliente.movimentacoes.filter(tipo=VENDA, data__gte=mes, data__lt=proximo_mes(mes))
        .prefetch_related("itens__produto")
        .order_by("data", "id")
    )
    return fatura, compras


def clientes_para_cobranca(limite=5):
    """Clientes com débito, começando por quem está há mais tempo sem pagar.

    Referência: data do último pagamento ou, se nunca pagou, da primeira compra.
    """
    hoje = timezone.localdate()
    clientes = list(
        Cliente.objects.com_saldo()
        .filter(saldo__gt=0)
        .annotate(referencia_cobranca=Coalesce("ultimo_pagamento", "primeira_venda"))
        .order_by("referencia_cobranca", "-saldo")[:limite]
    )
    for cliente in clientes:
        referencia = cliente.referencia_cobranca
        cliente.dias_sem_pagar = (hoje - referencia).days if referencia else None
    return clientes


def resumo_geral():
    """Indicadores da tela inicial."""
    hoje = timezone.localdate()
    inicio_do_mes = hoje.replace(day=1)

    def soma(**filtro):
        return Coalesce(Sum("valor", filter=Q(**filtro)), Value(ZERO), output_field=CAMPO_DINHEIRO)

    totais = Movimentacao.objects.filter(cancelada=False).aggregate(
        vendas=soma(tipo=VENDA),
        pagamentos=soma(tipo=PAGAMENTO),
        vendas_mes=soma(tipo=VENDA, data__gte=inicio_do_mes),
        pagamentos_mes=soma(tipo=PAGAMENTO, data__gte=inicio_do_mes),
    )
    return {
        "hoje": hoje,
        "total_a_receber": totais["vendas"] - totais["pagamentos"],
        "vendas_mes": totais["vendas_mes"],
        "pagamentos_mes": totais["pagamentos_mes"],
        "clientes_com_debito": Cliente.objects.com_saldo().filter(saldo__gt=0).count(),
        "clientes_ativos": Cliente.objects.filter(ativo=True).count(),
        "cobrancas": clientes_para_cobranca(),
        "ultimos_lancamentos": (
            Movimentacao.objects.select_related("cliente")
            .prefetch_related("itens__produto")
            .order_by("-criado_em", "-id")[:8]
        ),
        "estoque_baixo": list(estoque_services.produtos_com_estoque_baixo(limite=5)),
        "total_estoque_baixo": estoque_services.produtos_com_estoque_baixo().count(),
    }


def paginar(itens, pagina, por_pagina=25):
    return Paginator(itens, por_pagina).get_page(pagina)


# --- Operações (escrita) -------------------------------------------------------

def _bloquear_cliente(cliente_id):
    return Cliente.objects.select_for_update().get(pk=cliente_id)


@transaction.atomic
def registrar_movimentacao(*, cliente, tipo, valor=None, data=None, descricao="", itens=None):
    """Registra uma venda a prazo (RF002) ou um pagamento (RF003).

    Regras:
    - valor maior que zero, com no máximo 2 casas decimais;
    - data não pode estar no futuro;
    - cliente inativo não recebe novas vendas (mas pode pagar o que deve);
    - pagamento não pode ser maior que o saldo devedor (decisão de negócio:
      a caderneta não guarda "crédito" a favor do cliente).

    Venda com produtos do estoque (opcional): `itens` é uma lista de
    estoque.services.Item. O valor passa a ser o total dos produtos e o
    estoque é baixado na MESMA transação: se faltar estoque de qualquer
    item, nada é gravado.
    """
    cliente = _bloquear_cliente(cliente.pk if isinstance(cliente, Cliente) else cliente)
    hoje = timezone.localdate()
    data = data or hoje
    itens = list(itens or [])

    if tipo not in (VENDA, PAGAMENTO):
        raise RegraDeNegocioError("Tipo de lançamento inválido.", "tipo")
    if itens and tipo != VENDA:
        raise RegraDeNegocioError("Produtos do estoque só podem ser informados em vendas.", "itens")
    if data > hoje:
        raise RegraDeNegocioError("A data não pode estar no futuro.", "data")
    if tipo == VENDA and not cliente.ativo:
        raise RegraDeNegocioError(
            "Este cliente está inativo. Reative o cadastro para registrar novas vendas.", "cliente"
        )

    if itens:
        # bloqueia os produtos e confere o estoque na data da venda
        itens = estoque_services.preparar_itens(itens, data=data)
        valor = estoque_services.total_dos_itens(itens)
    if valor in (None, ""):
        raise RegraDeNegocioError("Informe o valor.", "valor")
    valor = Decimal(valor).quantize(CENTAVO)
    if valor <= ZERO:
        raise RegraDeNegocioError("Informe um valor maior que zero.", "valor")
    if valor >= LIMITE_VALOR:
        raise RegraDeNegocioError(
            f"Valor muito alto ({formatar_moeda(valor)}). Confira as quantidades e os preços.",
            "itens" if itens else "valor",
        )

    if tipo == PAGAMENTO:
        saldo = saldo_do_cliente(cliente.pk)
        if saldo <= ZERO:
            raise RegraDeNegocioError("Este cliente não possui saldo devedor.", "cliente")
        if valor > saldo:
            raise RegraDeNegocioError(
                f"O pagamento ({formatar_moeda(valor)}) é maior que o saldo devedor "
                f"({formatar_moeda(saldo)}).",
                "valor",
            )

    movimentacao = Movimentacao.objects.create(
        cliente=cliente,
        tipo=tipo,
        valor=valor,
        data=data,
        descricao=(descricao or "").strip(),
    )
    if itens:
        estoque_services.baixar_itens(itens, data=data, venda_a_prazo=movimentacao)
    return movimentacao


@transaction.atomic
def cancelar_movimentacao(movimentacao, motivo):
    """Cancela (estorna) um lançamento registrado por engano.

    O lançamento continua no histórico, marcado como cancelado, e deixa de
    contar no saldo. Não é permitido cancelar uma venda se isso deixar os
    pagamentos maiores que as compras (saldo negativo). Se a venda tinha
    produtos do estoque, eles voltam ao estoque.
    """
    movimentacao_id = movimentacao.pk if isinstance(movimentacao, Movimentacao) else movimentacao
    cliente_id = Movimentacao.objects.values_list("cliente_id", flat=True).get(pk=movimentacao_id)
    _bloquear_cliente(cliente_id)
    movimentacao = Movimentacao.objects.select_for_update().get(pk=movimentacao_id)
    motivo = (motivo or "").strip()

    if movimentacao.cancelada:
        raise RegraDeNegocioError("Este lançamento já foi cancelado.")
    if not motivo:
        raise RegraDeNegocioError("Informe o motivo do cancelamento.", "motivo")
    if movimentacao.eh_venda and saldo_do_cliente(cliente_id) - movimentacao.valor < ZERO:
        raise RegraDeNegocioError(
            "Não é possível cancelar esta venda: os pagamentos já registrados ficariam maiores "
            "que as compras. Cancele antes o pagamento correspondente."
        )

    movimentacao.cancelada = True
    movimentacao.cancelada_em = timezone.now()
    movimentacao.motivo_cancelamento = motivo[:200]
    movimentacao.save(update_fields=["cancelada", "cancelada_em", "motivo_cancelamento"])
    if movimentacao.eh_venda:
        estoque_services.devolver_itens_da_venda(movimentacao, motivo)
    return movimentacao
