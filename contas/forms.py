from django.contrib.auth.forms import AuthenticationForm, UserCreationForm

from caderneta.forms import BootstrapMixin


class EntrarForm(BootstrapMixin, AuthenticationForm):
    error_messages = {
        **AuthenticationForm.error_messages,
        "invalid_login": "Usuário ou senha incorretos.",
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].label = "Usuário"
        self.fields["password"].label = "Senha"


class PrimeiroAcessoForm(BootstrapMixin, UserCreationForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].label = "Usuário"
        self.fields["username"].help_text = "Nome que será usado para entrar. Ex.: mercadinho"
        self.fields["username"].widget.attrs.update({"autofocus": True, "autocomplete": "username"})
        self.fields["password2"].label = "Confirme a senha"
