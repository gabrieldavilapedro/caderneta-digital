"""Faturas por mês: compras de cada mês e pagamentos descontados (os mais antigos primeiro)."""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from caderneta import services
from caderneta.models import Cliente

D = Decimal


def mes_anterior(primeiro_dia):
    return (primeiro_dia - timedelta(days=1)).replace(day=1)


class BaseFaturas(TestCase):
    def setUp(self):
        self.cliente = Cliente.objects.create(nome="Daniel")
        self.hoje = timezone.localdate()
        self.mes_atual = self.hoje.replace(day=1)
        self.mes_passado = mes_anterior(self.mes_atual)
        self.dois_meses = mes_anterior(self.mes_passado)

    def lancar(self, tipo, valor, data):
        return services.registrar_movimentacao(cliente=self.cliente, tipo=tipo, valor=D(valor), data=data)

    def fatura(self, mes):
        return next(f for f in services.faturas_do_cliente(self.cliente) if f.mes == mes)


class FaturasTests(BaseFaturas):
    def test_cliente_sem_compras_nao_tem_faturas(self):
        self.assertEqual(services.faturas_do_cliente(self.cliente), [])

    def test_pagamento_feito_este_mes_e_descontado_da_fatura_do_mes_passado(self):
        # o caso do Daniel: comprou R$ 263,15 em outubro, R$ 45 em novembro e pagou R$ 150 em novembro
        self.lancar("VENDA", "263.15", self.mes_passado + timedelta(days=5))
        self.lancar("VENDA", "45.00", self.mes_atual)
        pagamento = self.lancar("PAGAMENTO", "150.00", self.hoje)

        atual, passado = services.faturas_do_cliente(self.cliente)

        self.assertEqual((passado.compras, passado.pago, passado.em_aberto), (D("263.15"), D("150.00"), D("113.15")))
        self.assertTrue(passado.parcial)
        self.assertEqual(passado.pagamentos, [(pagamento, D("150.00"))])
        self.assertEqual((atual.compras, atual.pago, atual.em_aberto), (D("45.00"), D("0"), D("45.00")))
        self.assertFalse(atual.parcial)

    def test_um_pagamento_pode_ser_dividido_entre_duas_faturas(self):
        self.lancar("VENDA", "100.00", self.dois_meses)
        self.lancar("VENDA", "50.00", self.mes_passado)
        pagamento = self.lancar("PAGAMENTO", "120.00", self.mes_atual)

        atual_ou_passado = services.faturas_do_cliente(self.cliente)
        passado, retrasado = atual_ou_passado  # não há compras no mês atual: não há fatura dele

        self.assertTrue(retrasado.quitada)
        self.assertEqual(retrasado.pagamentos, [(pagamento, D("100.00"))])
        self.assertEqual(passado.em_aberto, D("30.00"))
        self.assertEqual(passado.pagamentos, [(pagamento, D("20.00"))])

    def test_soma_das_faturas_em_aberto_e_o_saldo_devedor(self):
        self.lancar("VENDA", "187.40", self.dois_meses)
        self.lancar("VENDA", "263.15", self.mes_passado)
        self.lancar("PAGAMENTO", "200.00", self.mes_passado + timedelta(days=1))
        self.lancar("VENDA", "45.00", self.mes_atual)
        faturas = services.faturas_do_cliente(self.cliente)
        self.assertEqual(sum(f.em_aberto for f in faturas), services.saldo_do_cliente(self.cliente.pk))

    def test_lancamentos_cancelados_nao_contam(self):
        self.lancar("VENDA", "30.00", self.mes_atual)
        venda_errada = self.lancar("VENDA", "99.00", self.mes_atual)
        services.cancelar_movimentacao(venda_errada, "engano")
        pagamento_errado = self.lancar("PAGAMENTO", "10.00", self.hoje)
        services.cancelar_movimentacao(pagamento_errado, "digitei errado")
        fatura = self.fatura(self.mes_atual)
        self.assertEqual((fatura.compras, fatura.pago, fatura.quantidade_compras), (D("30.00"), D("0"), 1))

    def test_fatura_do_mes_traz_as_compras_inclusive_canceladas(self):
        self.lancar("VENDA", "20.00", self.mes_atual)
        cancelada = self.lancar("VENDA", "5.00", self.mes_atual)
        services.cancelar_movimentacao(cancelada, "engano")
        fatura, compras = services.fatura_do_mes(self.cliente, self.mes_atual)
        self.assertEqual(fatura.compras, D("20.00"))
        self.assertEqual(len(compras), 2)

    def test_mes_sem_compras_nao_tem_fatura(self):
        self.lancar("VENDA", "10.00", self.mes_atual)
        self.assertEqual(services.fatura_do_mes(self.cliente, self.mes_passado), (None, []))


class HistoricoPorMesTests(BaseFaturas):
    def test_lancamentos_ficam_no_mes_em_que_aconteceram(self):
        self.lancar("VENDA", "80.00", self.mes_passado + timedelta(days=2))
        self.lancar("VENDA", "20.00", self.mes_passado + timedelta(days=9))
        self.lancar("PAGAMENTO", "30.00", self.hoje)  # pago neste mês, para a fatura do mês passado
        cancelada = self.lancar("VENDA", "99.00", self.hoje)
        services.cancelar_movimentacao(cancelada, "engano")

        atual, passado = services.historico_por_mes(self.cliente)

        self.assertEqual((atual.mes, passado.mes), (self.mes_atual, self.mes_passado))
        self.assertTrue(atual.atual)
        self.assertEqual(len(atual.movimentacoes), 2)  # o cancelado aparece no histórico...
        self.assertEqual((atual.compras, atual.pagamentos), (D("0"), D("30.00")))  # ...mas não soma
        self.assertEqual((passado.compras, passado.pagamentos), (D("100.00"), D("0")))
        self.assertEqual([m.valor for m in passado.movimentacoes], [D("20.00"), D("80.00")])  # mais recente primeiro
        self.assertEqual(passado.movimentacoes[0].saldo_apos, D("100.00"))

    def test_cliente_sem_lancamentos(self):
        self.assertEqual(services.historico_por_mes(self.cliente), [])


class FaturasTelasTests(BaseFaturas):
    def setUp(self):
        super().setUp()
        # mês passado: compras de R$ 80; R$ 30 pagos neste mês -> faltam R$ 50
        self.lancar("VENDA", "80.00", self.mes_passado)
        self.lancar("PAGAMENTO", "30.00", self.hoje)

    def test_detalhe_mostra_as_faturas(self):
        resposta = self.client.get(reverse("caderneta:cliente", args=[self.cliente.pk]))
        self.assertContains(resposta, "Faturas por mês")
        self.assertContains(resposta, "Paga em parte")
        self.assertContains(resposta, f"?mes={self.mes_passado:%Y-%m}")
        fatura = resposta.context["faturas"][0]
        self.assertEqual((fatura.pago, fatura.em_aberto), (D("30.00"), D("50.00")))

    def test_detalhe_mostra_o_historico_separado_por_mes(self):
        resposta = self.client.get(reverse("caderneta:cliente", args=[self.cliente.pk]))
        grupos = resposta.context["historico_meses"]
        self.assertEqual([g.mes for g in grupos], [self.mes_atual, self.mes_passado])
        conteudo = resposta.content.decode()
        self.assertEqual(conteudo.count('class="historico-mes"'), 2)
        self.assertEqual(conteudo.count('class="historico-mes" open'), 1)  # só o mais recente aberto

    def test_fatura_quitada_aparece_na_tela(self):
        self.lancar("PAGAMENTO", "50.00", self.hoje)
        resposta = self.client.get(reverse("caderneta:cliente", args=[self.cliente.pk]))
        self.assertContains(resposta, "Quitada")
        resposta = self.client.get(
            reverse("caderneta:cliente_extrato", args=[self.cliente.pk]), {"mes": f"{self.mes_passado:%Y-%m}"}
        )
        self.assertContains(resposta, "Fatura quitada")

    def test_fatura_do_mes_mostra_compras_e_pagamentos_descontados(self):
        resposta = self.client.get(
            reverse("caderneta:cliente_extrato", args=[self.cliente.pk]), {"mes": f"{self.mes_passado:%Y-%m}"}
        )
        self.assertContains(resposta, "Compras do mês")
        self.assertContains(resposta, "Pagamentos descontados desta fatura")
        self.assertEqual(resposta.context["fatura"].em_aberto, D("50.00"))
        self.assertEqual(len(resposta.context["compras"]), 1)

    def test_extrato_de_mes_sem_fatura_volta_para_o_completo(self):
        url = reverse("caderneta:cliente_extrato", args=[self.cliente.pk])
        self.assertRedirects(self.client.get(url, {"mes": "2001-01"}), url)
        resposta = self.client.get(url, {"mes": "abc"})
        self.assertEqual(resposta.status_code, 200)
        self.assertNotIn("fatura", resposta.context)
        self.assertEqual(resposta.context["saldo_final"], D("50.00"))

    def test_pagamento_sem_valor_nao_fala_em_produtos(self):
        resposta = self.client.post(
            reverse("caderneta:pagamento", args=[self.cliente.pk]), {"valor": "", "data": f"{self.hoje:%Y-%m-%d}"}
        )
        self.assertEqual(resposta.context["form"].errors["valor"], ["Informe o valor."])

    def test_tela_de_pagamento_lista_as_faturas_em_aberto(self):
        self.lancar("VENDA", "45.00", self.mes_atual)
        url = reverse("caderneta:pagamento", args=[self.cliente.pk])
        opcoes = self.client.get(url).context["opcoes_pagamento"]
        self.assertEqual(
            [(o.fatura.mes, o.fatura.em_aberto, o.acumulado) for o in opcoes],
            [(self.mes_passado, D("50.00"), D("50.00")), (self.mes_atual, D("45.00"), D("95.00"))],
        )

        resposta = self.client.get(url, {"ate": f"{self.mes_passado:%Y-%m}"})
        self.assertEqual(resposta.context["form"].initial["valor"], "50,00")
        self.assertIn("Pagamento da fatura de", resposta.context["form"].initial["descricao"])

        self.client.post(url, {"valor": "50,00", "data": f"{self.hoje:%Y-%m-%d}", "descricao": "Fatura"})
        self.assertTrue(self.fatura(self.mes_passado).quitada)
        self.assertEqual(self.fatura(self.mes_atual).em_aberto, D("45.00"))

    def test_pagar_so_uma_parte_da_fatura_passada(self):
        url = reverse("caderneta:pagamento", args=[self.cliente.pk])
        self.assertContains(self.client.get(url), 'data-em-aberto-centavos="5000"')
        self.client.post(url, {"valor": "20,00", "data": f"{self.hoje:%Y-%m-%d}", "descricao": ""})
        fatura = self.fatura(self.mes_passado)
        self.assertTrue(fatura.parcial)
        self.assertEqual((fatura.pago, fatura.em_aberto), (D("50.00"), D("30.00")))
