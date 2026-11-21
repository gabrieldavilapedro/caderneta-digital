#!/usr/bin/env bash
# Gera um backup completo do banco em backups/caderneta_AAAA-MM-DD_HH-MM-SS.sql
# Uso: scripts/backup.sh
# (Opcional: BANCO=outro_banco scripts/backup.sh — usado nos testes.)
set -euo pipefail
cd "$(dirname "$0")/.."

mkdir -p backups
arquivo="backups/caderneta_$(date +%Y-%m-%d_%H-%M-%S).sql"
trap 'rm -f "$arquivo"; echo "Falha ao gerar o backup. O banco está em execução? (docker compose ps)" >&2' ERR

docker compose exec -T -e BANCO="${BANCO:-}" db sh -c \
  'pg_dump -U "$POSTGRES_USER" -d "${BANCO:-$POSTGRES_DB}" --clean --if-exists --no-owner' > "$arquivo"

echo "Backup salvo em $arquivo"
echo "Guarde uma cópia fora deste computador (pendrive ou nuvem). O arquivo contém dados de clientes."
