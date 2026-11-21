from django.contrib import messages
from django.db import transaction
from django.db.models import F, Q
from django.shortcuts import get_object_or_404, redirect
from django.utils.functional import cached_property
from django.views import View
from django.views.generic import CreateView, DetailView, FormView, ListView, UpdateView

from caderneta.excecoes import RegraDeNegocioError
from caderneta.forms import CancelamentoForm
from caderneta.services import paginar
from caderneta.utils import formatar_moeda
from caderneta.views import MenuMixin

from . import services
from .forms import (
    PREFIXO_ITENS,
    AjusteForm,
    EntradaForm,
    ItemFormSet,
    PerdaForm,
    ProdutoForm,
    VendaAVistaForm,
    criar_formset_itens,
)
from .models import MovimentoEstoque, Produto

SITUACOES = [
    ("ativos", "Ativos"),
    ("estoque_baixo", "Estoque baixo"),
    ("sem_estoque", "Sem estoque"),
    ("inativos", "Inativos"),
    ("todos", "Todos"),
]
ORDENACOES = [
    ("nome", "Nome (A–Z)"),
    ("menor_estoque", "Menor estoque"),
    ("recentes", "Movimento mais recente"),
]


def filtrar_produtos(queryset, busca):
    busca = (busca or "").strip()
    if not busca:
        return queryset
    return queryset.filter(Q(nome__icontains=busca) | Q(codigo__icontains=busca))


def aplicar_erro(form, erro):
    form.add_error(erro.campo if erro.campo in form.fields else None, erro.mensagem)


class EstoqueMixin(MenuMixin):
    menu = "estoque"


# --- Produtos ----------------------------------------------------------------

class ProdutoListView(EstoqueMixin, ListView):
    template_name = "estoque/lista.html"
    context_object_name = "produtos"
    paginate_by = 25
    page_kwarg = "pagina"

    @cached_property
    def filtros(self):
        parametros = self.request.GET
        situacao = parametros.get("situacao", "ativos")
        ordenar = parametros.get("ordenar", "nome")
        return {
            "busca": parametros.get("busca", "").strip(),
            "situacao": situacao if situacao in dict(SITUACOES) else "ativos",
            "ordenar": ordenar if ordenar in dict(ORDENACOES) else "nome",
        }

    def get_queryset(self):
        filtros = self.filtros
        queryset = filtrar_produtos(Produto.objects.com_estoque(), filtros["busca"])
        situacao = filtros["situacao"]
        if situacao == "ativos":
            queryset = queryset.filter(ativo=True)
        elif situacao == "estoque_baixo":
            queryset = queryset.filter(ativo=True, quantidade__lte=F("estoque_minimo"))
        elif situacao == "sem_estoque":
            queryset = queryset.filter(ativo=True, quantidade__lte=0)
        elif situacao == "inativos":
            queryset = queryset.filter(ativo=False)

        if filtros["ordenar"] == "menor_estoque":
            return queryset.order_by("quantidade", "nome")
        if filtros["ordenar"] == "recentes":
            return queryset.order_by(F("ultimo_movimento").desc(nulls_last=True), "nome")
        return queryset.order_by("nome", "id")

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto.update(
            filtros=self.filtros,
            situacoes=SITUACOES,
            ordenacoes=ORDENACOES,
            resumo=services.resumo_estoque(),
        )
        return contexto


class ProdutoDetailView(EstoqueMixin, DetailView):
    template_name = "estoque/produto_detalhe.html"
    context_object_name = "produto"

    def get_queryset(self):
        return Produto.objects.com_estoque()

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["historico"] = paginar(
            services.historico_do_produto(self.object), self.request.GET.get("pagina"), por_pagina=20
        )
        return contexto


class ProdutoCreateView(EstoqueMixin, CreateView):
    model = Produto
    form_class = ProdutoForm
    template_name = "estoque/produto_form.html"

    def form_valid(self, form):
        try:
            with transaction.atomic():
                self.object = form.save()
                estoque_inicial = form.cleaned_data.get("estoque_inicial")
                if estoque_inicial:
                    services.registrar_entrada(
                        produto=self.object,
                        quantidade=estoque_inicial,
                        custo_unitario=self.object.preco_custo,
                        observacao="Estoque inicial",
                    )
        except RegraDeNegocioError as erro:
            self.object = None
            form.add_error(None, erro.mensagem)
            return self.form_invalid(form)
        messages.success(self.request, f"Produto {self.object.nome} cadastrado.")
        return redirect("estoque:produto", pk=self.object.pk)


class ProdutoUpdateView(EstoqueMixin, UpdateView):
    model = Produto
    form_class = ProdutoForm
    template_name = "estoque/produto_form.html"
    context_object_name = "produto"

    def form_valid(self, form):
        self.object = form.save()
        messages.success(self.request, "Produto atualizado.")
        return redirect("estoque:produto", pk=self.object.pk)


class AlternarSituacaoProdutoView(View):
    http_method_names = ["post"]

    def post(self, request, pk):
        produto = get_object_or_404(Produto, pk=pk)
        produto.ativo = not produto.ativo
        produto.save(update_fields=["ativo", "atualizado_em"])
        messages.success(request, f"Produto {produto.nome} {'reativado' if produto.ativo else 'inativado'}.")
        return redirect("estoque:produto", pk=produto.pk)


class ProdutoExcluirView(EstoqueMixin, DetailView):
    """Exclusão só para produto sem movimentos (cadastro feito por engano)."""

    model = Produto
    template_name = "estoque/produto_excluir.html"
    context_object_name = "produto"

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["possui_movimentos"] = self.object.movimentos.exists()
        return contexto

    def post(self, request, *args, **kwargs):
        produto = self.get_object()
        if produto.movimentos.exists():
            messages.error(request, "Este produto possui movimentos e não pode ser excluído. Você pode inativá-lo.")
            return redirect("estoque:produto", pk=produto.pk)
        nome = produto.nome
        produto.delete()
        messages.success(request, f"Produto {nome} excluído.")
        return redirect("estoque:produtos")


class EscolherProdutoEntradaView(EstoqueMixin, ListView):
    """Etapa 1 da entrada de mercadoria: escolher o produto."""

    template_name = "estoque/escolher_produto.html"
    context_object_name = "produtos"
    paginate_by = 20
    page_kwarg = "pagina"

    def get_queryset(self):
        queryset = Produto.objects.com_estoque().filter(ativo=True)
        return filtrar_produtos(queryset, self.request.GET.get("busca")).order_by("nome", "id")

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["busca"] = self.request.GET.get("busca", "").strip()
        return contexto


# --- Movimentos de um produto: entrada, perda e ajuste ------------------------

class MovimentoProdutoView(EstoqueMixin, FormView):
    template_name = "estoque/movimento_form.html"
    titulo = ""
    icone = ""
    cor = "primary"
    explicacao = ""
    botao = "Confirmar"

    @cached_property
    def produto(self):
        return get_object_or_404(Produto.objects.com_estoque(), pk=self.kwargs["pk"])

    def motivo_de_bloqueio(self):
        return ""

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto.update(
            produto=self.produto,
            titulo=self.titulo,
            icone=self.icone,
            cor=self.cor,
            explicacao=self.explicacao,
            botao=self.botao,
            bloqueio=self.motivo_de_bloqueio(),
        )
        return contexto

    def form_valid(self, form):
        try:
            mensagem = self.executar(form.cleaned_data)
        except RegraDeNegocioError as erro:
            aplicar_erro(form, erro)
            return self.form_invalid(form)
        messages.success(self.request, mensagem)
        return redirect("estoque:produto", pk=self.produto.pk)

    def novo_estoque(self):
        produto = self.produto
        return produto.formatar(services.estoque_do_produto(produto.pk))


class EntradaView(MovimentoProdutoView):
    form_class = EntradaForm
    titulo = "Registrar entrada"
    icone = "bi-box-arrow-in-down"
    explicacao = "Mercadoria que chegou na loja (compra do fornecedor, reposição)."
    botao = "Confirmar entrada"

    def motivo_de_bloqueio(self):
        if not self.produto.ativo:
            return "Este produto está inativo. Reative o cadastro para registrar entradas."
        return ""

    def executar(self, dados):
        movimento = services.registrar_entrada(
            produto=self.produto,
            quantidade=dados["quantidade"],
            custo_unitario=dados.get("custo_unitario"),
            data=dados["data"],
            observacao=dados.get("observacao"),
        )
        return (
            f"Entrada de {self.produto.formatar(movimento.quantidade)} de {self.produto.nome} registrada. "
            f"Estoque atual: {self.novo_estoque()}."
        )


class PerdaView(MovimentoProdutoView):
    form_class = PerdaForm
    titulo = "Registrar perda"
    icone = "bi-trash3"
    cor = "danger"
    explicacao = "Produto que saiu do estoque sem ser vendido: vencido, quebrado, estragado ou consumido."
    botao = "Confirmar perda"

    def motivo_de_bloqueio(self):
        if self.produto.quantidade <= 0:
            return "Este produto está sem estoque; não há o que registrar como perda."
        return ""

    def executar(self, dados):
        movimento = services.registrar_perda(
            produto=self.produto, quantidade=dados["quantidade"], motivo=dados["observacao"], data=dados["data"]
        )
        return (
            f"Perda de {self.produto.formatar(movimento.quantidade_absoluta)} de {self.produto.nome} registrada. "
            f"Estoque atual: {self.novo_estoque()}."
        )


class AjusteView(MovimentoProdutoView):
    form_class = AjusteForm
    titulo = "Ajustar estoque"
    icone = "bi-clipboard-check"
    cor = "secondary"
    explicacao = (
        "Conte o produto na prateleira e informe a quantidade encontrada. "
        "A diferença para o estoque do sistema é registrada como ajuste."
    )
    botao = "Confirmar contagem"

    def executar(self, dados):
        movimento = services.ajustar_estoque(
            produto=self.produto, quantidade_contada=dados["quantidade"], observacao=dados.get("observacao")
        )
        if movimento is None:
            return "A contagem confere com o estoque do sistema. Nenhum ajuste foi necessário."
        sinal = "+" if movimento.entra else "−"
        return (
            f"Estoque ajustado ({sinal}{self.produto.formatar(movimento.quantidade_absoluta)}). "
            f"Estoque atual: {self.novo_estoque()}."
        )


# --- Venda à vista ------------------------------------------------------------

class VendaAVistaView(EstoqueMixin, FormView):
    """Venda paga na hora: baixa o estoque, não entra na caderneta."""

    template_name = "estoque/venda_avista.html"
    form_class = VendaAVistaForm

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["formset"] = getattr(self, "formset", None) or ItemFormSet(prefix=PREFIXO_ITENS)
        contexto["tem_produtos"] = Produto.objects.filter(ativo=True).exists()
        return contexto

    def post(self, request, *args, **kwargs):
        form = self.get_form()
        self.formset = criar_formset_itens(request)
        formset_ok = self.formset is not None and self.formset.is_valid()
        if not (form.is_valid() and formset_ok):
            return self.form_invalid(form)
        itens = self.formset.itens
        if not itens:
            form.add_error(None, "Adicione pelo menos um produto com a quantidade.")
            return self.form_invalid(form)
        try:
            movimentos = services.registrar_venda_a_vista(
                itens, data=form.cleaned_data["data"], observacao=form.cleaned_data.get("observacao")
            )
        except RegraDeNegocioError as erro:
            form.add_error(None, erro.mensagem)
            return self.form_invalid(form)
        total = sum(movimento.total for movimento in movimentos)
        quantidade = len(movimentos)
        messages.success(
            request,
            f"Venda à vista registrada: {quantidade} produto{'s' if quantidade > 1 else ''}, "
            f"total {formatar_moeda(total)}. Estoque atualizado.",
        )
        return redirect("estoque:produtos")


# --- Cancelamento -------------------------------------------------------------

class CancelarMovimentoView(EstoqueMixin, FormView):
    template_name = "estoque/cancelar_movimento.html"
    form_class = CancelamentoForm

    @cached_property
    def movimento(self):
        return get_object_or_404(
            MovimentoEstoque.objects.select_related("produto", "venda_a_prazo__cliente"), pk=self.kwargs["pk"]
        )

    def get(self, request, *args, **kwargs):
        movimento = self.movimento
        if movimento.cancelado:
            messages.info(request, "Este movimento já está cancelado.")
            return redirect("estoque:produto", pk=movimento.produto_id)
        if movimento.venda_a_prazo_id:
            messages.info(
                request,
                "Este item faz parte de uma venda a prazo. Cancele a venda no histórico do cliente "
                "para devolver o produto ao estoque.",
            )
            return redirect("caderneta:cliente", pk=movimento.venda_a_prazo.cliente_id)
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto.update(movimento=self.movimento, produto=self.movimento.produto)
        return contexto

    def form_valid(self, form):
        try:
            services.cancelar_movimento(self.movimento, form.cleaned_data["motivo"])
        except RegraDeNegocioError as erro:
            aplicar_erro(form, erro)
            return self.form_invalid(form)
        messages.success(self.request, "Movimento cancelado. O estoque foi recalculado.")
        return redirect("estoque:produto", pk=self.movimento.produto_id)
