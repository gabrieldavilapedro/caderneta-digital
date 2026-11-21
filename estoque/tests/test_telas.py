"""Testes das telas do estoque e da venda a prazo com produtos."""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from caderneta import services as caderneta
from caderneta.models import Cliente, Movimentacao
from estoque import services
from estoque.models import MovimentoEstoque, Produto

D = Decimal


def dados_itens(*itens, prefixo="itens"):
    """Monta o POST de um formset de itens: dados_itens((produto, "2"), ...)."""
    dados = {
        f"{prefixo}-TOTAL_FORMS": str(len(itens)),
        f"{prefixo}-INITIAL_FORMS": "0",
        f"{prefixo}-MIN_NUM_FORMS": "0",
        f"{prefixo}-MAX_NUM_FORMS": "50",
    }
    for indice, (produto, quantidade) in enumerate(itens):
        dados[f"{prefixo}-{indice}-produto"] = str(produto.pk) if produto else ""
        dados[f"{prefixo}-{indice}-quantidade"] = quantidade
    return dados


class TelaEstoqueTestCase(TestCase):
    def setUp(self):
        usuario = get_user_model().objects.create_user("comerciante", password="senha-forte-123")
        self.client.force_login(usuario)
        self.hoje = timezone.localdate().isoformat()
        self.leite = Produto.objects.create(nome="Leite", preco_venda=D("5.49"), estoque_minimo=D("6"))
        services.registrar_entrada(produto=self.leite, quantidade=D("10"))


class ProdutoTelasTests(TelaEstoqueTestCase):
    def test_estoque_exige_login(self):
        self.client.logout()
        resposta = self.client.get(reverse("estoque:produtos"))
        self.assertEqual(resposta.status_code, 302)

    def test_cadastra_produto_com_estoque_inicial(self):
        resposta = self.client.post(
            reverse("estoque:produto_novo"),
            {
                "nome": "Queijo  mussarela",
                "codigo": "7891000",
                "unidade": "KG",
                "preco_venda": "39,90",
                "preco_custo": "28,50",
                "estoque_minimo": "1,5",
                "estoque_inicial": "3,250",
            },
        )
        produto = Produto.objects.get(codigo="7891000")
        self.assertRedirects(resposta, reverse("estoque:produto", args=[produto.pk]))
        self.assertEqual(produto.nome, "Queijo mussarela")
        self.assertEqual(produto.preco_venda, D("39.90"))
        self.assertEqual(produto.estoque_minimo, D("1.500"))
        self.assertEqual(services.estoque_do_produto(produto.pk), D("3.250"))
        self.assertEqual(produto.movimentos.get().observacao, "Estoque inicial")

    def test_cadastro_com_erros(self):
        Produto.objects.create(nome="Outro", preco_venda=D("1"), codigo="123")
        resposta = self.client.post(
            reverse("estoque:produto_novo"),
            {"nome": "", "codigo": "123", "unidade": "UN", "preco_venda": "abc", "estoque_inicial": "1,5"},
        )
        self.assertEqual(resposta.status_code, 200)
        erros = resposta.context["form"].errors
        self.assertIn("nome", erros)
        self.assertIn("Já existe um produto com este código.", erros["codigo"])
        self.assertIn("preco_venda", erros)
        self.assertIn("estoque_inicial", erros)  # unidade inteira

    def test_edicao_mostra_valores_no_formato_brasileiro(self):
        resposta = self.client.get(reverse("estoque:produto_editar", args=[self.leite.pk]))
        self.assertContains(resposta, 'value="5,49"')
        self.assertContains(resposta, 'value="6"')
        self.assertNotIn("estoque_inicial", resposta.context["form"].fields)

    def test_lista_filtra_estoque_baixo(self):
        Produto.objects.create(nome="Café", preco_venda=D("18.90"))  # zerado
        arroz = Produto.objects.create(nome="Arroz", preco_venda=D("27.90"), estoque_minimo=D("2"))
        services.registrar_entrada(produto=arroz, quantidade=D("20"))

        def nomes(**parametros):
            return [p.nome for p in self.client.get(reverse("estoque:produtos"), parametros).context["produtos"]]

        self.assertEqual(nomes(), ["Arroz", "Café", "Leite"])
        self.assertEqual(nomes(situacao="estoque_baixo"), ["Café"])
        self.assertEqual(nomes(situacao="sem_estoque"), ["Café"])
        self.assertEqual(nomes(busca="arr"), ["Arroz"])

    def test_detalhe_mostra_historico(self):
        resposta = self.client.get(reverse("estoque:produto", args=[self.leite.pk]))
        self.assertContains(resposta, "Movimentos do estoque")
        self.assertContains(resposta, "10 un")

    def test_exclusao_so_sem_movimentos(self):
        novo = Produto.objects.create(nome="Novo", preco_venda=D("1"))
        self.client.post(reverse("estoque:produto_excluir", args=[self.leite.pk]))
        self.assertTrue(Produto.objects.filter(pk=self.leite.pk).exists())
        self.client.post(reverse("estoque:produto_excluir", args=[novo.pk]))
        self.assertFalse(Produto.objects.filter(pk=novo.pk).exists())


class MovimentoTelasTests(TelaEstoqueTestCase):
    def test_entrada(self):
        resposta = self.client.post(
            reverse("estoque:entrada", args=[self.leite.pk]),
            {"quantidade": "12", "custo_unitario": "3,90", "data": self.hoje, "observacao": "Fornecedor X"},
            follow=True,
        )
        self.assertContains(resposta, "Estoque atual: 22 un")

    def test_entrada_com_fracao_em_unidade_inteira_mostra_erro(self):
        resposta = self.client.post(
            reverse("estoque:entrada", args=[self.leite.pk]), {"quantidade": "1,5", "data": self.hoje}
        )
        self.assertIn("quantidade inteira", resposta.context["form"].errors["quantidade"][0])

    def test_perda_maior_que_estoque_mostra_erro(self):
        resposta = self.client.post(
            reverse("estoque:perda", args=[self.leite.pk]),
            {"quantidade": "11", "data": self.hoje, "observacao": "vencido"},
        )
        self.assertIn("Estoque insuficiente", resposta.context["form"].errors["quantidade"][0])

    def test_ajuste(self):
        resposta = self.client.post(reverse("estoque:ajuste", args=[self.leite.pk]), {"quantidade": "8"}, follow=True)
        self.assertContains(resposta, "Estoque atual: 8 un")
        resposta = self.client.post(reverse("estoque:ajuste", args=[self.leite.pk]), {"quantidade": "8"}, follow=True)
        self.assertContains(resposta, "Nenhum ajuste foi necessário")

    def test_ajuste_sem_campo_data_nao_quebra_ao_reexibir_erro(self):
        resposta = self.client.post(reverse("estoque:ajuste", args=[self.leite.pk]), {"quantidade": "abc"})
        self.assertEqual(resposta.status_code, 200)
        self.assertNotContains(resposta, "QueryDict")

    def test_cancelar_movimento(self):
        perda = services.registrar_perda(produto=self.leite, quantidade=D("2"), motivo="quebrado")
        resposta = self.client.post(reverse("estoque:movimento_cancelar", args=[perda.pk]), {"motivo": "engano"})
        self.assertRedirects(resposta, reverse("estoque:produto", args=[self.leite.pk]))
        self.assertEqual(services.estoque_do_produto(self.leite.pk), D("10"))

    def test_venda_a_vista_com_varios_produtos(self):
        cafe = Produto.objects.create(nome="Café", preco_venda=D("18.90"))
        services.registrar_entrada(produto=cafe, quantidade=D("5"))
        resposta = self.client.post(
            reverse("estoque:venda_avista"),
            {"data": self.hoje, **dados_itens((self.leite, "3"), (cafe, "1"), (None, ""))},
            follow=True,
        )
        self.assertContains(resposta, "total R$ 35,37")  # 3 x 5,49 + 18,90
        self.assertEqual(services.estoque_do_produto(self.leite.pk), D("7"))
        self.assertEqual(services.estoque_do_produto(cafe.pk), D("4"))

    def test_venda_a_vista_sem_estoque_mostra_erro(self):
        resposta = self.client.post(
            reverse("estoque:venda_avista"), {"data": self.hoje, **dados_itens((self.leite, "11"))}
        )
        self.assertContains(resposta, "Estoque insuficiente de Leite")
        self.assertFalse(MovimentoEstoque.objects.filter(tipo="VENDA").exists())

    def test_venda_a_vista_vazia_mostra_erro(self):
        resposta = self.client.post(reverse("estoque:venda_avista"), {"data": self.hoje, **dados_itens((None, ""))})
        self.assertContains(resposta, "Adicione pelo menos um produto")

    def test_linha_com_quantidade_sem_produto_mostra_erro(self):
        resposta = self.client.post(reverse("estoque:venda_avista"), {"data": self.hoje, **dados_itens((None, "2"))})
        self.assertContains(resposta, "Selecione o produto.")


class VendaAPrazoComProdutosTelaTests(TelaEstoqueTestCase):
    def setUp(self):
        super().setUp()
        self.cliente = Cliente.objects.create(nome="Maria")

    def test_formulario_de_venda_oferece_produtos(self):
        resposta = self.client.get(reverse("caderneta:venda", args=[self.cliente.pk]))
        self.assertContains(resposta, "Escolher produtos do estoque")
        self.assertContains(resposta, 'data-preco-centavos="549"')

    def test_venda_com_produtos_calcula_valor_e_baixa_estoque(self):
        resposta = self.client.post(
            reverse("caderneta:venda", args=[self.cliente.pk]),
            {"valor": "", "data": self.hoje, "descricao": "", **dados_itens((self.leite, "2"))},
            follow=True,
        )
        self.assertContains(resposta, "Venda de R$ 10,98 registrada para Maria (1 produto baixado do estoque)")
        self.assertContains(resposta, "2 un Leite")
        self.assertEqual(services.estoque_do_produto(self.leite.pk), D("8"))
        self.assertEqual(caderneta.saldo_do_cliente(self.cliente.pk), D("10.98"))

    def test_venda_sem_produtos_exige_valor(self):
        resposta = self.client.post(
            reverse("caderneta:venda", args=[self.cliente.pk]),
            {"valor": "", "data": self.hoje, **dados_itens((None, ""))},
        )
        self.assertContains(resposta, "Informe o valor ou adicione produtos do estoque.")
        self.assertFalse(Movimentacao.objects.exists())

    def test_venda_com_estoque_insuficiente_nao_grava(self):
        resposta = self.client.post(
            reverse("caderneta:venda", args=[self.cliente.pk]),
            {"valor": "", "data": self.hoje, **dados_itens((self.leite, "50"))},
        )
        self.assertContains(resposta, "Estoque insuficiente de Leite")
        self.assertFalse(Movimentacao.objects.exists())

    def test_cancelar_venda_pela_tela_devolve_estoque(self):
        venda = caderneta.registrar_movimentacao(
            cliente=self.cliente, tipo="VENDA", itens=[services.Item(self.leite, D("3"))]
        )
        resposta = self.client.post(
            reverse("caderneta:lancamento_cancelar", args=[venda.pk]), {"motivo": "devolução"}, follow=True
        )
        self.assertContains(resposta, "Os produtos voltaram ao estoque.")
        self.assertEqual(services.estoque_do_produto(self.leite.pk), D("10"))

    def test_painel_mostra_estoque_baixo(self):
        services.registrar_venda_a_vista([services.Item(self.leite, D("5"))])  # fica 5, mínimo 6
        resposta = self.client.get(reverse("caderneta:painel"))
        self.assertContains(resposta, "Estoque baixo")
        self.assertEqual([p.nome for p in resposta.context["resumo"]["estoque_baixo"]], ["Leite"])
