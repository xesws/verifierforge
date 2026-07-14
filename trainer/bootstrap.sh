#!/usr/bin/env bash
set -euo pipefail

ROOT="${VF_ROOT:-/workspace/verifierforge}"
cd "$ROOT"

if [[ ! -x .venv/bin/python ]]; then
  python3 -m venv .venv
fi

.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements-trainer.txt -r requirements-app.txt
mkdir -p runs models

echo "VerifierForge pod is ready in $ROOT"
