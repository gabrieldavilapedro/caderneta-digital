#!/usr/bin/env bash
# Caderneta Digital — inicia tudo com um clique: banco (Docker), sistema e navegador.
# Na primeira vez também prepara o ambiente (venv, dependências, .env e tabelas do banco).
#
# Uso: ./iniciar.sh   (ou pelo atalho criado com scripts/criar_atalho_linux.sh)
# Opcional: PORTA=8000 (padrão)  ABRIR_NAVEGADOR=0 (não abre o navegador)
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

PORTA="${PORTA:-8000}"
ENDERECO="http://localhost:${PORTA}"
ABRIR_NAVEGADOR="${ABRIR_NAVEGADOR:-1}"

porta_em_uso() { (exec 3<>"/dev/tcp/127.0.0.1/${PORTA}") 2>/dev/null; }
abrir_navegador() {
  [[ "$ABRIR_NAVEGADOR" == "1" ]] || return 0
  (xdg-open "$ENDERECO" || open "$ENDERECO") >/dev/null 2>&1 &
}
falhar() {
  echo
  echo "ERRO: $1" >&2
  read -r -p "Pressione Enter para fechar..." _ || true
  exit 1
}

echo "== Caderneta Digital =="

if porta_em_uso; then
  echo "O sistema já está em execução em $ENDERECO."
  abrir_navegador
  exit 0
fi

command -v docker >/dev/null || falhar "Docker não encontrado. Instale o Docker e tente de novo."
docker info >/dev/null 2>&1 || falhar "O Docker não está em execução. Inicie o Docker e tente de novo."

# 1. Primeira execução: ambiente Python, dependências e configuração (.env)
if [[ ! -x .venv/bin/python ]]; then
  echo "Preparando o ambiente Python (somente na primeira vez; precisa de internet)..."
  python3 -m venv .venv || falhar "Não foi possível criar o ambiente virtual. Instale o pacote python3-venv."
  .venv/bin/pip install -q --upgrade pip
  .venv/bin/pip install -q -r requirements.txt || falhar "Não foi possível instalar as dependências."
fi
.venv/bin/python scripts/preparar_ambiente.py

# 2. Banco de dados (PostgreSQL no Docker)
echo "Iniciando o banco de dados..."
docker compose up -d db >/dev/null || falhar "Não foi possível iniciar o banco de dados (docker compose)."
for _ in $(seq 1 60); do
  [[ "$(docker inspect -f '{{.State.Health.Status}}' "$(docker compose ps -q db)" 2>/dev/null)" == "healthy" ]] && break
  sleep 1
done

# 3. Tabelas do banco (aplica só o que estiver faltando)
.venv/bin/python manage.py migrate --noinput >/dev/null || falhar "Não foi possível preparar as tabelas do banco."

# 4. Sistema e navegador
echo "Abrindo $ENDERECO ..."
( for _ in $(seq 1 40); do porta_em_uso && break; sleep 0.5; done; abrir_navegador ) &
echo "Para encerrar o sistema, feche esta janela ou pressione Ctrl+C."
exec .venv/bin/python manage.py runserver "127.0.0.1:${PORTA}"
