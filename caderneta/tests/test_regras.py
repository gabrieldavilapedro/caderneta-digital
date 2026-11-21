"""Testes das regras de negócio (caderneta/services.py e cálculo do saldo)."""
from datetime import timedelta
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from caderneta import services
from caderneta.models import Cliente, Movimentacao
from caderneta.services import RegraDeNegocioError
from caderneta.utils import formatar_moeda, ler_valor

VENDA = Movimentacao.Tipo.VENDA
PAGAMENTO = Movimentacao.Tipo.PAGAMENTO


class BaseRegras(TestCase):
    def setUp(self):
        self.cliente = Cliente.objects.create(nome="Maria da Silva")

    def venda(self, valor, cliente=None, **extra):
        return services.registrar_movimentacao(
            cliente=cliente or self.cliente, tipo=VENDA, valor=Decimal(valor), **extra
        )

    def pagamento(self, valor, cliente=None, **extra):
        return services.registrar_movimentacao(
            cliente=cliente or self.cliente, tipo=PAGAMENTO, valor=Decimal(valor), **extra
        )

    def saldo(self, cliente=None):
        return services.saldo_do_cliente((cliente or self.cliente).pk)


class SaldoTests(BaseRegras):
    """RF006: saldo = vendas a prazo - pagamentos."""

    def test_cliente_sem_movimentacoes_tem_saldo_zero(self):
        self.assertEqual(self.saldo(), Decimal("0.00"))

    def test_vendas_aumentam_o_saldo(self):
        self.venda("25.50")
        self.venda("10.00")
        self.assertEqual(self.saldo(), Decimal("35.50"))

    def test_pagamento_parcial_reduz_o_saldo(self):
        self.venda("100.00")
        self.pagamento("30.25")
        self.assertEqual(self.saldo(), Decimal("69.75"))

    def test_pagamento_total_quita_o_saldo(self):
        self.venda("50.00")
        self.pagamento("50.00")
        self.assertEqual(self.saldo(), Decimal("0.00"))

    def test_saldo_de_um_cliente_nao_interfere_no_de_outro(self):
        joao = Cliente.objects.create(nome="João Pereira")
        self.venda("40.00")
        self.venda("15.00", cliente=joao)
        self.pagamento("5.00", cliente=joao)
        self.assertEqual(self.saldo(), Decimal("40.00"))
        self.assertEqual(self.saldo(joao), Decimal("10.00"))

    def test_totais_nao_sao_duplicados_com_varios_lancamentos(self):
        # Garante que somar vendas e pagamentos na mesma consulta não multiplica valores.
        for _ in range(3):
            self.venda("10.00")
        for _ in range(2):
            self.pagamento("4.00")
        cliente = Cliente.objects.com_saldo().get(pk=self.cliente.pk)
        self.assertEqual(cliente.total_vendas, Decimal("30.00"))
        self.assertEqual(cliente.total_pagamentos, Decimal("8.00"))
        self.assertEqual(cliente.saldo, Decimal("22.00"))
        self.assertEqual(cliente.quantidade_movimentacoes, 5)


class RegistroTests(BaseRegras):
    """RF002/RF003: validações ao registrar vendas e pagamentos."""

    def test_pagamento_maior_que_o_saldo_e_recusado(self):
        self.venda("20.00")
        with self.assertRaises(RegraDeNegocioError) as contexto:
            self.pagamento("20.01")
        self.assertEqual(contexto.exception.campo, "valor")
        self.assertIn("maior que o saldo devedor", contexto.exception.mensagem)
        self.assertEqual(self.saldo(), Decimal("20.00"))
        self.assertFalse(Movimentacao.objects.filter(tipo=PAGAMENTO).exists())

    def test_pagamento_sem_saldo_devedor_e_recusado(self):
        with self.assertRaises(RegraDeNegocioError) as contexto:
            self.pagamento("5.00")
        self.assertEqual(contexto.exception.campo, "cliente")

    def test_valor_zero_ou_negativo_e_recusado(self):
        for valor in ("0", "-5"):
            with self.subTest(valor=valor), self.assertRaises(RegraDeNegocioError):
                self.venda(valor)
        self.assertEqual(Movimentacao.objects.count(), 0)

    def test_data_futura_e_recusada(self):
        amanha = timezone.localdate() + timedelta(days=1)
        with self.assertRaises(RegraDeNegocioError) as contexto:
            self.venda("10.00", data=amanha)
        self.assertEqual(contexto.exception.campo, "data")

    def test_data_retroativa_e_aceita(self):
        semana_passada = timezone.localdate() - timedelta(days=7)
        movimentacao = self.venda("10.00", data=semana_passada)
        self.assertEqual(movimentacao.data, semana_passada)

    def test_cliente_inativo_nao_recebe_vendas_mas_pode_pagar(self):
        self.venda("30.00")
        self.cliente.ativo = False
        self.cliente.save()
        with self.assertRaises(RegraDeNegocioError):
            self.venda("10.00")
        self.pagamento("30.00")
        self.assertEqual(self.saldo(), Decimal("0.00"))

    def test_descricao_e_gravada_sem_espacos_extras(self):
        movimentacao = self.venda("12.00", descricao="  pão e leite  ")
        self.assertEqual(movimentacao.descricao, "pão e leite")


class CancelamentoTests(BaseRegras):
    """Correção de lançamentos registrados por engano (estorno)."""

    def test_lancamento_cancelado_deixa_de_contar_no_saldo(self):
        self.venda("40.00")
        errada = self.venda("10.00")
        services.cancelar_movimentacao(errada, "valor digitado errado")
        errada.refresh_from_db()
        self.assertTrue(errada.cancelada)
        self.assertIsNotNone(errada.cancelada_em)
        self.assertEqual(errada.motivo_cancelamento, "valor digitado errado")
        self.assertEqual(self.saldo(), Decimal("40.00"))

    def test_cancelar_pagamento_restaura_o_saldo(self):
        self.venda("60.00")
        pagamento = self.pagamento("20.00")
        services.cancelar_movimentacao(pagamento, "pagamento em duplicidade")
        self.assertEqual(self.saldo(), Decimal("60.00"))

    def test_nao_cancela_venda_se_o_saldo_ficaria_negativo(self):
        venda = self.venda("30.00")
        self.pagamento("30.00")
        with self.assertRaises(RegraDeNegocioError):
            services.cancelar_movimentacao(venda, "teste")
        venda.refresh_from_db()
        self.assertFalse(venda.cancelada)

    def test_cancelamento_exige_motivo_e_nao_pode_ser_repetido(self):
        venda = self.venda("10.00")
        with self.assertRaises(RegraDeNegocioError):
            services.cancelar_movimentacao(venda, "   ")
        services.cancelar_movimentacao(venda, "engano")
        with self.assertRaises(RegraDeNegocioError):
            services.cancelar_movimentacao(venda, "engano de novo")


class HistoricoTests(BaseRegras):
    """RF005: histórico com saldo acumulado após cada lançamento."""

    def test_historico_traz_saldo_acumulado_do_mais_recente_ao_mais_antigo(self):
        hoje = timezone.localdate()
        self.venda("10.00", data=hoje - timedelta(days=2))
        self.pagamento("4.00", data=hoje - timedelta(days=1))
        cancelada = self.venda("99.00", data=hoje)
        services.cancelar_movimentacao(cancelada, "engano")
        self.venda("5.00", data=hoje)

        historico = services.historico_do_cliente(self.cliente)

        self.assertEqual(len(historico), 4)
        self.assertEqual(historico[0].valor, Decimal("5.00"))
        self.assertEqual(historico[0].saldo_apos, Decimal("11.00"))
        self.assertIsNone(historico[1].saldo_apos)  # cancelada
        self.assertEqual(historico[2].saldo_apos, Decimal("6.00"))
        self.assertEqual(historico[3].saldo_apos, Decimal("10.00"))


class ResumoTests(BaseRegras):
    def test_resumo_totaliza_valores_a_receber(self):
        joao = Cliente.objects.create(nome="João")
        self.venda("50.00")
        self.pagamento("20.00")
        self.venda("15.00", cliente=joao)
        Cliente.objects.create(nome="Ana")  # sem débito

        resumo = services.resumo_geral()

        self.assertEqual(resumo["total_a_receber"], Decimal("45.00"))
        self.assertEqual(resumo["clientes_com_debito"], 2)
        self.assertEqual(resumo["clientes_ativos"], 3)
        self.assertEqual(resumo["vendas_mes"], Decimal("65.00"))
        self.assertEqual(resumo["pagamentos_mes"], Decimal("20.00"))

    def test_cobranca_prioriza_quem_esta_ha_mais_tempo_sem_pagar(self):
        hoje = timezone.localdate()
        antigo = Cliente.objects.create(nome="Antigo")
        self.venda("10.00", cliente=antigo, data=hoje - timedelta(days=40))
        self.venda("10.00", data=hoje - timedelta(days=5))

        cobrancas = services.clientes_para_cobranca()

        self.assertEqual([c.nome for c in cobrancas], ["Antigo", "Maria da Silva"])
        self.assertEqual(cobrancas[0].dias_sem_pagar, 40)


class IntegridadeNoBancoTests(BaseRegras):
    """RNF002: o próprio PostgreSQL recusa dados inválidos."""

    def test_banco_recusa_valor_nao_positivo(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Movimentacao.objects.create(cliente=self.cliente, tipo=VENDA, valor=Decimal("-1.00"))

    def test_banco_recusa_tipo_invalido(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Movimentacao.objects.create(cliente=self.cliente, tipo="OUTRO", valor=Decimal("1.00"))

    def test_cliente_com_lancamentos_nao_pode_ser_apagado(self):
        from django.db.models import ProtectedError

        self.venda("10.00")
        with self.assertRaises(ProtectedError):
            self.cliente.delete()


class UtilitariosTests(TestCase):
    def test_ler_valor_aceita_formatos_brasileiros(self):
        casos = {
            "12,50": "12.50",
            "12.50": "12.50",
            "1.234,56": "1234.56",
            "1.234": "1234.00",
            "R$ 7,5": "7.50",
            "0,99": "0.99",
        }
        for texto, esperado in casos.items():
            with self.subTest(texto=texto):
                self.assertEqual(ler_valor(texto), Decimal(esperado))

    def test_ler_valor_recusa_formatos_invalidos(self):
        for texto in ("", "abc", "12,555", "1,234.56", "12..5"):
            with self.subTest(texto=texto), self.assertRaises(ValueError):
                ler_valor(texto)

    def test_formatar_moeda(self):
        self.assertEqual(formatar_moeda(Decimal("1234.5")), "R$ 1.234,50")
        self.assertEqual(formatar_moeda(Decimal("0")), "R$ 0,00")
