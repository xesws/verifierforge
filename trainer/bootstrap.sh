#!/usr/bin/env bash
set -euo pipefail

WORKSPACE="${VF_WORKSPACE:-/workspace}"
ROOT="${VF_ROOT:-$WORKSPACE/verifierforge}"
REPO_URL="${VF_REPO_URL:-git@github.com:xesws/verifierforge.git}"
export HF_HOME="${HF_HOME:-$WORKSPACE/hf-cache}"
export PIP_CACHE_DIR="${PIP_CACHE_DIR:-$WORKSPACE/pip-cache}"

mkdir -p "$WORKSPACE" "$HF_HOME" "$PIP_CACHE_DIR"

restore_workspace_deploy_key() {
  local source_dir="$WORKSPACE/.ssh"
  [[ -f "$source_dir/id_ed25519" ]] || return 0

  install -d -m 700 "$HOME/.ssh"
  install -m 600 "$source_dir/id_ed25519" "$HOME/.ssh/id_ed25519"
  [[ ! -f "$source_dir/id_ed25519.pub" ]] || \
    install -m 644 "$source_dir/id_ed25519.pub" "$HOME/.ssh/id_ed25519.pub"
  [[ ! -f "$source_dir/known_hosts" ]] || \
    install -m 600 "$source_dir/known_hosts" "$HOME/.ssh/known_hosts"
}

restore_workspace_deploy_key

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
