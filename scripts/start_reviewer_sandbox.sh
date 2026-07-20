#!/usr/bin/env bash
# Start the zero-cost fallback or authenticated full reviewer sandbox.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON:-python}"
HOST="127.0.0.1"
MODE="fallback"
API_PORT="${VF_REVIEW_API_PORT:-8012}"
PROXY_PORT="${VF_REVIEW_PROXY_PORT:-8013}"
PUBLIC_PORT="${VF_REVIEW_PUBLIC_PORT:-8014}"
RUNTIME_DIR="${VF_REVIEW_RUNTIME_DIR:-$ROOT_DIR/runs/reviewer-sandbox}"

usage() {
  cat <<'EOF'
Usage: bash scripts/start_reviewer_sandbox.sh [--mode fallback|full]
       [--api-port PORT] [--proxy-port PORT] [--public-port PORT]

fallback (default): loopback artifact API + deterministic fake proxy, no secrets.
full: authenticated composite API/UI/proxy + Supabase + configured tuned endpoint
      + mock Agent/Provisioner, exposed through a Cloudflare quick tunnel.
EOF
}

while (($#)); do
  case "$1" in
    --mode) MODE="${2:?--mode requires a value}"; shift 2 ;;
    --api-port) API_PORT="${2:?--api-port requires a value}"; shift 2 ;;
    --proxy-port) PROXY_PORT="${2:?--proxy-port requires a value}"; shift 2 ;;
    --public-port) PUBLIC_PORT="${2:?--public-port requires a value}"; shift 2 ;;
    --help|-h) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ "$MODE" != "fallback" && "$MODE" != "full" ]]; then
  echo "--mode must be fallback or full" >&2
  exit 2
fi

check_port() {
  "$PYTHON_BIN" - "$HOST" "$1" <<'PY'
import socket, sys
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

wait_for_http() {
  "$PYTHON_BIN" - "$1" <<'PY'
import sys, time
from urllib.request import urlopen
url = sys.argv[1]
last_error = None
for _ in range(120):
    try:
        with urlopen(url, timeout=1) as response:
            if 200 <= response.status < 300:
                raise SystemExit(0)
    except Exception as error:
        last_error = error
    time.sleep(.1)
raise SystemExit(f"service did not become ready at {url}: {last_error}")
PY
}

mkdir -p "$RUNTIME_DIR"
api_pid=""; proxy_pid=""; tunnel_pid=""; route_saved=""
restore_route() {
  [[ "$MODE" == "full" && -n "$route_saved" && -f "$route_saved" ]] || return 0
  "$PYTHON_BIN" - "$PUBLIC_PORT" "$route_saved" <<'PY' || true
import base64, json, os, sys
from urllib.request import Request, urlopen
port, path = sys.argv[1:]
payload = open(path, encoding="utf-8").read().encode()
token = base64.b64encode(f"judge:{os.environ['VF_REVIEW_INVITE_CODE']}".encode()).decode()
request = Request(
    f"http://127.0.0.1:{port}/clusters/data-pull-sql/routing",
    data=payload,
    headers={"Authorization": f"Basic {token}", "Content-Type": "application/json"},
    method="PUT",
)
with urlopen(request, timeout=5):
    pass
PY
}
cleanup() {
  local exit_code=$?
  trap - EXIT INT TERM
  restore_route
  [[ -z "$tunnel_pid" ]] || kill "$tunnel_pid" 2>/dev/null || true
  [[ -z "$proxy_pid" ]] || kill "$proxy_pid" 2>/dev/null || true
  [[ -z "$api_pid" ]] || kill "$api_pid" 2>/dev/null || true
  [[ -z "$tunnel_pid" ]] || wait "$tunnel_pid" 2>/dev/null || true
  [[ -z "$proxy_pid" ]] || wait "$proxy_pid" 2>/dev/null || true
  [[ -z "$api_pid" ]] || wait "$api_pid" 2>/dev/null || true
  exit "$exit_code"
}
trap cleanup EXIT INT TERM

if [[ "$MODE" == "fallback" ]]; then
  if [[ "$API_PORT" == "$PROXY_PORT" ]]; then
    echo "refusing to start: API and proxy ports must differ" >&2
    exit 2
  fi
  check_port "$API_PORT"; check_port "$PROXY_PORT"
  (
    cd "$ROOT_DIR"
    export VF_API_DATA_MODE=artifacts VF_PROXY_UPSTREAM=fake
    export VF_PROXY_DB_PATH="$RUNTIME_DIR/traffic.db"
    exec "$PYTHON_BIN" -m uvicorn app.api.main:app --host "$HOST" --port "$API_PORT" --log-level warning
  ) >"$RUNTIME_DIR/api.log" 2>&1 & api_pid=$!
  (
    cd "$ROOT_DIR"
    export VF_API_DATA_MODE=artifacts VF_PROXY_UPSTREAM=fake
    export VF_PROXY_DB_PATH="$RUNTIME_DIR/traffic.db"
    exec "$PYTHON_BIN" -m uvicorn app.proxy.main:app --host "$HOST" --port "$PROXY_PORT" --log-level warning
  ) >"$RUNTIME_DIR/proxy.log" 2>&1 & proxy_pid=$!
  wait_for_http "http://$HOST:$API_PORT/jobs"
  wait_for_http "http://$HOST:$PROXY_PORT/openapi.json"
  echo "Reviewer fallback is ready."
  echo "API:   http://$HOST:$API_PORT/docs"
  echo "Proxy: http://$HOST:$PROXY_PORT/v1/chat/completions"
else
  check_port "$PUBLIC_PORT"
  test_mode="${VF_REVIEW_TEST_MODE:-false}"
  if [[ "$test_mode" != "true" ]]; then
    "$PYTHON_BIN" - "$ROOT_DIR/.env" <<'PY'
import os, sys
from dotenv import dotenv_values
values = {**dotenv_values(sys.argv[1]), **os.environ}
required = ["SUPABASE_DB_URL", "VF_PROXY_TUNED_UPSTREAM", "VF_PROXY_TUNED_API_KEY", "VF_ENDPOINT_MODEL"]
if any(not str(values.get(name, "")).strip() for name in required):
    raise SystemExit("full reviewer mode is missing required environment configuration")
if not str(values["VF_PROXY_TUNED_UPSTREAM"]).startswith(("http://", "https://")):
    raise SystemExit("full reviewer mode requires an HTTP tuned endpoint")
PY
    "$PYTHON_BIN" - "$ROOT_DIR/.env" <<'PY'
import os, sys
import httpx
from dotenv import dotenv_values
values = {**dotenv_values(sys.argv[1]), **os.environ}
base = str(values["VF_PROXY_TUNED_UPSTREAM"]).rstrip("/")
response = httpx.get(
    f"{base}/models",
    headers={"Authorization": f"Bearer {values['VF_PROXY_TUNED_API_KEY']}"},
    timeout=15,
)
if response.status_code != 200:
    raise SystemExit(f"configured tuned endpoint health returned HTTP {response.status_code}")
body = response.json()
if not isinstance(body, dict) or not isinstance(body.get("data"), list) or not body["data"]:
    raise SystemExit("configured tuned endpoint returned no models")
print("configured tuned endpoint health passed")
PY
    command -v cloudflared >/dev/null || {
      echo "full reviewer mode requires cloudflared" >&2
      exit 1
    }
  fi
  export VF_REVIEW_INVITE_CODE="${VF_REVIEW_INVITE_CODE:-$($PYTHON_BIN -c 'import secrets; print(secrets.token_urlsafe(18))')}"
  umask 077
  printf '%s\n' "$VF_REVIEW_INVITE_CODE" >"$RUNTIME_DIR/invite-code.txt"
  chmod 600 "$RUNTIME_DIR/invite-code.txt"
  if [[ "$test_mode" == "true" ]]; then
    export VF_DB_BACKEND=sqlite VF_PROXY_DB_PATH="$RUNTIME_DIR/full.sqlite3"
    export VF_PROXY_TUNED_UPSTREAM=fake-tuned
    unset VF_PROXY_TUNED_API_KEY || true
  else
    export VF_DB_BACKEND=postgres
  fi
  export VF_API_DATA_MODE=hybrid VF_AGENT_ENABLED=true VF_AGENT_BINDING=mock
  export VF_AUTOPROVISION=true VF_PROVISION_BINDING=mock VF_PROXY_UPSTREAM=fake
  (
    cd "$ROOT_DIR"
    exec "$PYTHON_BIN" -m uvicorn app.reviewer.main:app --host "$HOST" --port "$PUBLIC_PORT" --log-level warning --env-file .env
  ) >"$RUNTIME_DIR/full.log" 2>&1 & api_pid=$!
  wait_for_http "http://$HOST:$PUBLIC_PORT/healthz"
  route_saved="$RUNTIME_DIR/route-before.json"
  "$PYTHON_BIN" - "$PUBLIC_PORT" "$route_saved" <<'PY'
import base64, json, os, sys
from urllib.request import Request, urlopen
port, path = sys.argv[1:]
token = base64.b64encode(f"judge:{os.environ['VF_REVIEW_INVITE_CODE']}".encode()).decode()
headers = {"Authorization": f"Basic {token}"}
url = f"http://127.0.0.1:{port}/clusters/data-pull-sql/routing"
with urlopen(Request(url, headers=headers), timeout=5) as response:
    before = json.load(response)
open(path, "w", encoding="utf-8").write(json.dumps(before, sort_keys=True) + "\n")
after = {**before, "enabled": True, "canary_percent": 50}
request = Request(url, data=json.dumps(after).encode(), headers={**headers, "Content-Type": "application/json"}, method="PUT")
with urlopen(request, timeout=5) as response:
    saved = json.load(response)
if saved["canary_percent"] != 50:
    raise SystemExit("reviewer canary setup failed")
PY
  if [[ "$test_mode" != "true" ]]; then
    cloudflared tunnel --url "http://$HOST:$PUBLIC_PORT" --no-autoupdate >"$RUNTIME_DIR/cloudflared.log" 2>&1 & tunnel_pid=$!
    "$PYTHON_BIN" - "$RUNTIME_DIR/cloudflared.log" "$RUNTIME_DIR/public-url.txt" <<'PY'
import re, sys, time
log_path, output_path = sys.argv[1:]
pattern = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
for _ in range(120):
    try:
        text = open(log_path, encoding="utf-8", errors="replace").read()
    except OSError:
        text = ""
    match = pattern.search(text)
    if match:
        open(output_path, "w", encoding="utf-8").write(match.group(0) + "\n")
        print(match.group(0))
        raise SystemExit(0)
    time.sleep(.5)
raise SystemExit("cloudflared quick tunnel did not publish a URL")
PY
  else
    printf '%s\n' "http://$HOST:$PUBLIC_PORT" >"$RUNTIME_DIR/public-url.txt"
  fi
  echo "Reviewer full mode is ready."
  echo "URL: $(<"$RUNTIME_DIR/public-url.txt")"
  echo "Basic Auth user: judge"
  echo "Invite code file: $RUNTIME_DIR/invite-code.txt"
fi

echo "Runtime logs: $RUNTIME_DIR"
while true; do
  if ! kill -0 "$api_pid" 2>/dev/null; then wait "$api_pid"; exit $?; fi
  if [[ -n "$proxy_pid" ]] && ! kill -0 "$proxy_pid" 2>/dev/null; then wait "$proxy_pid"; exit $?; fi
  if [[ -n "$tunnel_pid" ]] && ! kill -0 "$tunnel_pid" 2>/dev/null; then wait "$tunnel_pid"; exit $?; fi
  sleep 2
done
