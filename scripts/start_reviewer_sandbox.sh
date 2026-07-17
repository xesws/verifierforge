#!/usr/bin/env bash
# Start the reviewer-safe, zero-cost artifact API and fake OpenAI-compatible proxy.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON:-python}"
HOST="127.0.0.1"
API_PORT="${VF_REVIEW_API_PORT:-8012}"
PROXY_PORT="${VF_REVIEW_PROXY_PORT:-8013}"
RUNTIME_DIR="${VF_REVIEW_RUNTIME_DIR:-$ROOT_DIR/runs/reviewer-sandbox}"

usage() {
  cat <<'EOF'
Usage: bash scripts/start_reviewer_sandbox.sh [--api-port PORT] [--proxy-port PORT]

Starts a loopback-only reviewer sandbox:
  API:   http://127.0.0.1:<api-port> (/docs and /jobs)
  Proxy: http://127.0.0.1:<proxy-port>/v1/chat/completions

The API reads committed demo artifacts and the proxy uses the deterministic fake
upstream. No model-provider request, credential, or public listener is used.
EOF
}

while (($#)); do
  case "$1" in
    --api-port)
      API_PORT="${2:?--api-port requires a value}"
      shift 2
      ;;
    --proxy-port)
      PROXY_PORT="${2:?--proxy-port requires a value}"
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

check_port() {
  "$PYTHON_BIN" - "$HOST" "$1" <<'PY'
import socket
import sys

host, raw_port = sys.argv[1:]
try:
    port = int(raw_port)
except ValueError:
    raise SystemExit(f"invalid port: {raw_port}")
if not 1 <= port <= 65535:
    raise SystemExit(f"invalid port: {port}")
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
    probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        probe.bind((host, port))
    except OSError as error:
        raise SystemExit(f"refusing to start: {host}:{port} is unavailable ({error})")
PY
}

if [[ "$API_PORT" == "$PROXY_PORT" ]]; then
  echo "refusing to start: API and proxy ports must differ" >&2
  exit 2
fi

check_port "$API_PORT"
check_port "$PROXY_PORT"
mkdir -p "$RUNTIME_DIR"

api_pid=""
proxy_pid=""
cleanup() {
  local exit_code=$?
  trap - EXIT INT TERM
  [[ -z "$proxy_pid" ]] || kill "$proxy_pid" 2>/dev/null || true
  [[ -z "$api_pid" ]] || kill "$api_pid" 2>/dev/null || true
  [[ -z "$proxy_pid" ]] || wait "$proxy_pid" 2>/dev/null || true
  [[ -z "$api_pid" ]] || wait "$api_pid" 2>/dev/null || true
  exit "$exit_code"
}
trap cleanup EXIT INT TERM

wait_for_http() {
  "$PYTHON_BIN" - "$1" <<'PY'
import sys
import time
from urllib.request import urlopen

url = sys.argv[1]
last_error = None
for _ in range(80):
    try:
        with urlopen(url, timeout=1) as response:
            if 200 <= response.status < 300:
                raise SystemExit(0)
    except Exception as error:  # Service startup is the expected transient case.
        last_error = error
    time.sleep(0.1)
raise SystemExit(f"service did not become ready at {url}: {last_error}")
PY
}

(
  cd "$ROOT_DIR"
  export VF_API_DATA_MODE=artifacts
  export VF_PROXY_UPSTREAM=fake
  export VF_PROXY_DB_PATH="$RUNTIME_DIR/traffic.db"
  exec "$PYTHON_BIN" -m uvicorn app.api.main:app --host "$HOST" --port "$API_PORT" --log-level warning
) >"$RUNTIME_DIR/api.log" 2>&1 &
api_pid=$!

(
  cd "$ROOT_DIR"
  export VF_API_DATA_MODE=artifacts
  export VF_PROXY_UPSTREAM=fake
  export VF_PROXY_DB_PATH="$RUNTIME_DIR/traffic.db"
  exec "$PYTHON_BIN" -m uvicorn app.proxy.main:app --host "$HOST" --port "$PROXY_PORT" --log-level warning
) >"$RUNTIME_DIR/proxy.log" 2>&1 &
proxy_pid=$!

wait_for_http "http://$HOST:$API_PORT/jobs"
wait_for_http "http://$HOST:$PROXY_PORT/openapi.json"

echo "Reviewer sandbox is ready."
echo "API:   http://$HOST:$API_PORT/docs"
echo "Proxy: http://$HOST:$PROXY_PORT/v1/chat/completions"
echo "Runtime logs: $RUNTIME_DIR"
while true; do
  if ! kill -0 "$api_pid" 2>/dev/null; then
    wait "$api_pid"
    exit $?
  fi
  if ! kill -0 "$proxy_pid" 2>/dev/null; then
    wait "$proxy_pid"
    exit $?
  fi
  sleep 1
done
