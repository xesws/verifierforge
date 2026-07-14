#!/usr/bin/env bash
set -euo pipefail

job="${1:?usage: launch.sh <job> <cfg>}"
cfg="${2:?usage: launch.sh <job> <cfg>}"

# cfg is reserved for the real trainer configuration added in a later phase.
if [[ -x .venv/bin/python ]]; then
  exec .venv/bin/python -m trainer.fake_train --job "$job"
fi

exec python3 -m trainer.fake_train --job "$job"
