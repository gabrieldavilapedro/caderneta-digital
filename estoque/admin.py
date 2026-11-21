from django.contrib import admin

from .models import MovimentoEstoque, Produto


@admin.register(Produto)
class ProdutoAdmin(admin.ModelAdmin):
    list_display = ("nome", "codigo", "unidade", "preco_venda", "estoque_minimo", "ativo")
    list_filter = ("ativo", "unidade")
    search_fields = ("nome", "codigo")


@admin.register(MovimentoEstoque)
class MovimentoEstoqueAdmin(admin.ModelAdmin):
    """Somente leitura: movimentos só são criados/cancelados pela aplicação,
    que aplica as regras de negócio (estoque/services.py)."""

    list_display = ("data", "produto", "tipo", "quantidade", "preco_unitario", "cancelado", "criado_em")
    list_filter = ("tipo", "cancelado")
    search_fields = ("produto__nome", "observacao")
    date_hierarchy = "data"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
