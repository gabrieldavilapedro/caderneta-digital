import re
from datetime import datetime

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import F, Q
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.utils.functional import cached_property
from django.views import View
from django.views.generic import CreateView, DetailView, FormView, ListView, TemplateView, UpdateView

from estoque.forms import PREFIXO_ITENS, ItemFormSet, criar_formset_itens
from estoque.models import Produto

from . import services
from .forms import CancelamentoForm, ClienteForm, LancamentoForm
from .models import Cliente, Movimentacao
from .services import RegraDeNegocioError
from .utils import formatar_moeda, somente_digitos

VENDA = Movimentacao.Tipo.VENDA
PAGAMENTO = Movimentacao.Tipo.PAGAMENTO

SITUACOES = [
    ("ativos", "Ativos"),
    ("com_debito", "Com débito"),
    ("em_dia", "Em dia"),
    ("inativos", "Inativos"),
    ("todos", "Todos"),
]
ORDENACOES = [
    ("nome", "Nome (A–Z)"),
    ("saldo", "Maior saldo"),
    ("recentes", "Lançamento mais recente"),
]
_SO_TELEFONE = re.compile(r"^[\d\s()+-]+$")


def filtrar_por_busca(queryset, busca):
    """Busca por parte do nome ou, se o texto for numérico, pelo telefone."""
    busca = (busca or "").strip()
    if not busca:
        return queryset
    filtro = Q(nome__icontains=busca)
    digitos = somente_digitos(busca)
    if digitos and _SO_TELEFONE.match(busca):
        filtro |= Q(telefone__contains=digitos)
    return queryset.filter(filtro)


class MenuMixin(LoginRequiredMixin):
    """Exige login e informa ao menu qual item destacar."""

    menu = None

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["menu_ativo"] = self.menu
        return contexto


# --- Painel ------------------------------------------------------------------

class PainelView(MenuMixin, TemplateView):
    template_name = "caderneta/painel.html"
    menu = "inicio"

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["resumo"] = services.resumo_geral()
        return contexto


# --- Clientes (RF001, RF004, RF005) ------------------------------------------

class ClienteListView(MenuMixin, ListView):
    template_name = "caderneta/clientes/lista.html"
    context_object_name = "clientes"
    paginate_by = 25
    page_kwarg = "pagina"
    menu = "clientes"

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
        queryset = filtrar_por_busca(Cliente.objects.com_saldo(), filtros["busca"])

        situacao = filtros["situacao"]
        if situacao == "ativos":
            queryset = queryset.filter(ativo=True)
        elif situacao == "inativos":
            queryset = queryset.filter(ativo=False)
        elif situacao == "com_debito":
            queryset = queryset.filter(saldo__gt=0)
        elif situacao == "em_dia":
            queryset = queryset.filter(ativo=True, saldo__lte=0)

        if filtros["ordenar"] == "saldo":
            return queryset.order_by("-saldo", "nome")
        if filtros["ordenar"] == "recentes":
            return queryset.order_by(F("ultima_movimentacao").desc(nulls_last=True), "nome")
        return queryset.order_by("nome", "id")

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto.update(filtros=self.filtros, situacoes=SITUACOES, ordenacoes=ORDENACOES)
        return contexto


class ClienteDetailView(MenuMixin, DetailView):
    template_name = "caderneta/clientes/detalhe.html"
    context_object_name = "cliente"
    menu = "clientes"

    def get_queryset(self):
        return Cliente.objects.com_saldo()

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["faturas"] = services.faturas_do_cliente(self.object)
        contexto["historico_meses"] = services.historico_por_mes(self.object)
        return contexto


class ExtratoView(MenuMixin, DetailView):
    """Extrato para impressão ou para mostrar ao cliente (transparência).

    Sem parâmetro: todos os lançamentos. Com ?mes=AAAA-MM: a fatura daquele mês,
    com as compras do mês, os pagamentos descontados dela e quanto falta pagar.
    """

    template_name = "caderneta/clientes/extrato.html"
    context_object_name = "cliente"
    menu = "clientes"

    def get_queryset(self):
        return Cliente.objects.com_saldo()

    def mes_pedido(self):
        try:
            return datetime.strptime(self.request.GET.get("mes", ""), "%Y-%m").date()
        except ValueError:
            return None

    def get(self, request, *args, **kwargs):
        self.object = self.get_object()
        self.fatura, self.compras = (None, [])
        mes = self.mes_pedido()
        if mes:
            self.fatura, self.compras = services.fatura_do_mes(self.object, mes)
            if self.fatura is None:
                messages.info(request, "Este cliente não tem fatura no mês escolhido. Mostrando o extrato completo.")
                return redirect("caderneta:cliente_extrato", pk=self.object.pk)
        return self.render_to_response(self.get_context_data(object=self.object))

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        cliente = self.object
        if self.fatura:
            contexto.update(fatura=self.fatura, compras=self.compras)
        else:
            contexto.update(
                movimentacoes=list(reversed(services.historico_do_cliente(cliente))),  # ordem cronológica
                total_compras=cliente.total_vendas,
                total_pagamentos=cliente.total_pagamentos,
                saldo_final=cliente.saldo,
            )
        contexto["faturas"] = services.faturas_do_cliente(cliente)
        contexto["emitido_em"] = timezone.localtime()
        return contexto


class ClienteCreateView(MenuMixin, CreateView):
    model = Cliente
    form_class = ClienteForm
    template_name = "caderneta/clientes/form.html"
    menu = "clientes"

    def form_valid(self, form):
        self.object = form.save()
        messages.success(self.request, f"Cliente {self.object.nome} cadastrado.")
        if self.request.GET.get("depois") == "venda":
            return redirect("caderneta:venda", pk=self.object.pk)
        return redirect("caderneta:cliente", pk=self.object.pk)


class ClienteUpdateView(MenuMixin, UpdateView):
    model = Cliente
    form_class = ClienteForm
    template_name = "caderneta/clientes/form.html"
    context_object_name = "cliente"
    menu = "clientes"

    def form_valid(self, form):
        self.object = form.save()
        messages.success(self.request, "Cadastro atualizado.")
        return redirect("caderneta:cliente", pk=self.object.pk)


class AlternarSituacaoView(LoginRequiredMixin, View):
    """Inativa ou reativa o cliente. Inativo não aparece na lista padrão e
    não recebe novas vendas, mas o histórico e o saldo são mantidos."""

    http_method_names = ["post"]

    def post(self, request, pk):
        cliente = get_object_or_404(Cliente, pk=pk)
        cliente.ativo = not cliente.ativo
        cliente.save(update_fields=["ativo", "atualizado_em"])
        situacao = "reativado" if cliente.ativo else "inativado"
        messages.success(request, f"Cadastro de {cliente.nome} {situacao}.")
        return redirect("caderneta:cliente", pk=cliente.pk)


class ClienteExcluirView(MenuMixin, DetailView):
    """Exclusão só é permitida para cliente sem lançamentos (cadastro feito
    por engano). Quem tem histórico deve ser inativado."""

    model = Cliente
    template_name = "caderneta/clientes/excluir.html"
    context_object_name = "cliente"
    menu = "clientes"

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["possui_lancamentos"] = self.object.movimentacoes.exists()
        return contexto

    def post(self, request, *args, **kwargs):
        cliente = self.get_object()
        if cliente.movimentacoes.exists():
            messages.error(request, "Este cliente possui lançamentos e não pode ser excluído. Você pode inativá-lo.")
            return redirect("caderneta:cliente", pk=cliente.pk)
        nome = cliente.nome
        cliente.delete()
        messages.success(request, f"Cliente {nome} excluído.")
        return redirect("caderneta:clientes")


# --- Lançamentos (RF002, RF003, RF006) ---------------------------------------

class EscolherClienteView(MenuMixin, ListView):
    """Etapa 1 do lançamento: escolher o cliente."""

    template_name = "caderneta/lancamentos/escolher_cliente.html"
    context_object_name = "clientes"
    paginate_by = 20
    page_kwarg = "pagina"
    tipo = None

    def get_queryset(self):
        queryset = Cliente.objects.com_saldo()
        if self.tipo == VENDA:
            queryset = queryset.filter(ativo=True)
        else:
            queryset = queryset.filter(saldo__gt=0)
        return filtrar_por_busca(queryset, self.request.GET.get("busca")).order_by("nome", "id")

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto.update(
            eh_venda=self.tipo == VENDA,
            busca=self.request.GET.get("busca", "").strip(),
            destino="caderneta:venda" if self.tipo == VENDA else "caderneta:pagamento",
        )
        return contexto


class NovoLancamentoView(MenuMixin, FormView):
    """Etapa 2 do lançamento: informar valor, data e descrição e confirmar."""

    template_name = "caderneta/lancamentos/form.html"
    form_class = LancamentoForm
    menu = "clientes"
    tipo = None

    @cached_property
    def cliente(self):
        return get_object_or_404(Cliente.objects.com_saldo(), pk=self.kwargs["pk"])

    @property
    def eh_venda(self):
        return self.tipo == VENDA

    @cached_property
    def opcoes_pagamento(self):
        """Meses em aberto que o comerciante pode escolher ao registrar um pagamento."""
        return [] if self.eh_venda else services.meses_em_aberto(self.cliente)

    def get_initial(self):
        # ?ate=AAAA-MM (link de um mês na tela de pagamento) já preenche valor e descrição.
        inicial = super().get_initial()
        escolhido = self.request.GET.get("ate")
        for opcao in self.opcoes_pagamento:
            if opcao.parametro == escolhido:
                inicial.update(valor=opcao.valor_campo, descricao=opcao.descricao)
        return inicial

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["tipo"] = self.tipo
        # Com produtos do estoque, o valor é calculado e não precisa ser digitado.
        formset = getattr(self, "formset", None)
        kwargs["exige_valor"] = not (formset is not None and formset.preenchido)
        return kwargs

    def post(self, request, *args, **kwargs):
        # Produtos do estoque são opcionais e só existem na venda.
        self.formset = criar_formset_itens(request) if self.eh_venda else None
        form = self.get_form()
        formset_ok = self.formset is None or self.formset.is_valid()
        if form.is_valid() and formset_ok:
            return self.form_valid(form)
        return self.form_invalid(form)

    def motivo_de_bloqueio(self):
        if self.eh_venda and not self.cliente.ativo:
            return "Este cliente está inativo. Reative o cadastro para registrar novas vendas."
        if not self.eh_venda and self.cliente.saldo <= 0:
            return "Este cliente não possui saldo devedor, então não há pagamento a registrar."
        return ""

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        saldo = self.cliente.saldo
        contexto.update(
            cliente=self.cliente,
            tipo=self.tipo,
            eh_venda=self.eh_venda,
            bloqueio=self.motivo_de_bloqueio(),
            saldo_centavos=int(saldo * 100),
            saldo_texto=f"{saldo:.2f}".replace(".", ","),
            opcoes_pagamento=self.opcoes_pagamento,
            mes_escolhido=self.request.GET.get("ate", ""),
        )
        if self.eh_venda:
            formset = getattr(self, "formset", None) or ItemFormSet(prefix=PREFIXO_ITENS)
            contexto.update(
                formset=formset,
                tem_produtos=Produto.objects.filter(ativo=True).exists(),
                produtos_abertos=formset.is_bound and formset.preenchido,
            )
        return contexto

    def form_valid(self, form):
        itens = self.formset.itens if getattr(self, "formset", None) is not None else []
        dados = form.cleaned_data
        try:
            movimentacao = services.registrar_movimentacao(
                cliente=self.cliente,
                tipo=self.tipo,
                valor=dados.get("valor"),
                data=dados["data"],
                descricao=dados.get("descricao", ""),
                itens=itens,
            )
        except RegraDeNegocioError as erro:
            form.add_error(erro.campo if erro.campo in form.fields else None, erro.mensagem)
            return self.form_invalid(form)

        novo_saldo = services.saldo_do_cliente(self.cliente.pk)
        descricao = "Venda" if self.eh_venda else "Pagamento"
        registrada = "registrada" if self.eh_venda else "registrado"
        baixa = ""
        if itens:
            quantidade = movimentacao.itens.count()  # produtos distintos (repetidos são somados)
            baixa = f" ({quantidade} produto{'s' if quantidade > 1 else ''} baixado{'s' if quantidade > 1 else ''} do estoque)"
        messages.success(
            self.request,
            f"{descricao} de {formatar_moeda(movimentacao.valor)} {registrada} para {self.cliente.nome}{baixa}. "
            f"Novo saldo: {formatar_moeda(novo_saldo)}.",
        )
        return redirect("caderneta:cliente", pk=self.cliente.pk)


class CancelarLancamentoView(MenuMixin, FormView):
    """Cancela (estorna) um lançamento registrado por engano."""

    template_name = "caderneta/lancamentos/cancelar.html"
    form_class = CancelamentoForm
    menu = "clientes"

    @cached_property
    def movimentacao(self):
        return get_object_or_404(
            Movimentacao.objects.select_related("cliente").prefetch_related("itens__produto"), pk=self.kwargs["pk"]
        )

    def get(self, request, *args, **kwargs):
        if self.movimentacao.cancelada:
            messages.info(request, "Este lançamento já está cancelado.")
            return redirect("caderneta:cliente", pk=self.movimentacao.cliente_id)
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto.update(movimentacao=self.movimentacao, cliente=self.movimentacao.cliente)
        return contexto

    def form_valid(self, form):
        try:
            services.cancelar_movimentacao(self.movimentacao, form.cleaned_data["motivo"])
        except RegraDeNegocioError as erro:
            form.add_error(erro.campo if erro.campo in form.fields else None, erro.mensagem)
            return self.form_invalid(form)
        devolvidos = " Os produtos voltaram ao estoque." if self.movimentacao.itens.exists() else ""
        messages.success(self.request, f"Lançamento cancelado. O saldo foi recalculado.{devolvidos}")
        return redirect("caderneta:cliente", pk=self.movimentacao.cliente_id)
