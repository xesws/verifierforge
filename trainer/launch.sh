#!/usr/bin/env bash
set -euo pipefail

job="${1:?usage: launch.sh <job> <cfg>}"
cfg="${2:?usage: launch.sh <job> <cfg>}"
export HF_HOME="${HF_HOME:-/workspace/hf-cache}"
mkdir -p "$HF_HOME"

args=()
if [[ "$cfg" == "fake_smoke" ]]; then
  args=(--steps 150 --interval 2)
fi

if [[ -x .venv/bin/python ]]; then
  exec .venv/bin/python -m trainer.fake_train --job "$job" "${args[@]}"
fi

exec python3 -m trainer.fake_train --job "$job" "${args[@]}"
