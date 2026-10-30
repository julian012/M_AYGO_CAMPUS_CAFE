#!/usr/bin/env bash
# Each service has its own app.py/common.py, so run each suite in its own process.
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PYTHON:-.venv/bin/python}"
"$PY" -m unittest discover -s tests/catalog -v
"$PY" -m unittest discover -s tests/orders -v
