#!/usr/bin/env bash
set -euo pipefail
cd /home/sribd/spectrum_agent
export SPECTRUM_ROOT="$PWD"
export PYTHONPATH="$PWD/src"
export PYTHONDONTWRITEBYTECODE=1
exec "$PWD/.venv/bin/python" -m uvicorn spectrum_agent.api:app --host 127.0.0.1 --port 8765 --workers 1
