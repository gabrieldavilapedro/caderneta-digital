from decimal import Decimal

from django import forms
from django.utils import timezone

from .models import Cliente, Movimentacao
from .utils import formatar_telefone, ler_valor, somente_digitos


class BootstrapMixin:
    """Aplica as classes do Bootstrap aos campos e marca os campos com erro."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for campo in self.fields.values():
            widget = campo.widget
            if isinstance(widget, (forms.RadioSelect, forms.CheckboxInput)):
                continue
            classe = "form-select" if isinstance(widget, forms.Select) else "form-control"
            widget.attrs["class"] = f"{widget.attrs.get('class', '')} {classe}".strip()

    def add_error(self, field, error):
        super().add_error(field, error)
        for nome in self._errors:
            if nome in self.fields:
                attrs = self.fields[nome].widget.attrs
                classes = attrs.get("class", "").split()
                if "is-invalid" not in classes:
                    attrs["class"] = " ".join([*classes, "is-invalid"])


class ClienteForm(BootstrapMixin, forms.ModelForm):
    # Aceita o telefone formatado "(48) 99999-1234"; grava apenas os dígitos.
    telefone = forms.CharField(
        label="Telefone",
        max_length=20,
        required=False,
        help_text="Com DDD. Usado apenas para contato.",
        widget=forms.TextInput(
            attrs={
                "inputmode": "tel",
                "autocomplete": "off",
                "placeholder": "(48) 99999-9999",
                "data-mascara": "telefone",
            }
        ),
    )

    class Meta:
        model = Cliente
        fields = ["nome", "telefone", "observacoes"]
        labels = {"nome": "Nome", "observacoes": "Observações"}
        help_texts = {"observacoes": "Algo que ajude a identificar o cliente (até 500 caracteres)."}
        widgets = {
            "nome": forms.TextInput(
                attrs={"autofocus": True, "autocomplete": "off", "placeholder": "Ex.: Maria da Silva"}
            ),
            "observacoes": forms.Textarea(
                attrs={"rows": 3, "placeholder": "Ex.: mora na rua da padaria; filha do seu João"}
            ),
        }
        error_messages = {"nome": {"required": "Informe o nome do cliente."}}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk and not self.is_bound:
            self.initial["telefone"] = formatar_telefone(self.instance.telefone)

    def clean_nome(self):
        nome = " ".join(self.cleaned_data["nome"].split())
        if len(nome) < 2:
            raise forms.ValidationError("O nome deve ter pelo menos 2 letras.")
        return nome

    def clean_telefone(self):
        digitos = somente_digitos(self.cleaned_data.get("telefone"))
        if digitos and len(digitos) not in (10, 11):
            raise forms.ValidationError("Informe o telefone com DDD (10 ou 11 dígitos).")
        return digitos

    def clean_observacoes(self):
        return (self.cleaned_data.get("observacoes") or "").strip()


class LancamentoForm(BootstrapMixin, forms.Form):
    """Formulário de venda a prazo ou pagamento. O cliente e o tipo vêm da URL."""

    valor = forms.CharField(
        label="Valor",
        max_length=20,
        required=False,
        widget=forms.TextInput(
            attrs={
                "inputmode": "decimal",
                "autocomplete": "off",
                "placeholder": "0,00",
                "data-mascara": "dinheiro",
                "autofocus": True,
                "class": "form-control-lg valor",
            }
        ),
    )
    data = forms.DateField(
        label="Data",
        error_messages={"required": "Informe a data.", "invalid": "Informe uma data válida."},
        widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date", "class": "form-control-lg"}),
    )
    descricao = forms.CharField(label="Descrição", max_length=200, required=False)

    def __init__(self, *args, tipo, exige_valor=True, **kwargs):
        # exige_valor=False quando a venda tem produtos do estoque: o valor é o total dos produtos.
        self.exige_valor = exige_valor
        self.tipo = tipo
        super().__init__(*args, **kwargs)
        hoje = timezone.localdate()
        self.fields["data"].initial = hoje
        self.fields["data"].widget.attrs["max"] = hoje.isoformat()
        exemplo = "Ex.: pão, leite e café" if tipo == Movimentacao.Tipo.VENDA else "Ex.: pago em dinheiro"
        self.fields["descricao"].widget.attrs.update({"placeholder": exemplo, "autocomplete": "off"})

    def clean_valor(self):
        if not self.exige_valor:
            return None  # calculado a partir dos produtos
        if not self.cleaned_data.get("valor"):
            if self.tipo == Movimentacao.Tipo.VENDA:
                raise forms.ValidationError("Informe o valor ou adicione produtos do estoque.")
            raise forms.ValidationError("Informe o valor.")
        try:
            valor = ler_valor(self.cleaned_data["valor"])
        except ValueError:
            raise forms.ValidationError("Valor inválido. Use o formato 12,50.") from None
        if valor <= 0:
            raise forms.ValidationError("Informe um valor maior que zero.")
        if valor >= Decimal("100000000"):
            raise forms.ValidationError("Valor muito alto. Confira o que foi digitado.")
        return valor

    def clean_data(self):
        data = self.cleaned_data["data"]
        if data > timezone.localdate():
            raise forms.ValidationError("A data não pode estar no futuro.")
        return data

    def clean_descricao(self):
        return (self.cleaned_data.get("descricao") or "").strip()


class CancelamentoForm(BootstrapMixin, forms.Form):
    motivo = forms.CharField(
        label="Motivo do cancelamento",
        max_length=200,
        error_messages={"required": "Informe o motivo do cancelamento."},
        widget=forms.TextInput(
            attrs={"autofocus": True, "autocomplete": "off", "placeholder": "Ex.: valor digitado errado"}
        ),
    )
