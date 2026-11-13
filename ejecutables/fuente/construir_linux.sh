#!/usr/bin/env bash
# Genera ejecutables/pasarela y ejecutables/cliente para Linux.
# Usa el entorno .venv del proyecto (con pybullet y UR3e_rg2 instalados).
set -euo pipefail
cd "$(dirname "$0")"
PY=../../.venv/bin/python
[ -x "$PY" ] || PY=python3

"$PY" -m PyInstaller --version >/dev/null 2>&1 || "$PY" -m pip install pyinstaller
"$PY" -m PyInstaller --noconfirm --clean --distpath .. --workpath build ejecutables.spec
echo
echo "Hecho: $(cd .. && pwd)/pasarela y $(cd .. && pwd)/cliente"
