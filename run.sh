#!/usr/bin/env bash
set -euo pipefail

python3 -m pip install --disable-pip-version-check -q -r requirements.txt
exec python3 agent.py
