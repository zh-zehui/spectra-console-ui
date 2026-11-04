#!/usr/bin/env bash
set -euo pipefail
cd /home/sribd/spectrum_agent
export PYTHONPATH="$PWD/src"
exec .venv/bin/python -m uvicorn spectrum_agent.ui_gateway:app --host 127.0.0.1 --port 8780 --workers 1 --log-level warning
