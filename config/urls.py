from django.contrib import admin
from django.urls import include, path

admin.site.site_header = "Caderneta Digital — administração"
admin.site.site_title = "Caderneta Digital"
admin.site.index_title = "Administração"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("contas.urls")),
    path("estoque/", include("estoque.urls")),
    path("", include("caderneta.urls")),
]
