#!/usr/bin/env bash
# Bot Mover bundle setup (Mac / Linux). Safe to re-run.
# Makes .venv, installs requirements.lock, falls back to requirements.txt.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
PY="${PYTHON:-python3}"
command -v "$PY" >/dev/null 2>&1 || { echo "python3 not found. Install Python 3.10+ first." >&2; exit 1; }
LOCK="$ROOT/requirements.lock"; TXT="$ROOT/requirements.txt"
if [ ! -f "$LOCK" ] && [ ! -f "$TXT" ]; then
  echo "No Python packages needed. Setup done."
else
  [ -d "$ROOT/.venv" ] || "$PY" -m venv "$ROOT/.venv"
  VPY="$ROOT/.venv/bin/python"
  if [ -f "$LOCK" ] && "$VPY" -m pip install --disable-pip-version-check -r "$LOCK"; then
    echo "Installed from requirements.lock."
  elif [ -f "$TXT" ]; then
    [ -f "$LOCK" ] && echo "requirements.lock didn't install (often a different computer type). Using requirements.txt..." >&2
    "$VPY" -m pip install --disable-pip-version-check -r "$TXT"
    echo "Installed from requirements.txt."
  else
    echo "requirements.lock failed and there is no requirements.txt to fall back to." >&2; exit 1
  fi
fi
mkdir -p "$ROOT/data"
cat <<MSG
Setup done. In each new terminal:
  export DATA_ROOT="$ROOT/data"
  PY="$ROOT/.venv/bin/python"
MSG
