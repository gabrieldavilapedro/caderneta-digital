from django.contrib.auth.views import LogoutView
from django.urls import path

from . import views

app_name = "contas"

urlpatterns = [
    path("entrar/", views.EntrarView.as_view(), name="entrar"),
    path("sair/", LogoutView.as_view(), name="sair"),
    path("primeiro-acesso/", views.PrimeiroAcessoView.as_view(), name="primeiro_acesso"),
]
