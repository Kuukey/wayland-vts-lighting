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
  echo "Could not find a usable Python interpreter. Set PYTHON=/path/to/python and rerun."
  exit 1
fi
"$PYTHON" -m pip install PySide6
chmod +x launch.sh
printf '\nInstalled. Launch with: %s/launch.sh\n' "$PWD"
