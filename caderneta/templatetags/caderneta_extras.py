from django import template
from django.utils import timezone

from caderneta.utils import formatar_moeda, formatar_telefone

register = template.Library()


@register.filter
def moeda(valor):
    """{{ valor|moeda }} -> R$ 1.234,56"""
    return formatar_moeda(valor)


@register.filter
def telefone(valor):
    """{{ cliente.telefone|telefone }} -> (48) 99999-1234"""
    return formatar_telefone(valor) if valor else ""


@register.filter
def quantidade(valor, produto):
    """{{ produto.quantidade|quantidade:produto }} -> 12 un / 0,35 kg"""
    return produto.formatar(valor or 0)


@register.filter
def tempo_desde(data):
    """{{ data|tempo_desde }} -> hoje / ontem / há 12 dias"""
    if not data:
        return ""
    dias = (timezone.localdate() - data).days
    if dias <= 0:
        return "hoje"
    if dias == 1:
        return "ontem"
    return f"há {dias} dias"
