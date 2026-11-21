from django.urls import path

from . import views

app_name = "estoque"

urlpatterns = [
    path("", views.ProdutoListView.as_view(), name="produtos"),
    path("produtos/novo/", views.ProdutoCreateView.as_view(), name="produto_novo"),
    path("produtos/<int:pk>/", views.ProdutoDetailView.as_view(), name="produto"),
    path("produtos/<int:pk>/editar/", views.ProdutoUpdateView.as_view(), name="produto_editar"),
    path("produtos/<int:pk>/situacao/", views.AlternarSituacaoProdutoView.as_view(), name="produto_situacao"),
    path("produtos/<int:pk>/excluir/", views.ProdutoExcluirView.as_view(), name="produto_excluir"),
    path("produtos/<int:pk>/entrada/", views.EntradaView.as_view(), name="entrada"),
    path("produtos/<int:pk>/perda/", views.PerdaView.as_view(), name="perda"),
    path("produtos/<int:pk>/ajuste/", views.AjusteView.as_view(), name="ajuste"),
    path("entrada/", views.EscolherProdutoEntradaView.as_view(), name="escolher_entrada"),
    path("venda/", views.VendaAVistaView.as_view(), name="venda_avista"),
    path("movimentos/<int:pk>/cancelar/", views.CancelarMovimentoView.as_view(), name="movimento_cancelar"),
]
