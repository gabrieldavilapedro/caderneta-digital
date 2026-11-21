from django.contrib import admin

from .models import Cliente, Movimentacao


@admin.register(Cliente)
class ClienteAdmin(admin.ModelAdmin):
    list_display = ("nome", "telefone", "ativo", "criado_em")
    list_filter = ("ativo",)
    search_fields = ("nome", "telefone")


@admin.register(Movimentacao)
class MovimentacaoAdmin(admin.ModelAdmin):
    """Somente leitura: lançamentos só podem ser criados/cancelados pela
    aplicação, que aplica as regras de negócio (caderneta/services.py)."""

    list_display = ("data", "cliente", "tipo", "valor", "cancelada", "criado_em")
    list_filter = ("tipo", "cancelada")
    search_fields = ("cliente__nome", "descricao")
    date_hierarchy = "data"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
