"""Testes das correções apontadas na revisão de código."""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from caderneta import services as caderneta
from caderneta.excecoes import RegraDeNegocioError
from caderneta.models import Cliente
from caderneta.utils import ler_quantidade, ler_valor
from estoque import services
from estoque.models import Produto
from estoque.services import Item

D = Decimal


class LeituraDeQuantidadeTests(TestCase):
    def test_ponto_unico_em_quantidade_e_decimal(self):
        casos = {"0.350": "0.350", "1.500": "1.5", "0.5": "0.5", "2,5": "2.5", "1.234.567": "1234567"}
        for texto, esperado in casos.items():
            with self.subTest(texto=texto):
                self.assertEqual(ler_quantidade(texto), D(esperado))

    def test_dinheiro_mantem_ponto_como_milhar(self):
        self.assertEqual(ler_valor("1.234"), D("1234.00"))
        with self.assertRaises(ValueError):
            ler_valor("0.350")  # não é milhar e tem 3 casas: inválido em dinheiro


class BaseCorrecoes(TestCase):
    def setUp(self):
        self.hoje = timezone.localdate()
        self.leite = Produto.objects.create(nome="Leite", preco_venda=D("5.49"))


class HistoricoNuncaNegativoTests(BaseCorrecoes):
    def test_perda_retroativa_antes_da_entrada_e_recusada(self):
        services.registrar_entrada(produto=self.leite, quantidade=D("10"), data=self.hoje)
        with self.assertRaises(RegraDeNegocioError) as contexto:
            services.registrar_perda(
                produto=self.leite, quantidade=D("5"), motivo="vencido", data=self.hoje - timedelta(days=3)
            )
        self.assertIn("nessa data", contexto.exception.mensagem)
        self.assertEqual(services.estoque_do_produto(self.leite.pk), D("10"))

    def test_venda_retroativa_antes_da_entrada_e_recusada(self):
        services.registrar_entrada(produto=self.leite, quantidade=D("10"), data=self.hoje)
        with self.assertRaises(RegraDeNegocioError):
            services.registrar_venda_a_vista([Item(self.leite, D("1"))], data=self.hoje - timedelta(days=1))

    def test_venda_retroativa_depois_da_entrada_e_aceita(self):
        services.registrar_entrada(produto=self.leite, quantidade=D("10"), data=self.hoje - timedelta(days=5))
        services.registrar_venda_a_vista([Item(self.leite, D("4"))], data=self.hoje - timedelta(days=2))
        self.assertEqual(services.estoque_do_produto(self.leite.pk), D("6"))

    def test_cancelar_entrada_antiga_que_deixaria_historico_negativo_e_recusado(self):
        primeira = services.registrar_entrada(produto=self.leite, quantidade=D("10"), data=self.hoje - timedelta(days=3))
        services.registrar_venda_a_vista([Item(self.leite, D("8"))], data=self.hoje - timedelta(days=2))
        services.registrar_entrada(produto=self.leite, quantidade=D("10"), data=self.hoje - timedelta(days=1))
        with self.assertRaises(RegraDeNegocioError):
            services.cancelar_movimento(primeira, "engano")

    def test_venda_a_prazo_retroativa_confere_o_estoque_na_data(self):
        cliente = Cliente.objects.create(nome="Maria")
        services.registrar_entrada(produto=self.leite, quantidade=D("10"), data=self.hoje)
        with self.assertRaises(RegraDeNegocioError):
            caderneta.registrar_movimentacao(
                cliente=cliente, tipo="VENDA", data=self.hoje - timedelta(days=1), itens=[Item(self.leite, D("1"))]
            )


class LimitesDeValorTests(BaseCorrecoes):
    def test_total_muito_alto_vira_mensagem_e_nao_erro_500(self):
        caro = Produto.objects.create(nome="Caro", preco_venda=D("1000.00"))
        services.registrar_entrada(produto=caro, quantidade=D("200000"))
        cliente = Cliente.objects.create(nome="Maria")
        with self.assertRaises(RegraDeNegocioError) as contexto:
            caderneta.registrar_movimentacao(cliente=cliente, tipo="VENDA", itens=[Item(caro, D("200000"))])
        self.assertEqual(contexto.exception.campo, "itens")
        self.assertIn("Valor muito alto", contexto.exception.mensagem)

    def test_quantidade_com_valor_abaixo_de_um_centavo_e_recusada(self):
        barato = Produto.objects.create(nome="Barato", preco_venda=D("0.01"), unidade="KG")
        services.registrar_entrada(produto=barato, quantidade=D("1"))
        with self.assertRaises(RegraDeNegocioError) as contexto:
            services.registrar_venda_a_vista([Item(barato, D("0.001"))])
        self.assertEqual(contexto.exception.campo, "itens")


class CustoAoCancelarEntradaTests(BaseCorrecoes):
    def test_cancelar_entrada_volta_ao_custo_anterior(self):
        services.registrar_entrada(produto=self.leite, quantidade=D("10"), custo_unitario=D("3.90"))
        errada = services.registrar_entrada(produto=self.leite, quantidade=D("5"), custo_unitario=D("500.00"))
        services.cancelar_movimento(errada, "custo digitado errado")
        self.leite.refresh_from_db()
        self.assertEqual(self.leite.preco_custo, D("3.90"))

    def test_unica_entrada_com_custo_cancelada_deixa_custo_em_branco(self):
        errada = services.registrar_entrada(produto=self.leite, quantidade=D("5"), custo_unitario=D("500.00"))
        services.cancelar_movimento(errada, "engano")
        self.leite.refresh_from_db()
        self.assertIsNone(self.leite.preco_custo)

    def test_custo_editado_depois_nao_e_sobrescrito(self):
        entrada = services.registrar_entrada(produto=self.leite, quantidade=D("5"), custo_unitario=D("4.00"))
        self.leite.preco_custo = D("4.20")
        self.leite.save()
        services.cancelar_movimento(entrada, "engano")
        self.leite.refresh_from_db()
        self.assertEqual(self.leite.preco_custo, D("4.20"))


class TelasCorrecoesTests(BaseCorrecoes):
    def setUp(self):
        super().setUp()
        usuario = get_user_model().objects.create_user("comerciante", password="senha-forte-123")
        self.client.force_login(usuario)
        services.registrar_entrada(produto=self.leite, quantidade=D("10"))
        self.cliente = Cliente.objects.create(nome="Maria")

    def post_venda(self, *linhas):
        dados = {
            "valor": "", "data": f"{self.hoje:%Y-%m-%d}", "descricao": "",
            "itens-TOTAL_FORMS": str(len(linhas)), "itens-INITIAL_FORMS": "0",
            "itens-MIN_NUM_FORMS": "0", "itens-MAX_NUM_FORMS": "50",
        }
        for indice, (produto, quantidade, preco) in enumerate(linhas):
            dados[f"itens-{indice}-produto"] = str(produto.pk)
            dados[f"itens-{indice}-quantidade"] = quantidade
            dados[f"itens-{indice}-preco_exibido"] = preco
        return self.client.post(reverse("caderneta:venda", args=[self.cliente.pk]), dados, follow=True)

    def test_unidade_nao_muda_se_o_produto_tem_movimentos(self):
        url = reverse("estoque:produto_editar", args=[self.leite.pk])
        self.assertContains(self.client.get(url), "Não pode ser alterada")
        self.client.post(url, {"nome": "Leite", "codigo": "", "unidade": "KG", "preco_venda": "5,49", "estoque_minimo": "0"})
        self.leite.refresh_from_db()
        self.assertEqual(self.leite.unidade, "UN")

    def test_unidade_pode_mudar_em_produto_sem_movimentos(self):
        novo = Produto.objects.create(nome="Queijo", preco_venda=D("39.90"))
        self.client.post(
            reverse("estoque:produto_editar", args=[novo.pk]),
            {"nome": "Queijo", "codigo": "", "unidade": "KG", "preco_venda": "39,90", "estoque_minimo": "0"},
        )
        novo.refresh_from_db()
        self.assertEqual(novo.unidade, "KG")

    def test_preco_alterado_depois_de_abrir_a_tela_e_avisado(self):
        self.leite.preco_venda = D("6.49")
        self.leite.save()
        resposta = self.post_venda((self.leite, "2", "549"))  # a tela mostrava R$ 5,49
        self.assertContains(resposta, "O preço de Leite mudou para R$ 6,49/un")
        self.assertFalse(self.cliente.movimentacoes.exists())
        resposta = self.post_venda((self.leite, "2", "649"))
        self.assertContains(resposta, "Venda de R$ 12,98 registrada")

    def test_mensagem_conta_produtos_distintos(self):
        resposta = self.post_venda((self.leite, "1", "549"), (self.leite, "1", "549"))
        self.assertContains(resposta, "(1 produto baixado do estoque)")
        self.assertEqual(services.estoque_do_produto(self.leite.pk), D("8"))
