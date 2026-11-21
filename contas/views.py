from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.contrib.auth.views import LoginView
from django.db import transaction
from django.shortcuts import redirect
from django.views.generic import FormView

from .forms import EntrarForm, PrimeiroAcessoForm


def existe_usuario():
    return get_user_model().objects.exists()


class EntrarView(LoginView):
    template_name = "contas/entrar.html"
    form_class = EntrarForm
    redirect_authenticated_user = True

    def dispatch(self, request, *args, **kwargs):
        # Na primeira execução ainda não existe usuário: cria o acesso do comerciante.
        if not existe_usuario():
            return redirect("contas:primeiro_acesso")
        return super().dispatch(request, *args, **kwargs)


class PrimeiroAcessoView(FormView):
    """Cria o usuário do comerciante na primeira execução.

    Evita senhas padrão no projeto: a tela só funciona enquanto nenhum
    usuário existir. Depois disso, redireciona para o login.
    """

    template_name = "contas/primeiro_acesso.html"
    form_class = PrimeiroAcessoForm

    def dispatch(self, request, *args, **kwargs):
        if existe_usuario():
            return redirect("contas:entrar")
        return super().dispatch(request, *args, **kwargs)

    @transaction.atomic
    def form_valid(self, form):
        if existe_usuario():
            messages.error(self.request, "O acesso já foi configurado. Entre com usuário e senha.")
            return redirect("contas:entrar")
        usuario = form.save(commit=False)
        usuario.is_staff = True
        usuario.is_superuser = True
        usuario.save()
        login(self.request, usuario)
        messages.success(self.request, "Acesso criado! Comece cadastrando os seus clientes.")
        return redirect("caderneta:painel")
