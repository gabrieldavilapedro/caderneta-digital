"""Testes das regras de negócio do estoque (estoque/services.py)."""
from datetime import timedelta
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from caderneta.excecoes import RegraDeNegocioError
from estoque import services
from estoque.models import MovimentoEstoque, Produto
from estoque.services import Item

D = Decimal


def criar_produto(nome="Leite integral 1 L", preco="5.49", unidade="UN", minimo="0", **extra):
    return Produto.objects.create(nome=nome, preco_venda=D(preco), unidade=unidade, estoque_minimo=D(minimo), **extra)


class BaseEstoque(TestCase):
    def setUp(self):
        self.leite = criar_produto()
        self.queijo = criar_produto("Queijo mussarela", preco="39.90", unidade="KG")

    def estoque(self, produto):
        return services.estoque_do_produto(produto.pk)


class EstoqueCalculadoTests(BaseEstoque):
    def test_produto_novo_tem_estoque_zero(self):
        self.assertEqual(self.estoque(self.leite), D("0"))

    def test_estoque_soma_entradas_e_subtrai_saidas(self):
        services.registrar_entrada(produto=self.leite, quantidade=D("24"))
        services.registrar_venda_a_vista([Item(self.leite, D("3"))])
        services.registrar_perda(produto=self.leite, quantidade=D("1"), motivo="caixa amassada")
        self.assertEqual(self.estoque(self.leite), D("20"))

    def test_quilo_aceita_fracao(self):
        services.registrar_entrada(produto=self.queijo, quantidade=D("2.5"))
        services.registrar_venda_a_vista([Item(self.queijo, D("0.350"))])
        self.assertEqual(self.estoque(self.queijo), D("2.150"))

    def test_unidade_inteira_recusa_fracao(self):
        with self.assertRaises(RegraDeNegocioError) as contexto:
            services.registrar_entrada(produto=self.leite, quantidade=D("1.5"))
        self.assertEqual(contexto.exception.campo, "quantidade")

    def test_movimento_cancelado_nao_conta(self):
        entrada = services.registrar_entrada(produto=self.leite, quantidade=D("10"))
        services.registrar_entrada(produto=self.leite, quantidade=D("5"))
        services.cancelar_movimento(entrada, "quantidade errada")
        self.assertEqual(self.estoque(self.leite), D("5"))

    def test_entrada_com_custo_atualiza_preco_de_custo(self):
        services.registrar_entrada(produto=self.leite, quantidade=D("12"), custo_unitario=D("3.80"))
        self.leite.refresh_from_db()
        self.assertEqual(self.leite.preco_custo, D("3.80"))

    def test_data_futura_e_recusada(self):
        with self.assertRaises(RegraDeNegocioError) as contexto:
            services.registrar_entrada(
                produto=self.leite, quantidade=D("1"), data=timezone.localdate() + timedelta(days=1)
            )
        self.assertEqual(contexto.exception.campo, "data")


class VendaTests(BaseEstoque):
    def setUp(self):
        super().setUp()
        services.registrar_entrada(produto=self.leite, quantidade=D("5"))
        services.registrar_entrada(produto=self.queijo, quantidade=D("1"))

    def test_estoque_insuficiente_bloqueia_a_venda_inteira(self):
        with self.assertRaises(RegraDeNegocioError) as contexto:
            services.registrar_venda_a_vista([Item(self.leite, D("2")), Item(self.queijo, D("1.5"))])
        self.assertIn("Estoque insuficiente de Queijo mussarela", contexto.exception.mensagem)
        # nada foi gravado, nem o item que tinha estoque
        self.assertEqual(self.estoque(self.leite), D("5"))
        self.assertFalse(MovimentoEstoque.objects.filter(tipo="VENDA").exists())

    def test_itens_repetidos_sao_somados(self):
        with self.assertRaises(RegraDeNegocioError):
            services.registrar_venda_a_vista([Item(self.leite, D("3")), Item(self.leite, D("3"))])
        movimentos = services.registrar_venda_a_vista([Item(self.leite, D("2")), Item(self.leite, D("3"))])
        self.assertEqual(len(movimentos), 1)
        self.assertEqual(self.estoque(self.leite), D("0"))

    def test_venda_grava_preco_praticado(self):
        movimento = services.registrar_venda_a_vista([Item(self.leite, D("2"))])[0]
        self.leite.preco_venda = D("6.00")
        self.leite.save()
        movimento.refresh_from_db()
        self.assertEqual(movimento.preco_unitario, D("5.49"))
        self.assertEqual(movimento.total, D("10.98"))

    def test_total_arredonda_centavos(self):
        itens = services.preparar_itens([Item(self.queijo, D("0.350"))])
        self.assertEqual(services.total_dos_itens(itens), D("13.97"))  # 0,350 x 39,90 = 13,965

    def test_produto_inativo_nao_e_vendido(self):
        self.leite.ativo = False
        self.leite.save()
        with self.assertRaises(RegraDeNegocioError):
            services.registrar_venda_a_vista([Item(self.leite, D("1"))])

    def test_venda_sem_itens_e_recusada(self):
        with self.assertRaises(RegraDeNegocioError):
            services.registrar_venda_a_vista([])


class PerdaEAjusteTests(BaseEstoque):
    def setUp(self):
        super().setUp()
        services.registrar_entrada(produto=self.leite, quantidade=D("10"))

    def test_perda_maior_que_o_estoque_e_recusada(self):
        with self.assertRaises(RegraDeNegocioError):
            services.registrar_perda(produto=self.leite, quantidade=D("11"), motivo="vencido")

    def test_perda_exige_motivo(self):
        with self.assertRaises(RegraDeNegocioError) as contexto:
            services.registrar_perda(produto=self.leite, quantidade=D("1"), motivo=" ")
        self.assertEqual(contexto.exception.campo, "observacao")

    def test_ajuste_registra_a_diferenca_da_contagem(self):
        movimento = services.ajustar_estoque(produto=self.leite, quantidade_contada=D("7"))
        self.assertEqual(movimento.quantidade, D("-3"))
        self.assertEqual(self.estoque(self.leite), D("7"))
        movimento = services.ajustar_estoque(produto=self.leite, quantidade_contada=D("9"))
        self.assertEqual(movimento.quantidade, D("2"))
        self.assertEqual(self.estoque(self.leite), D("9"))

    def test_ajuste_sem_diferenca_nao_grava_nada(self):
        self.assertIsNone(services.ajustar_estoque(produto=self.leite, quantidade_contada=D("10")))

    def test_ajuste_para_zero_e_permitido_mas_negativo_nao(self):
        services.ajustar_estoque(produto=self.leite, quantidade_contada=D("0"))
        self.assertEqual(self.estoque(self.leite), D("0"))
        with self.assertRaises(RegraDeNegocioError):
            services.ajustar_estoque(produto=self.leite, quantidade_contada=D("-1"))

    def test_produto_inativo_pode_ter_perda_e_ajuste(self):
        self.leite.ativo = False
        self.leite.save()
        services.registrar_perda(produto=self.leite, quantidade=D("2"), motivo="vencido")
        services.ajustar_estoque(produto=self.leite, quantidade_contada=D("0"))
        self.assertEqual(self.estoque(self.leite), D("0"))


class CancelamentoTests(BaseEstoque):
    def test_nao_cancela_entrada_que_ja_saiu(self):
        entrada = services.registrar_entrada(produto=self.leite, quantidade=D("5"))
        services.registrar_venda_a_vista([Item(self.leite, D("4"))])
        with self.assertRaises(RegraDeNegocioError):
            services.cancelar_movimento(entrada, "engano")

    def test_cancelar_perda_devolve_ao_estoque(self):
        services.registrar_entrada(produto=self.leite, quantidade=D("5"))
        perda = services.registrar_perda(produto=self.leite, quantidade=D("2"), motivo="quebrado")
        services.cancelar_movimento(perda, "não quebrou")
        self.assertEqual(self.estoque(self.leite), D("5"))

    def test_cancelamento_exige_motivo_e_nao_repete(self):
        entrada = services.registrar_entrada(produto=self.leite, quantidade=D("5"))
        with self.assertRaises(RegraDeNegocioError):
            services.cancelar_movimento(entrada, "")
        services.cancelar_movimento(entrada, "engano")
        with self.assertRaises(RegraDeNegocioError):
            services.cancelar_movimento(entrada, "de novo")


class ConsultasTests(BaseEstoque):
    def test_estoque_baixo_inclui_zerados_e_abaixo_do_minimo(self):
        arroz = criar_produto("Arroz 5 kg", preco="27.90", minimo="5")
        services.registrar_entrada(produto=arroz, quantidade=D("3"))
        cafe = criar_produto("Café 500 g", preco="18.90", minimo="2")
        services.registrar_entrada(produto=cafe, quantidade=D("10"))

        baixos = [p.nome for p in services.produtos_com_estoque_baixo()]

        self.assertIn("Arroz 5 kg", baixos)
        self.assertIn("Leite integral 1 L", baixos)  # zerado
        self.assertNotIn("Café 500 g", baixos)

    def test_resumo_valoriza_o_estoque(self):
        self.leite.preco_custo = D("4.00")
        self.leite.save()
        services.registrar_entrada(produto=self.leite, quantidade=D("10"))
        resumo = services.resumo_estoque()
        self.assertEqual(resumo["valor_venda"], D("54.90"))
        self.assertEqual(resumo["valor_custo"], D("40.00"))

    def test_historico_traz_estoque_apos_cada_movimento(self):
        services.registrar_entrada(produto=self.leite, quantidade=D("10"))
        services.registrar_venda_a_vista([Item(self.leite, D("3"))])
        historico = services.historico_do_produto(self.leite)
        self.assertEqual([m.estoque_apos for m in historico], [D("7"), D("10")])


class IntegridadeNoBancoTests(BaseEstoque):
    def test_banco_recusa_sinal_incompativel_com_o_tipo(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            MovimentoEstoque.objects.create(produto=self.leite, tipo="ENTRADA", quantidade=D("-1"))
        with self.assertRaises(IntegrityError), transaction.atomic():
            MovimentoEstoque.objects.create(produto=self.leite, tipo="VENDA", quantidade=D("1"))

    def test_codigo_nao_pode_repetir(self):
        criar_produto("A", codigo="789100")
        with self.assertRaises(IntegrityError), transaction.atomic():
            criar_produto("B", codigo="789100")
        criar_produto("C")  # código vazio pode repetir
        criar_produto("D")
