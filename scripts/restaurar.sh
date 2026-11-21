#!/usr/bin/env bash
# Restaura um backup gerado por scripts/backup.sh (SUBSTITUI os dados atuais).
# Uso: scripts/restaurar.sh backups/caderneta_AAAA-MM-DD_HH-MM-SS.sql
# (Opcional: BANCO=outro_banco scripts/restaurar.sh ... — usado nos testes.)
#
# Segurança:
#  - antes de restaurar, gera um backup dos dados atuais;
#  - a restauração roda em UMA transação: se algo falhar, nada é alterado;
#  - o schema é recriado do zero, então backups de versões anteriores do sistema
#    também funcionam (depois, rode "python manage.py migrate").
set -euo pipefail
cd "$(dirname "$0")/.."

arquivo="${1:-}"
if [[ -z "$arquivo" || ! -f "$arquivo" ]]; then
  echo "Uso: scripts/restaurar.sh backups/caderneta_AAAA-MM-DD_HH-MM-SS.sql" >&2
  exit 1
fi

read -r -p "Os dados atuais serão SUBSTITUÍDOS pelo backup '$arquivo'. Continuar? [s/N] " resposta
if [[ ! "$resposta" =~ ^[sS]$ ]]; then
  echo "Restauração cancelada."
  exit 0
fi

echo "Gerando backup de segurança dos dados atuais..."
scripts/backup.sh

trap 'echo "A restauração FALHOU e nada foi alterado: o banco continua como estava." >&2' ERR
{
  echo "DROP SCHEMA public CASCADE;"
  echo "CREATE SCHEMA public;"
  cat "$arquivo"
} | docker compose exec -T -e BANCO="${BANCO:-}" -e PGOPTIONS="-c client_min_messages=warning" db sh -c \
  'psql -v ON_ERROR_STOP=1 -q --single-transaction -U "$POSTGRES_USER" -d "${BANCO:-$POSTGRES_DB}"' > /dev/null

echo "Backup restaurado com sucesso."
echo "Se o backup for de uma versão anterior do sistema, rode: python manage.py migrate"
