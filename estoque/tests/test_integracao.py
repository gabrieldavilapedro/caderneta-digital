"""Integração caderneta x estoque: venda a prazo com produtos."""
from decimal import Decimal

from django.test import TestCase

from caderneta import services as caderneta
from caderneta.excecoes import RegraDeNegocioError
from caderneta.models import Cliente, Movimentacao
from estoque import services as estoque
from estoque.models import MovimentoEstoque, Produto
from estoque.services import Item

D = Decimal


class VendaAPrazoComProdutosTests(TestCase):
    def setUp(self):
        self.cliente = Cliente.objects.create(nome="Maria")
        self.leite = Produto.objects.create(nome="Leite", preco_venda=D("5.49"))
        self.pao = Produto.objects.create(nome="Pão francês", preco_venda=D("16.90"), unidade="KG")
        estoque.registrar_entrada(produto=self.leite, quantidade=D("10"))
        estoque.registrar_entrada(produto=self.pao, quantidade=D("3"))

    def vender(self, itens, **extra):
        return caderneta.registrar_movimentacao(cliente=self.cliente, tipo="VENDA", itens=itens, **extra)

    def test_valor_e_o_total_dos_produtos_e_estoque_baixa(self):
        venda = self.vender([Item(self.leite, D("2")), Item(self.pao, D("0.5"))], valor=D("999"))
        self.assertEqual(venda.valor, D("19.43"))  # 2 x 5,49 + 0,5 x 16,90 (valor digitado é ignorado)
        self.assertEqual(caderneta.saldo_do_cliente(self.cliente.pk), D("19.43"))
        self.assertEqual(estoque.estoque_do_produto(self.leite.pk), D("8"))
        self.assertEqual(estoque.estoque_do_produto(self.pao.pk), D("2.5"))
        self.assertEqual(venda.itens.count(), 2)
        self.assertEqual(venda.descricao_completa, "2 un Leite, 0,5 kg Pão francês")

    def test_falta_de_estoque_nao_grava_nem_a_venda(self):
        with self.assertRaises(RegraDeNegocioError) as contexto:
            self.vender([Item(self.leite, D("2")), Item(self.pao, D("4"))])
        self.assertEqual(contexto.exception.campo, "itens")
        self.assertFalse(Movimentacao.objects.exists())
        self.assertEqual(estoque.estoque_do_produto(self.leite.pk), D("10"))

    def test_cancelar_a_venda_devolve_os_produtos(self):
        venda = self.vender([Item(self.leite, D("4"))])
        caderneta.cancelar_movimentacao(venda, "cliente desistiu")
        self.assertEqual(estoque.estoque_do_produto(self.leite.pk), D("10"))
        self.assertEqual(caderneta.saldo_do_cliente(self.cliente.pk), D("0"))
        item = venda.itens.get()
        self.assertTrue(item.cancelado)
        self.assertIn("cliente desistiu", item.motivo_cancelamento)

    def test_item_de_venda_a_prazo_nao_e_cancelado_pelo_estoque(self):
        venda = self.vender([Item(self.leite, D("1"))])
        with self.assertRaises(RegraDeNegocioError):
            estoque.cancelar_movimento(venda.itens.get(), "teste")

    def test_pagamento_nao_aceita_produtos(self):
        self.vender([Item(self.leite, D("1"))])
        with self.assertRaises(RegraDeNegocioError):
            caderneta.registrar_movimentacao(
                cliente=self.cliente, tipo="PAGAMENTO", valor=D("1"), itens=[Item(self.leite, D("1"))]
            )

    def test_venda_sem_produtos_continua_igual(self):
        venda = caderneta.registrar_movimentacao(cliente=self.cliente, tipo="VENDA", valor=D("12.00"))
        self.assertEqual(venda.valor, D("12.00"))
        self.assertFalse(MovimentoEstoque.objects.filter(venda_a_prazo=venda).exists())

    def test_venda_sem_produtos_e_sem_valor_e_recusada(self):
        with self.assertRaises(RegraDeNegocioError) as contexto:
            caderneta.registrar_movimentacao(cliente=self.cliente, tipo="VENDA")
        self.assertEqual(contexto.exception.campo, "valor")
