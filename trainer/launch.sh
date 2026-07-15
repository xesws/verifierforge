#!/usr/bin/env bash
set -euo pipefail

job="${1:?usage: launch.sh <job> <cfg>}"
cfg="${2:?usage: launch.sh <job> <cfg>}"
export HF_HOME="${HF_HOME:-/workspace/hf-cache}"
mkdir -p "$HF_HOME"

python=python3
if [[ -x .venv/bin/python ]]; then
  python=.venv/bin/python
fi

case "$cfg" in
  fake_smoke)
    exec "$python" -m trainer.fake_train --job "$job" --steps 150 --interval 2
    ;;
  grpo_v1_0p5b)
    exec "$python" -m trainer.grpo_train --job "$job" --config "$cfg"
    ;;
  grpo_v1_0p5b_preflight)
    exec "$python" -m trainer.grpo_train --job "$job" --config grpo_v1_0p5b --steps 2
    ;;
  *)
    echo "unknown trainer config: $cfg" >&2
    exit 2
    ;;
esac
