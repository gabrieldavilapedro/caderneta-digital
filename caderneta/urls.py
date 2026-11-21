from django.urls import path

from . import views
from .models import Movimentacao

app_name = "caderneta"

VENDA = Movimentacao.Tipo.VENDA
PAGAMENTO = Movimentacao.Tipo.PAGAMENTO

urlpatterns = [
    path("", views.PainelView.as_view(), name="painel"),
    # Clientes
    path("clientes/", views.ClienteListView.as_view(), name="clientes"),
    path("clientes/novo/", views.ClienteCreateView.as_view(), name="cliente_novo"),
    path("clientes/<int:pk>/", views.ClienteDetailView.as_view(), name="cliente"),
    path("clientes/<int:pk>/editar/", views.ClienteUpdateView.as_view(), name="cliente_editar"),
    path("clientes/<int:pk>/situacao/", views.AlternarSituacaoView.as_view(), name="cliente_situacao"),
    path("clientes/<int:pk>/excluir/", views.ClienteExcluirView.as_view(), name="cliente_excluir"),
    path("clientes/<int:pk>/extrato/", views.ExtratoView.as_view(), name="cliente_extrato"),
    # Lançamentos
    path("clientes/<int:pk>/venda/", views.NovoLancamentoView.as_view(tipo=VENDA), name="venda"),
    path("clientes/<int:pk>/pagamento/", views.NovoLancamentoView.as_view(tipo=PAGAMENTO), name="pagamento"),
    path("lancamentos/venda/", views.EscolherClienteView.as_view(tipo=VENDA), name="escolher_venda"),
    path(
        "lancamentos/pagamento/",
        views.EscolherClienteView.as_view(tipo=PAGAMENTO),
        name="escolher_pagamento",
    ),
    path("lancamentos/<int:pk>/cancelar/", views.CancelarLancamentoView.as_view(), name="lancamento_cancelar"),
]
