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

h100_diagnostic_environment() {
  export HF_HUB_OFFLINE=1
  export TRANSFORMERS_OFFLINE=1
  export VLLM_LOGGING_LEVEL=INFO
  export RAY_DEDUP_LOGS=0
  export PYTHONFAULTHANDLER=1
}

case "$cfg" in
  fake_smoke)
    exec "$python" -m trainer.fake_train --job "$job" --steps 150 --interval 2
    ;;
  grpo_v1_0p5b)
    exec "$python" -m trainer.grpo_train --job "$job" --config "$cfg"
    ;;
  grpo_v1_0p5b_p2)
    "$python" -m trainer.grpo_train --job "$job" --config "$cfg"
    ray_executable="$(dirname "$python")/ray"
    if [[ ! -x "$ray_executable" ]]; then
      ray_executable="$(command -v ray)"
    fi
    "$ray_executable" stop --force
    exec "$python" -m trainer.finalize_checkpoint --job "$job" --config "$cfg"
    ;;
  grpo_v1_0p5b_preflight)
    exec "$python" -m trainer.grpo_train --job "$job" --config grpo_v1_0p5b --steps 2
    ;;
  grpo_v1_1p5b_blackwell_smoke)
    exec "$python" -m trainer.grpo_train --job "$job" --config "$cfg"
    ;;
  grpo_v1_1p5b_h100_smoke)
    h100_diagnostic_environment
    exec "$python" -m trainer.grpo_train --job "$job" --config "$cfg"
    ;;
  grpo_v1_1p5b_h100_main|grpo_v1_0p5b_random_control)
    h100_diagnostic_environment
    exec "$python" -m trainer.grpo_train --job "$job" --config "$cfg"
    ;;
  *)
    echo "unknown trainer config: $cfg" >&2
    exit 2
    ;;
esac
