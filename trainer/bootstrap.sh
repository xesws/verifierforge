#!/usr/bin/env bash
set -euo pipefail

WORKSPACE="${VF_WORKSPACE:-/workspace}"
ROOT="${VF_ROOT:-$WORKSPACE/verifierforge}"
REPO_URL="${VF_REPO_URL:-git@github.com:xesws/verifierforge.git}"
export HF_HOME="${HF_HOME:-$WORKSPACE/hf-cache}"

mkdir -p "$WORKSPACE" "$HF_HOME"

packages=()
for command in git tmux rsync python3; do
  command -v "$command" >/dev/null 2>&1 || packages+=("$command")
done
if ! command -v python3 >/dev/null 2>&1 || ! python3 -m venv --help >/dev/null 2>&1; then
  packages+=("python3-venv")
fi
if ((${#packages[@]})); then
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y "${packages[@]}"
fi

if [[ ! -d "$ROOT/.git" ]]; then
  git clone "$REPO_URL" "$ROOT"
fi
cd "$ROOT"

if [[ ! -x .venv/bin/python ]]; then
  python3 -m venv .venv
fi

.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements-trainer.txt
mkdir -p "$ROOT/runs" "$ROOT/models"

echo "VerifierForge pod is ready in $ROOT (HF_HOME=$HF_HOME)"
