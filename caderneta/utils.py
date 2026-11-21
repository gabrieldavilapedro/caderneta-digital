"""Funções auxiliares de formatação e leitura de números (padrão brasileiro)."""
import re
from decimal import Decimal, InvalidOperation

# "1.234" ou "12.345.678". Um grupo começando em "0." ("0.350") nunca é milhar.
_MILHAR_COM_PONTO = re.compile(r"^[1-9]\d{0,2}(\.\d{3})+$")
CENTAVO = Decimal("0.01")


def formatar_numero(valor, casas=2, aparar=False):
    """Decimal('1234.5') -> '1.234,50'. Com aparar=True remove zeros finais: '1,5'."""
    texto = f"{Decimal(valor or 0):,.{casas}f}"
    texto = texto.replace(",", "_").replace(".", ",").replace("_", ".")
    if aparar and "," in texto:
        texto = texto.rstrip("0").rstrip(",")
    return texto


def formatar_moeda(valor):
    """Decimal('1234.5') -> 'R$ 1.234,50'."""
    texto = formatar_numero(valor, 2)
    if texto.startswith("-"):
        return f"-R$ {texto[1:]}"
    return f"R$ {texto}"


def formatar_quantidade(quantidade, sigla, fracionado):
    """12 -> '12 un'; 0.35 -> '0,35 kg'."""
    return f"{formatar_numero(quantidade, 3 if fracionado else 0, aparar=True)} {sigla}"


def formatar_para_campo(valor, casas=2, aparar=False):
    """Valor para preencher um campo de formulário: sem separador de milhar ('1234,50')."""
    if valor is None:
        return ""
    texto = f"{Decimal(valor):.{casas}f}".replace(".", ",")
    if aparar and "," in texto:
        texto = texto.rstrip("0").rstrip(",")
    return texto


def ler_decimal(texto, casas=2):
    """Converte o número digitado em Decimal.

    Aceita "12,50", "12.50", "1.234,56", "1234" e "R$ 12,50".
    Lança ValueError se o formato for inválido ou tiver mais casas decimais que o permitido.

    Ponto sem vírgula: em dinheiro (2 casas), "1.234" só pode ser milhar. Em quantidades
    (3 casas), um único ponto é decimal: "0.350" kg = 0,350 kg e "1.500" kg = 1,5 kg.
    """
    limpo = re.sub(r"R\$|\s", "", str(texto or ""))
    if not limpo:
        raise ValueError("Valor vazio.")
    if "," in limpo:
        limpo = limpo.replace(".", "").replace(",", ".")
    elif _MILHAR_COM_PONTO.match(limpo) and (casas < 3 or limpo.count(".") > 1):
        limpo = limpo.replace(".", "")
    if not re.fullmatch(rf"\d+(\.\d{{1,{casas}}})?", limpo):
        raise ValueError("Formato inválido.")
    try:
        return Decimal(limpo).quantize(Decimal(1).scaleb(-casas))
    except InvalidOperation as erro:
        raise ValueError("Formato inválido.") from erro


def ler_valor(texto):
    """Valor em dinheiro: até 2 casas decimais."""
    return ler_decimal(texto, 2)


def ler_quantidade(texto):
    """Quantidade de estoque: até 3 casas decimais (ex.: 0,350 kg)."""
    return ler_decimal(texto, 3)


def somente_digitos(texto):
    return re.sub(r"\D", "", str(texto or ""))


def formatar_telefone(digitos):
    """'48999991234' -> '(48) 99999-1234'; '4833331234' -> '(48) 3333-1234'."""
    d = somente_digitos(digitos)
    if len(d) == 11:
        return f"({d[:2]}) {d[2:7]}-{d[7:]}"
    if len(d) == 10:
        return f"({d[:2]}) {d[2:6]}-{d[6:]}"
    return d
