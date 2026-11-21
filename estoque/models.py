from decimal import ROUND_HALF_UP, Decimal

from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Count, DecimalField, Max, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from caderneta.utils import formatar_quantidade

ZERO_QTD = Decimal("0.000")
CAMPO_QUANTIDADE = DecimalField(max_digits=14, decimal_places=3)


class ProdutoQuerySet(models.QuerySet):
    def com_estoque(self):
        """Anota em cada produto a quantidade em estoque.

        Único lugar onde a regra do estoque é definida:

            estoque = soma das quantidades dos movimentos não cancelados

        (entradas são positivas; vendas e perdas, negativas; ajustes, ambos).
        Como o saldo dos clientes, o estoque não fica gravado em uma coluna:
        é sempre calculado a partir do histórico de movimentos.
        """
        validos = Q(movimentos__cancelado=False)
        return self.annotate(
            quantidade=Coalesce(
                Sum("movimentos__quantidade", filter=validos), Value(ZERO_QTD), output_field=CAMPO_QUANTIDADE
            ),
            ultimo_movimento=Max("movimentos__data", filter=validos),
            quantidade_movimentos=Count("movimentos"),
        )


class Produto(models.Model):
    class Unidade(models.TextChoices):
        UNIDADE = "UN", "Unidade (un)"
        QUILO = "KG", "Quilo (kg)"
        LITRO = "L", "Litro (L)"
        PACOTE = "PCT", "Pacote (pct)"
        CAIXA = "CX", "Caixa (cx)"

    SIGLAS = {"UN": "un", "KG": "kg", "L": "L", "PCT": "pct", "CX": "cx"}
    UNIDADES_FRACIONADAS = {"KG", "L"}

    nome = models.CharField("nome", max_length=120)
    codigo = models.CharField("código", max_length=40, blank=True)
    unidade = models.CharField("unidade", max_length=3, choices=Unidade.choices, default=Unidade.UNIDADE)
    preco_venda = models.DecimalField(
        "preço de venda", max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))]
    )
    preco_custo = models.DecimalField(
        "preço de custo", max_digits=10, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(Decimal("0"))],
    )
    estoque_minimo = models.DecimalField(
        "estoque mínimo", max_digits=10, decimal_places=3, default=0, validators=[MinValueValidator(Decimal("0"))]
    )
    ativo = models.BooleanField("ativo", default=True)
    criado_em = models.DateTimeField("cadastrado em", auto_now_add=True)
    atualizado_em = models.DateTimeField("atualizado em", auto_now=True)

    objects = ProdutoQuerySet.as_manager()

    class Meta:
        verbose_name = "produto"
        verbose_name_plural = "produtos"
        ordering = ["nome"]
        constraints = [
            models.UniqueConstraint(
                fields=["codigo"],
                condition=~Q(codigo=""),
                name="produto_codigo_unico",
                violation_error_message="Já existe um produto com este código.",
            ),
            models.CheckConstraint(condition=Q(preco_venda__gt=0), name="produto_preco_venda_positivo"),
            models.CheckConstraint(condition=Q(estoque_minimo__gte=0), name="produto_estoque_minimo_positivo"),
        ]

    def __str__(self):
        return self.nome

    @property
    def sigla(self):
        return self.SIGLAS.get(self.unidade, self.unidade)

    @property
    def fracionado(self):
        """Quilo e litro aceitam frações (0,350 kg); as demais unidades, só inteiros."""
        return self.unidade in self.UNIDADES_FRACIONADAS

    def formatar(self, quantidade):
        return formatar_quantidade(quantidade, self.sigla, self.fracionado)


class MovimentoEstoque(models.Model):
    """Entrada ou saída de um produto.

    `quantidade` tem sinal: positiva quando o produto entra no estoque e
    negativa quando sai. Movimentos não são editados nem apagados; um
    movimento errado é cancelado (com motivo) e deixa de contar no estoque.
    """

    class Tipo(models.TextChoices):
        ENTRADA = "ENTRADA", "Entrada"
        VENDA = "VENDA", "Venda"
        PERDA = "PERDA", "Perda"
        AJUSTE = "AJUSTE", "Ajuste de contagem"

    produto = models.ForeignKey(
        Produto, verbose_name="produto", on_delete=models.PROTECT, related_name="movimentos"
    )
    tipo = models.CharField("tipo", max_length=7, choices=Tipo.choices)
    quantidade = models.DecimalField("quantidade", max_digits=12, decimal_places=3)
    preco_unitario = models.DecimalField(
        "preço unitário", max_digits=10, decimal_places=2, null=True, blank=True,
        help_text="Venda: preço praticado. Entrada: custo unitário (opcional).",
    )
    data = models.DateField("data", default=timezone.localdate)
    observacao = models.CharField("observação", max_length=200, blank=True)
    venda_a_prazo = models.ForeignKey(
        "caderneta.Movimentacao",
        verbose_name="venda a prazo",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="itens",
    )
    criado_em = models.DateTimeField("registrado em", auto_now_add=True)
    cancelado = models.BooleanField("cancelado", default=False)
    cancelado_em = models.DateTimeField("cancelado em", null=True, blank=True)
    motivo_cancelamento = models.CharField("motivo do cancelamento", max_length=200, blank=True)

    class Meta:
        verbose_name = "movimento de estoque"
        verbose_name_plural = "movimentos de estoque"
        ordering = ["data", "id"]
        indexes = [models.Index(fields=["produto", "data"], name="movest_produto_data_idx")]
        constraints = [
            # O sinal da quantidade precisa combinar com o tipo do movimento.
            models.CheckConstraint(
                condition=(
                    Q(tipo="ENTRADA", quantidade__gt=0)
                    | Q(tipo__in=["VENDA", "PERDA"], quantidade__lt=0)
                    | (Q(tipo="AJUSTE") & ~Q(quantidade=0))
                ),
                name="movimento_estoque_sinal_valido",
            ),
            # Só vendas podem estar ligadas a uma venda a prazo da caderneta.
            models.CheckConstraint(
                condition=Q(venda_a_prazo__isnull=True) | Q(tipo="VENDA"),
                name="movimento_estoque_vinculo_so_em_venda",
            ),
        ]

    def __str__(self):
        return f"{self.get_tipo_display()}: {self.descricao_item}"

    @property
    def entra(self):
        return self.quantidade > 0

    @property
    def quantidade_absoluta(self):
        return abs(self.quantidade)

    @property
    def descricao_item(self):
        """'2 un Leite integral'"""
        return f"{self.produto.formatar(self.quantidade_absoluta)} {self.produto.nome}"

    @property
    def total(self):
        if self.preco_unitario is None:
            return None
        return (self.quantidade_absoluta * self.preco_unitario).quantize(Decimal("0.01"), ROUND_HALF_UP)
