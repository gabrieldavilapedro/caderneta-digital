from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView

admin.site.site_header = "Caderneta Digital — administração"
admin.site.site_title = "Caderneta Digital"
admin.site.index_title = "Administração"

urlpatterns = [
    path("admin/", admin.site.urls),
    # O sistema não tem mais login: endereços antigos (favoritos) levam direto ao painel.
    path("entrar/", RedirectView.as_view(pattern_name="caderneta:painel", permanent=False)),
    path("primeiro-acesso/", RedirectView.as_view(pattern_name="caderneta:painel", permanent=False)),
    path("sair/", RedirectView.as_view(pattern_name="caderneta:painel", permanent=False)),
    path("estoque/", include("estoque.urls")),
    path("", include("caderneta.urls")),
]
