"""Cria o arquivo .env na primeira execução, com chave secreta e senha do banco aleatórias.

Usado por iniciar.sh e iniciar.bat. Se o .env já existir, não faz nada.
"""
import secrets
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
ENV = RAIZ / ".env"
EXEMPLO = RAIZ / ".env.example"


def main():
    if ENV.exists():
        return
    # token_urlsafe gera só letras, números, "-" e "_": seguro para o Docker Compose e o dotenv.
    valores = {
        "DJANGO_SECRET_KEY": secrets.token_urlsafe(50),
        "POSTGRES_PASSWORD": secrets.token_urlsafe(18),
        "DJANGO_DEBUG": "False",  # uso diário: não mostra detalhes técnicos de erros na tela
    }
    linhas = []
    for linha in EXEMPLO.read_text(encoding="utf-8").splitlines():
        chave = linha.split("=", 1)[0]
        if chave in valores and not linha.startswith("#"):
            linha = f"{chave}={valores[chave]}"
        linhas.append(linha)
    ENV.write_text("\n".join(linhas) + "\n", encoding="utf-8")
    try:
        ENV.chmod(0o600)
    except OSError:
        pass
    print("Arquivo .env criado com chave secreta e senha do banco aleatórias.")


if __name__ == "__main__":
    sys.exit(main())
