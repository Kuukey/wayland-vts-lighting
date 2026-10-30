#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"
if [[ -z "${PYTHON:-}" ]]; then
  if [[ -x "$PWD/.venv/bin/python" ]]; then
    PYTHON="$PWD/.venv/bin/python"
  elif [[ -x "$PWD/venv/bin/python" ]]; then
    PYTHON="$PWD/venv/bin/python"
  else
    PYTHON="$(command -v python3)"
  fi
fi
if [[ ! -x "$PYTHON" ]]; then
  PYTHON="$(command -v python3)"
fi
if ! "$PYTHON" -c 'import PySide6' >/dev/null 2>&1; then
  echo "PySide6 is missing from: $PYTHON"
  echo "Install it with: $PYTHON -m pip install PySide6"
  exit 1
fi
exec "$PYTHON" gui.py
