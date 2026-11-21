from decimal import Decimal

from django import forms
from django.forms import BaseFormSet, formset_factory
from django.utils import timezone

from caderneta.forms import BootstrapMixin
from caderneta.utils import formatar_moeda, formatar_para_campo, ler_quantidade, ler_valor

from .models import Produto
from .services import Item

LIMITE_QUANTIDADE = Decimal("1000000")


# --- Campos numéricos (aceitam apenas números e vírgula; ver static/js/caderneta.js) ---

def campo_dinheiro(rotulo, obrigatorio=True, mensagem=None):
    return forms.CharField(
        label=rotulo,
        required=obrigatorio,
        max_length=20,
        error_messages={"required": mensagem or f"Informe o {rotulo.lower()}."},
        widget=forms.TextInput(
            attrs={"inputmode": "decimal", "autocomplete": "off", "placeholder": "0,00", "data-mascara": "dinheiro"}
        ),
    )


def campo_quantidade(rotulo="Quantidade", obrigatorio=True, mensagem="Informe a quantidade."):
    return forms.CharField(
        label=rotulo,
        required=obrigatorio,
        max_length=20,
        error_messages={"required": mensagem},
        widget=forms.TextInput(
            attrs={"inputmode": "decimal", "autocomplete": "off", "placeholder": "0", "data-mascara": "quantidade"}
        ),
    )


def campo_data():
    return forms.DateField(
        label="Data",
        initial=timezone.localdate,
        error_messages={"required": "Informe a data.", "invalid": "Informe uma data válida."},
        widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
    )


class DataAteHojeMixin:
    """Limita o calendário do campo "data" até hoje (calculado a cada formulário)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "data" in self.fields:
            self.fields["data"].widget.attrs["max"] = timezone.localdate().isoformat()


def limpar_dinheiro(texto, minimo=Decimal("0.01")):
    if not texto:
        return None
    try:
        valor = ler_valor(texto)
    except ValueError:
        raise forms.ValidationError("Valor inválido. Use o formato 12,50.") from None
    if valor < minimo:
        raise forms.ValidationError("Informe um valor maior que zero.")
    if valor >= Decimal("100000000"):
        raise forms.ValidationError("Valor muito alto. Confira o que foi digitado.")
    return valor


def limpar_quantidade(texto, permitir_zero=False):
    if texto in (None, ""):
        return None
    try:
        quantidade = ler_quantidade(texto)
    except ValueError:
        raise forms.ValidationError("Quantidade inválida. Use até 3 casas decimais, ex.: 1,5.") from None
    if quantidade < 0 or (quantidade == 0 and not permitir_zero):
        raise forms.ValidationError("Informe uma quantidade maior que zero.")
    if quantidade >= LIMITE_QUANTIDADE:
        raise forms.ValidationError("Quantidade muito alta. Confira o que foi digitado.")
    return quantidade


def eh_inteiro(numero):
    return numero is None or numero == numero.to_integral_value()


# --- Produto -------------------------------------------------------------------

class ProdutoForm(BootstrapMixin, forms.ModelForm):
    preco_venda = campo_dinheiro("Preço de venda")
    preco_custo = campo_dinheiro("Preço de custo", obrigatorio=False)
    estoque_minimo = campo_quantidade("Estoque mínimo", obrigatorio=False)
    estoque_inicial = campo_quantidade("Estoque inicial", obrigatorio=False)

    class Meta:
        model = Produto
        fields = ["nome", "codigo", "unidade", "preco_venda", "preco_custo", "estoque_minimo"]
        labels = {"nome": "Nome do produto", "codigo": "Código"}
        widgets = {
            "nome": forms.TextInput(
                attrs={"autofocus": True, "autocomplete": "off", "placeholder": "Ex.: Leite integral 1 L"}
            ),
            "codigo": forms.TextInput(attrs={"autocomplete": "off", "placeholder": "Código de barras ou interno"}),
        }
        error_messages = {"nome": {"required": "Informe o nome do produto."}}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["codigo"].help_text = "Opcional. Não pode repetir entre produtos."
        self.fields["preco_custo"].help_text = "Opcional. Atualizado a cada entrada com custo informado."
        self.fields["estoque_minimo"].help_text = "O painel avisa quando o estoque chegar a esta quantidade."
        self.fields["estoque_inicial"].help_text = "Quantidade que existe hoje na loja."
        if self.instance.pk:
            del self.fields["estoque_inicial"]  # depois do cadastro, use entrada ou ajuste
            if self.instance.movimentos.exists():
                # Trocar kg por un com estoque fracionado deixaria quantidades impossíveis de vender.
                self.fields["unidade"].disabled = True
                self.fields["unidade"].help_text = (
                    "Não pode ser alterada porque o produto já tem movimentos de estoque. "
                    "Se a unidade estiver errada, cadastre um novo produto."
                )
            if not self.is_bound:
                self.initial.update(
                    preco_venda=formatar_para_campo(self.instance.preco_venda, 2),
                    preco_custo=formatar_para_campo(self.instance.preco_custo, 2),
                    estoque_minimo=formatar_para_campo(self.instance.estoque_minimo, 3, aparar=True),
                )

    def clean_nome(self):
        nome = " ".join(self.cleaned_data["nome"].split())
        if len(nome) < 2:
            raise forms.ValidationError("O nome deve ter pelo menos 2 letras.")
        return nome

    def clean_codigo(self):
        codigo = (self.cleaned_data.get("codigo") or "").strip()
        if codigo and Produto.objects.filter(codigo=codigo).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("Já existe um produto com este código.")
        return codigo

    def clean_preco_venda(self):
        return limpar_dinheiro(self.cleaned_data.get("preco_venda"))

    def clean_preco_custo(self):
        return limpar_dinheiro(self.cleaned_data.get("preco_custo"), minimo=Decimal("0"))

    def clean_estoque_minimo(self):
        return limpar_quantidade(self.cleaned_data.get("estoque_minimo"), permitir_zero=True) or Decimal("0")

    def clean_estoque_inicial(self):
        return limpar_quantidade(self.cleaned_data.get("estoque_inicial"), permitir_zero=True)

    def clean(self):
        dados = super().clean()
        unidade = dados.get("unidade")
        if unidade and unidade not in Produto.UNIDADES_FRACIONADAS:
            for campo in ("estoque_minimo", "estoque_inicial"):
                if campo in self.fields and not eh_inteiro(dados.get(campo)):
                    self.add_error(campo, "Para esta unidade, use uma quantidade inteira.")
        return dados


# --- Movimentos de um produto --------------------------------------------------

class EntradaForm(DataAteHojeMixin, BootstrapMixin, forms.Form):
    quantidade = campo_quantidade()
    custo_unitario = campo_dinheiro("Custo unitário", obrigatorio=False)
    data = campo_data()
    observacao = forms.CharField(label="Observação", max_length=200, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["quantidade"].widget.attrs["autofocus"] = True
        self.fields["custo_unitario"].help_text = "Opcional. Se informado, atualiza o preço de custo do produto."
        self.fields["observacao"].widget.attrs["placeholder"] = "Ex.: fornecedor, nota fiscal"

    def clean_quantidade(self):
        return limpar_quantidade(self.cleaned_data.get("quantidade"))

    def clean_custo_unitario(self):
        return limpar_dinheiro(self.cleaned_data.get("custo_unitario"), minimo=Decimal("0"))


class PerdaForm(DataAteHojeMixin, BootstrapMixin, forms.Form):
    quantidade = campo_quantidade()
    data = campo_data()
    observacao = forms.CharField(
        label="Motivo", max_length=200, error_messages={"required": "Informe o motivo da perda."}
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["quantidade"].widget.attrs["autofocus"] = True
        self.fields["observacao"].widget.attrs["placeholder"] = "Ex.: vencido, quebrado, estragado"

    def clean_quantidade(self):
        return limpar_quantidade(self.cleaned_data.get("quantidade"))


class AjusteForm(BootstrapMixin, forms.Form):
    quantidade = campo_quantidade("Quantidade contada", mensagem="Informe a quantidade contada.")
    observacao = forms.CharField(label="Observação", max_length=200, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["quantidade"].widget.attrs["autofocus"] = True
        self.fields["quantidade"].help_text = "Quantos existem de fato na loja agora. Pode ser 0."
        self.fields["observacao"].widget.attrs["placeholder"] = "Ex.: contagem mensal"

    def clean_quantidade(self):
        quantidade = limpar_quantidade(self.cleaned_data.get("quantidade"), permitir_zero=True)
        if quantidade is None:
            raise forms.ValidationError("Informe a quantidade contada.")
        return quantidade


# --- Itens de venda (venda à vista e venda a prazo com produtos) ---------------

class SelectProduto(forms.Select):
    """Inclui preço, unidade e estoque nas opções, para o JavaScript calcular o total."""

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        opcao = super().create_option(name, value, label, selected, index, subindex, attrs)
        produto = getattr(value, "instance", None)
        if produto is not None:
            opcao["attrs"].update(
                {
                    "data-preco-centavos": int(produto.preco_venda * 100),
                    "data-sigla": produto.sigla,
                    "data-fracionado": "1" if produto.fracionado else "0",
                    "data-estoque": f"{produto.quantidade:.3f}",
                    "data-estoque-texto": produto.formatar(produto.quantidade),
                }
            )
        return opcao


class CampoProduto(forms.ModelChoiceField):
    def label_from_instance(self, produto):
        return f"{produto.nome} — {formatar_moeda(produto.preco_venda)}/{produto.sigla}"


class ItemForm(BootstrapMixin, forms.Form):
    produto = CampoProduto(
        queryset=Produto.objects.none(),
        required=False,
        empty_label="Selecione o produto…",
        widget=SelectProduto,
        error_messages={"invalid_choice": "Produto não encontrado ou inativo."},
    )
    quantidade = campo_quantidade(obrigatorio=False)
    # Preço (em centavos) que estava na tela quando o comerciante conferiu o total.
    # Preenchido pelo JavaScript; se o preço mudou desde então, a venda é recusada.
    preco_exibido = forms.IntegerField(required=False, widget=forms.HiddenInput)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["produto"].queryset = Produto.objects.com_estoque().filter(ativo=True).order_by("nome")

    def clean_quantidade(self):
        return limpar_quantidade(self.cleaned_data.get("quantidade"))

    def clean(self):
        dados = super().clean()
        produto = dados.get("produto")
        quantidade = dados.get("quantidade")
        preco_exibido = dados.get("preco_exibido")
        if produto and preco_exibido is not None and preco_exibido != int(produto.preco_venda * 100):
            self.add_error(
                "produto",
                f"O preço de {produto.nome} mudou para {formatar_moeda(produto.preco_venda)}/{produto.sigla}. "
                "Confira o total e confirme novamente.",
            )
        if produto and quantidade is None and "quantidade" not in self.errors:
            self.add_error("quantidade", "Informe a quantidade.")
        if quantidade is not None and not produto and "produto" not in self.errors:
            self.add_error("produto", "Selecione o produto.")
        if produto and quantidade is not None and not produto.fracionado and not eh_inteiro(quantidade):
            self.add_error("quantidade", f"{produto.nome} é vendido por {produto.sigla}: use quantidade inteira.")
        return dados


class BaseItemFormSet(BaseFormSet):
    @property
    def preenchido(self):
        """Alguma linha foi preenchida (mesmo que com erro)? O preço oculto não conta."""
        return any(
            any(campo != "preco_exibido" for campo in form.changed_data) for form in self.forms
        )

    @property
    def itens(self):
        """Itens válidos (usar depois de is_valid())."""
        return [
            Item(form.cleaned_data["produto"], form.cleaned_data["quantidade"])
            for form in self.forms
            if form.cleaned_data.get("produto") and form.cleaned_data.get("quantidade")
        ]


ItemFormSet = formset_factory(ItemForm, formset=BaseItemFormSet, extra=1, max_num=50, validate_max=True)
PREFIXO_ITENS = "itens"


def criar_formset_itens(request):
    """Formset de itens a partir do POST; None se o formulário não trouxe a seção de produtos."""
    if request.method == "POST":
        if f"{PREFIXO_ITENS}-TOTAL_FORMS" not in request.POST:
            return None
        return ItemFormSet(request.POST, prefix=PREFIXO_ITENS)
    return ItemFormSet(prefix=PREFIXO_ITENS)


class VendaAVistaForm(DataAteHojeMixin, BootstrapMixin, forms.Form):
    data = campo_data()
    observacao = forms.CharField(label="Observação", max_length=200, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["observacao"].widget.attrs["placeholder"] = "Opcional"
