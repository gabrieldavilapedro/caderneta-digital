from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Count, DecimalField, F, Max, Min, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

ZERO = Decimal("0.00")
CAMPO_DINHEIRO = DecimalField(max_digits=14, decimal_places=2)


class ClienteQuerySet(models.QuerySet):
    def com_saldo(self):
        """Anota em cada cliente os totais e o saldo devedor.

        Este é o ÚNICO lugar onde a regra do saldo é definida (RF006/RNF002):

            saldo = soma das vendas a prazo - soma dos pagamentos

        Lançamentos cancelados não entram no cálculo. O saldo é sempre
        calculado a partir das movimentações gravadas no PostgreSQL (não fica
        armazenado em uma coluna), então nunca diverge do histórico.

        Todas as somas usam a mesma relação (um único JOIN), por isso os
        valores não são duplicados.
        """
        validas = Q(movimentacoes__cancelada=False)
        vendas = validas & Q(movimentacoes__tipo=Movimentacao.Tipo.VENDA)
        pagamentos = validas & Q(movimentacoes__tipo=Movimentacao.Tipo.PAGAMENTO)
        return self.annotate(
            total_vendas=Coalesce(
                Sum("movimentacoes__valor", filter=vendas), Value(ZERO), output_field=CAMPO_DINHEIRO
            ),
            total_pagamentos=Coalesce(
                Sum("movimentacoes__valor", filter=pagamentos), Value(ZERO), output_field=CAMPO_DINHEIRO
            ),
            ultimo_pagamento=Max("movimentacoes__data", filter=pagamentos),
            primeira_venda=Min("movimentacoes__data", filter=vendas),
            ultima_movimentacao=Max("movimentacoes__data", filter=validas),
            quantidade_movimentacoes=Count("movimentacoes"),
        ).annotate(
            saldo=F("total_vendas") - F("total_pagamentos"),
        )


class Cliente(models.Model):
    """Cliente que compra a prazo (fiado).

    Coleta apenas os dados mínimos para identificação e contato (LGPD,
    princípio da necessidade).
    """

    nome = models.CharField("nome", max_length=120)
    telefone = models.CharField(
        "telefone", max_length=11, blank=True, help_text="Somente números, com DDD."
    )
    observacoes = models.TextField("observações", max_length=500, blank=True)
    ativo = models.BooleanField("ativo", default=True)
    criado_em = models.DateTimeField("cadastrado em", auto_now_add=True)
    atualizado_em = models.DateTimeField("atualizado em", auto_now=True)

    objects = ClienteQuerySet.as_manager()

    class Meta:
        verbose_name = "cliente"
        verbose_name_plural = "clientes"
        ordering = ["nome"]

    def __str__(self):
        return self.nome


class Movimentacao(models.Model):
    """Lançamento da caderneta: uma venda a prazo ou um pagamento.

    Lançamentos não são editados nem apagados. Um lançamento errado é
    CANCELADO (com motivo), continua visível no histórico e deixa de contar
    no saldo. Isso preserva a rastreabilidade das movimentações.
    """

    class Tipo(models.TextChoices):
        VENDA = "VENDA", "Venda a prazo"
        PAGAMENTO = "PAGAMENTO", "Pagamento"

    cliente = models.ForeignKey(
        Cliente, verbose_name="cliente", on_delete=models.PROTECT, related_name="movimentacoes"
    )
    tipo = models.CharField("tipo", max_length=10, choices=Tipo.choices)
    valor = models.DecimalField(
        "valor", max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))]
    )
    data = models.DateField("data", default=timezone.localdate)
    descricao = models.CharField("descrição", max_length=200, blank=True)
    criado_em = models.DateTimeField("registrado em", auto_now_add=True)
    cancelada = models.BooleanField("cancelada", default=False)
    cancelada_em = models.DateTimeField("cancelada em", null=True, blank=True)
    motivo_cancelamento = models.CharField("motivo do cancelamento", max_length=200, blank=True)

    class Meta:
        verbose_name = "movimentação"
        verbose_name_plural = "movimentações"
        ordering = ["data", "id"]
        indexes = [models.Index(fields=["cliente", "data"], name="mov_cliente_data_idx")]
        constraints = [
            # Garantias de integridade no próprio banco (RNF002), além das validações da aplicação.
            models.CheckConstraint(condition=Q(valor__gt=0), name="movimentacao_valor_positivo"),
            models.CheckConstraint(
                condition=Q(tipo__in=["VENDA", "PAGAMENTO"]), name="movimentacao_tipo_valido"
            ),
        ]

    def __str__(self):
        return f"{self.get_tipo_display()} de {self.valor} — {self.cliente}"

    @property
    def eh_venda(self):
        return self.tipo == self.Tipo.VENDA

    @property
    def itens_texto(self):
        """Produtos do estoque vendidos neste lançamento: '2 un Leite, 1 un Pão'."""
        return ", ".join(item.descricao_item for item in self.itens.all())

    @property
    def descricao_completa(self):
        """Descrição digitada ou, se vazia, a lista de produtos."""
        return self.descricao or self.itens_texto
