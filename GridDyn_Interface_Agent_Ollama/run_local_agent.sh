#!/usr/bin/env bash
set -euo pipefail

MODEL="qwen3:8b"
MODE="demo"
INTERVAL="30"
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PYTHON="$PROJECT_ROOT/.venv/bin/python"

usage() {
  echo "Usage: $0 [--demo | --live-helics] [--model MODEL] [--interval SECONDS]"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --demo) MODE="demo"; shift ;;
    --live-helics) MODE="live"; shift ;;
    --model) MODEL="$2"; shift 2 ;;
    --interval) INTERVAL="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage; exit 1 ;;
  esac
done

if [[ ! -x "$VENV_PYTHON" ]]; then
  echo "The local environment was not found. Run ./install_local_agent.sh first." >&2
  exit 1
fi

endpoint_up() {
  curl -fsS --max-time 2 "$1" >/dev/null 2>&1
}

wait_endpoint() {
  local url="$1"
  for _ in $(seq 1 30); do
    if endpoint_up "$url"; then return 0; fi
    sleep 1
  done
  echo "Service did not become available at $url." >&2
  exit 1
}

INTERFACE_PID=""
GATEWAY_PID=""
cleanup() {
  if [[ -n "$GATEWAY_PID" ]] && kill -0 "$GATEWAY_PID" 2>/dev/null; then kill "$GATEWAY_PID" 2>/dev/null || true; fi
  if [[ -n "$INTERFACE_PID" ]] && kill -0 "$INTERFACE_PID" 2>/dev/null; then kill "$INTERFACE_PID" 2>/dev/null || true; fi
}
trap cleanup EXIT INT TERM

if ! endpoint_up "http://127.0.0.1:8765/api/status"; then
  "$VENV_PYTHON" "$PROJECT_ROOT/app.py" --host 127.0.0.1 --port 8765 >"$PROJECT_ROOT/app-local.log" 2>&1 &
  INTERFACE_PID="$!"
fi
wait_endpoint "http://127.0.0.1:8765/api/status"

if [[ "$MODE" == "live" ]]; then
  if ! endpoint_up "http://127.0.0.1:8766/v1/health"; then
    "$VENV_PYTHON" "$PROJECT_ROOT/agent_gateway.py" --config "$PROJECT_ROOT/helics_agent_config.json" --host 127.0.0.1 --port 8766 >"$PROJECT_ROOT/gateway-local.log" 2>&1 &
    GATEWAY_PID="$!"
  fi
  wait_endpoint "http://127.0.0.1:8766/v1/health"
  CONTEXT_URL="http://127.0.0.1:8766/v1/context"
  echo "Using live HELICS context."
else
  CONTEXT_URL="http://127.0.0.1:8765/api/agent/demo-context"
  echo "Using synthetic demo context."
fi

if command -v xdg-open >/dev/null 2>&1; then xdg-open "http://127.0.0.1:8765/review" >/dev/null 2>&1 || true; fi
if command -v open >/dev/null 2>&1; then open "http://127.0.0.1:8765/review" >/dev/null 2>&1 || true; fi

echo "Review console: http://127.0.0.1:8765/review"
echo "Press Ctrl+C to stop the local agent and services started by this script."
"$VENV_PYTHON" "$PROJECT_ROOT/local_agent.py" --model "$MODEL" --context-url "$CONTEXT_URL" --interval "$INTERVAL"
