"""Testes das telas (views): fluxo completo como o comerciante usaria."""
from decimal import Decimal

from django.conf import settings
from django.test import TestCase
from django.urls import reverse

from caderneta import services
from caderneta.models import Cliente, Movimentacao

VENDA = Movimentacao.Tipo.VENDA


class TelaTestCase(TestCase):
    """As telas são usadas sem login: nenhum teste entra com usuário."""

    def criar_cliente(self, nome="Maria da Silva", **extra):
        return Cliente.objects.create(nome=nome, **extra)


class AcessoTests(TelaTestCase):
    def test_telas_abrem_sem_login(self):
        cliente = self.criar_cliente()
        telas = [
            reverse("caderneta:painel"),
            reverse("caderneta:clientes"),
            reverse("caderneta:cliente_novo"),
            reverse("caderneta:cliente", args=[cliente.pk]),
            reverse("caderneta:cliente_extrato", args=[cliente.pk]),
            reverse("caderneta:escolher_venda"),
            reverse("caderneta:escolher_pagamento"),
            reverse("caderneta:venda", args=[cliente.pk]),
            reverse("estoque:produtos"),
        ]
        for endereco in telas:
            with self.subTest(tela=endereco):
                self.assertEqual(self.client.get(endereco).status_code, 200)

    def test_enderecos_antigos_de_login_levam_ao_painel(self):
        for endereco in ("/entrar/", "/primeiro-acesso/", "/sair/"):
            with self.subTest(endereco=endereco):
                self.assertRedirects(self.client.get(endereco), reverse("caderneta:painel"))

    def test_menu_nao_tem_mais_botao_sair(self):
        resposta = self.client.get(reverse("caderneta:painel"))
        self.assertContains(resposta, "Receber pagamento")
        self.assertNotContains(resposta, "Sair")

    def test_sistema_so_atende_no_proprio_computador(self):
        # Sem login, a proteção é o sistema não responder a outros aparelhos da rede.
        self.assertNotIn("*", settings.ALLOWED_HOSTS)
        resposta = self.client.get(reverse("caderneta:painel"), HTTP_HOST="192.168.0.10:8000")
        self.assertEqual(resposta.status_code, 400)
        resposta = self.client.get(reverse("caderneta:painel"), HTTP_HOST="127.0.0.1:8000")
        self.assertEqual(resposta.status_code, 200)


class ClienteTelasTests(TelaTestCase):
    def test_cadastra_cliente_normalizando_nome_e_telefone(self):
        resposta = self.client.post(
            reverse("caderneta:cliente_novo"),
            {"nome": "  João   Pereira ", "telefone": "(48) 99999-1234", "observacoes": ""},
        )
        cliente = Cliente.objects.get()
        self.assertRedirects(resposta, reverse("caderneta:cliente", args=[cliente.pk]))
        self.assertEqual(cliente.nome, "João Pereira")
        self.assertEqual(cliente.telefone, "48999991234")

    def test_cadastro_seguido_de_venda(self):
        resposta = self.client.post(f"{reverse('caderneta:cliente_novo')}?depois=venda", {"nome": "Ana"})
        cliente = Cliente.objects.get()
        self.assertRedirects(resposta, reverse("caderneta:venda", args=[cliente.pk]))

    def test_cadastro_com_dados_invalidos_mostra_erros(self):
        resposta = self.client.post(reverse("caderneta:cliente_novo"), {"nome": "", "telefone": "123"})
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "Informe o nome do cliente.")
        self.assertContains(resposta, "Informe o telefone com DDD")
        self.assertContains(resposta, "is-invalid")
        self.assertFalse(Cliente.objects.exists())

    def test_edita_cliente(self):
        cliente = self.criar_cliente(telefone="48999991234")
        resposta = self.client.get(reverse("caderneta:cliente_editar", args=[cliente.pk]))
        self.assertContains(resposta, "(48) 99999-1234")
        self.client.post(
            reverse("caderneta:cliente_editar", args=[cliente.pk]), {"nome": "Maria S.", "telefone": ""}
        )
        cliente.refresh_from_db()
        self.assertEqual(cliente.nome, "Maria S.")
        self.assertEqual(cliente.telefone, "")

    def test_lista_filtra_por_busca_e_situacao(self):
        maria = self.criar_cliente("Maria", telefone="48999990000")
        self.criar_cliente("João")
        self.criar_cliente("Pedro", ativo=False)
        services.registrar_movimentacao(cliente=maria, tipo=VENDA, valor=Decimal("10"))

        def nomes(**parametros):
            resposta = self.client.get(reverse("caderneta:clientes"), parametros)
            return [c.nome for c in resposta.context["clientes"]]

        self.assertEqual(nomes(), ["João", "Maria"])
        self.assertEqual(nomes(situacao="com_debito"), ["Maria"])
        self.assertEqual(nomes(situacao="em_dia"), ["João"])
        self.assertEqual(nomes(situacao="inativos"), ["Pedro"])
        self.assertEqual(nomes(busca="mar"), ["Maria"])
        self.assertEqual(nomes(busca="9999-0000"), ["Maria"])

    def test_detalhe_mostra_saldo_e_historico(self):
        cliente = self.criar_cliente()
        services.registrar_movimentacao(cliente=cliente, tipo=VENDA, valor=Decimal("1234.50"), descricao="Rancho do mês")
        resposta = self.client.get(reverse("caderneta:cliente", args=[cliente.pk]))
        self.assertContains(resposta, "R$ 1.234,50")
        self.assertContains(resposta, "Rancho do mês")

    def test_extrato(self):
        cliente = self.criar_cliente()
        services.registrar_movimentacao(cliente=cliente, tipo=VENDA, valor=Decimal("8"))
        resposta = self.client.get(reverse("caderneta:cliente_extrato", args=[cliente.pk]))
        self.assertContains(resposta, "Extrato de compras a prazo")
        self.assertContains(resposta, "R$ 8,00")

    def test_inativar_e_reativar(self):
        cliente = self.criar_cliente()
        url = reverse("caderneta:cliente_situacao", args=[cliente.pk])
        self.client.post(url)
        cliente.refresh_from_db()
        self.assertFalse(cliente.ativo)
        self.client.post(url)
        cliente.refresh_from_db()
        self.assertTrue(cliente.ativo)

    def test_exclusao_so_para_cliente_sem_lancamentos(self):
        com_lancamento = self.criar_cliente("Com lançamento")
        services.registrar_movimentacao(cliente=com_lancamento, tipo=VENDA, valor=Decimal("5"))
        sem_lancamento = self.criar_cliente("Sem lançamento")

        self.client.post(reverse("caderneta:cliente_excluir", args=[com_lancamento.pk]))
        self.assertTrue(Cliente.objects.filter(pk=com_lancamento.pk).exists())

        resposta = self.client.post(reverse("caderneta:cliente_excluir", args=[sem_lancamento.pk]))
        self.assertRedirects(resposta, reverse("caderneta:clientes"))
        self.assertFalse(Cliente.objects.filter(pk=sem_lancamento.pk).exists())


class LancamentoTelasTests(TelaTestCase):
    def test_fluxo_venda_e_pagamento_atualiza_saldo(self):
        cliente = self.criar_cliente()
        resposta = self.client.post(
            reverse("caderneta:venda", args=[cliente.pk]),
            {"valor": "45,90", "data": "", "descricao": "Compras da semana"},
        )
        # data vazia é obrigatória
        self.assertEqual(resposta.status_code, 200)

        hoje = resposta.context["form"]["data"].initial.isoformat()
        resposta = self.client.post(
            reverse("caderneta:venda", args=[cliente.pk]),
            {"valor": "45,90", "data": hoje, "descricao": "Compras da semana"},
            follow=True,
        )
        self.assertContains(resposta, "Novo saldo: R$ 45,90")

        self.client.post(reverse("caderneta:pagamento", args=[cliente.pk]), {"valor": "20", "data": hoje})
        self.assertEqual(services.saldo_do_cliente(cliente.pk), Decimal("25.90"))

    def test_pagamento_maior_que_o_saldo_mostra_erro_no_campo_valor(self):
        cliente = self.criar_cliente()
        services.registrar_movimentacao(cliente=cliente, tipo=VENDA, valor=Decimal("10"))
        resposta = self.client.post(
            reverse("caderneta:pagamento", args=[cliente.pk]), {"valor": "10,01", "data": "2020-01-01"}
        )
        self.assertEqual(resposta.status_code, 200)
        self.assertIn("maior que o saldo devedor", resposta.context["form"].errors["valor"][0])
        self.assertEqual(Movimentacao.objects.count(), 1)

    def test_valor_invalido_mostra_erro(self):
        cliente = self.criar_cliente()
        resposta = self.client.post(
            reverse("caderneta:venda", args=[cliente.pk]), {"valor": "12,555", "data": "2020-01-01"}
        )
        self.assertContains(resposta, "Valor inválido")
        self.assertFalse(Movimentacao.objects.exists())

    def test_tela_de_pagamento_bloqueada_sem_saldo(self):
        cliente = self.criar_cliente()
        resposta = self.client.get(reverse("caderneta:pagamento", args=[cliente.pk]))
        self.assertContains(resposta, "não possui saldo devedor")
        self.assertNotContains(resposta, "Confirmar pagamento")

    def test_escolher_cliente_lista_apenas_quem_pode_receber_o_lancamento(self):
        devedor = self.criar_cliente("Devedor")
        services.registrar_movimentacao(cliente=devedor, tipo=VENDA, valor=Decimal("3"))
        self.criar_cliente("Em dia")
        self.criar_cliente("Inativo", ativo=False)

        vendas = self.client.get(reverse("caderneta:escolher_venda")).context["clientes"]
        pagamentos = self.client.get(reverse("caderneta:escolher_pagamento")).context["clientes"]

        self.assertEqual([c.nome for c in vendas], ["Devedor", "Em dia"])
        self.assertEqual([c.nome for c in pagamentos], ["Devedor"])

    def test_cancelamento_pela_tela(self):
        cliente = self.criar_cliente()
        venda = services.registrar_movimentacao(cliente=cliente, tipo=VENDA, valor=Decimal("15"))
        url = reverse("caderneta:lancamento_cancelar", args=[venda.pk])

        resposta = self.client.post(url, {"motivo": ""})
        self.assertContains(resposta, "Informe o motivo do cancelamento.")

        resposta = self.client.post(url, {"motivo": "cliente errado"})
        self.assertRedirects(resposta, reverse("caderneta:cliente", args=[cliente.pk]))
        self.assertEqual(services.saldo_do_cliente(cliente.pk), Decimal("0.00"))

    def test_painel_mostra_total_a_receber(self):
        cliente = self.criar_cliente()
        services.registrar_movimentacao(cliente=cliente, tipo=VENDA, valor=Decimal("99.90"))
        resposta = self.client.get(reverse("caderneta:painel"))
        self.assertContains(resposta, "R$ 99,90")
        self.assertEqual(resposta.context["resumo"]["clientes_com_debito"], 1)
