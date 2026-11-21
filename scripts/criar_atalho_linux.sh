#!/usr/bin/env bash
# Cria o atalho "Caderneta Digital" no menu de aplicativos (e na Área de Trabalho, se existir).
# Depois disso, basta um clique no ícone para iniciar o sistema.
set -euo pipefail
PROJETO="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
APLICATIVOS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ATALHO="$APLICATIVOS/caderneta-digital.desktop"

mkdir -p "$APLICATIVOS"
chmod +x "$PROJETO/iniciar.sh"
cat > "$ATALHO" <<EOF
[Desktop Entry]
Type=Application
Name=Caderneta Digital
Comment=Controle de vendas a prazo (fiado)
Exec="$PROJETO/iniciar.sh"
Path=$PROJETO
Icon=$PROJETO/static/img/favicon.svg
Terminal=true
Categories=Office;Finance;
EOF
chmod +x "$ATALHO"
echo "Atalho criado no menu de aplicativos: $ATALHO"

AREA_DE_TRABALHO="$(xdg-user-dir DESKTOP 2>/dev/null || true)"
if [[ -n "$AREA_DE_TRABALHO" && -d "$AREA_DE_TRABALHO" && "$AREA_DE_TRABALHO" != "$HOME" ]]; then
  cp "$ATALHO" "$AREA_DE_TRABALHO/"
  chmod +x "$AREA_DE_TRABALHO/caderneta-digital.desktop"
  gio set "$AREA_DE_TRABALHO/caderneta-digital.desktop" metadata::trusted true 2>/dev/null || true
  echo "Atalho criado na Área de Trabalho: $AREA_DE_TRABALHO/caderneta-digital.desktop"
fi
