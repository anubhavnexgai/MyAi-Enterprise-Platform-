#!/usr/bin/env bash
# Bootstrap the dedicated venv used to run vendored Odysseus instances.
#
# The MyAi bridge launches one Odysseus subprocess per tenant using this venv's
# interpreter (see app/odysseus_bridge/supervisor.py -> odysseus_python_exe).
# Giving Odysseus its own venv keeps its pinned deps from clashing with MyAi's.
#
# Usage:  bash scripts/bootstrap_odysseus.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ODY_DIR="$ROOT/vendor/odysseus"
VENV="$ODY_DIR/.venv"

[ -d "$ODY_DIR" ] || { echo "vendor/odysseus not found at $ODY_DIR" >&2; exit 1; }

if [ ! -d "$VENV" ]; then
  echo "Creating venv at $VENV ..."
  python3 -m venv "$VENV"
fi

PY="$VENV/bin/python"
echo "Upgrading pip ..."
"$PY" -m pip install --upgrade pip wheel setuptools

echo "Installing Odysseus requirements ..."
"$PY" -m pip install -r "$ODY_DIR/requirements.txt"

echo "Installing optional requirements (best-effort) ..."
"$PY" -m pip install -r "$ODY_DIR/requirements-optional.txt" || \
  echo "optional requirements failed (non-fatal)"

echo "Done. Odysseus interpreter: $PY"
