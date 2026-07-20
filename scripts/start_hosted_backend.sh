#!/usr/bin/env bash
set -euo pipefail

: "${PORT:=8000}"
: "${VF_DB_BACKEND:=postgres}"
: "${VF_API_DATA_MODE:=hybrid}"
: "${VF_AGENT_ENABLED:=true}"
: "${VF_AGENT_BINDING:=mock}"
: "${VF_AUTOPROVISION:=false}"
: "${VF_PROVISION_BINDING:=mock}"
: "${VF_PROXY_UPSTREAM:=fake}"

export PORT VF_DB_BACKEND VF_API_DATA_MODE VF_AGENT_ENABLED VF_AGENT_BINDING
export VF_AUTOPROVISION VF_PROVISION_BINDING VF_PROXY_UPSTREAM

alembic upgrade head
python -m scripts.build_demo_artifacts --sync-existing-db
exec python -m uvicorn app.reviewer.main:app \
  --host 0.0.0.0 \
  --port "$PORT" \
  --workers 1 \
  --log-level info
