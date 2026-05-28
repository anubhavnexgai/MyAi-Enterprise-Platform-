#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if [[ ! -d .venv ]]; then
    echo "Creating virtualenv..."
    python3 -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate

if [[ ! -f .env ]]; then
    echo "Copying .env.example to .env (first run)..."
    cp .env.example .env
fi

pip install -q -r requirements.txt

echo
echo "============================================================"
echo " MyAi-Enterprise starting on http://localhost:8002"
echo " Docs:   http://localhost:8002/docs"
echo " Health: http://localhost:8002/health"
echo "============================================================"
echo

exec python -m app.main
