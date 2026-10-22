#!/usr/bin/env bash
set -euo pipefail

MODEL="${MODEL:-qwen3:8b}"
OFFLINE="${OFFLINE:-0}"
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PATH="$PROJECT_ROOT/.venv"
VENV_PYTHON="$VENV_PATH/bin/python"
REQUIREMENTS="$PROJECT_ROOT/requirements-local-agent.txt"

say() {
  printf '\n==> %s\n' "$1"
}

if command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="$(command -v python3)"
elif command -v python >/dev/null 2>&1; then
  PYTHON_BIN="$(command -v python)"
else
  echo "Python 3 was not found. Install Python 3.10 or newer and retry." >&2
  exit 1
fi

say "Creating or reusing the local Python environment"
if [[ ! -x "$VENV_PYTHON" ]]; then
  "$PYTHON_BIN" -m venv "$VENV_PATH"
fi

say "Installing the local agent dependencies"
if [[ "$OFFLINE" == "1" ]]; then
  "$VENV_PYTHON" -m pip install --no-index -r "$REQUIREMENTS"
else
  "$VENV_PYTHON" -m pip install --upgrade pip
  "$VENV_PYTHON" -m pip install -r "$REQUIREMENTS"
fi

OLLAMA_BIN="$(command -v ollama || true)"
if [[ -z "$OLLAMA_BIN" ]]; then
  for candidate in /usr/local/bin/ollama /usr/bin/ollama /Applications/Ollama.app/Contents/Resources/ollama; do
    if [[ -x "$candidate" ]]; then
      OLLAMA_BIN="$candidate"
      break
    fi
  done
fi

if [[ -z "$OLLAMA_BIN" ]]; then
  if [[ "$OFFLINE" == "1" ]]; then
    echo "Ollama is not installed. Re-run without OFFLINE=1 while connected, or install Ollama and retry." >&2
    exit 1
  fi
  say "Installing Ollama"
  curl -fsSL https://ollama.com/install.sh | sh
  OLLAMA_BIN="$(command -v ollama || true)"
fi

if [[ -z "$OLLAMA_BIN" ]]; then
  echo "Ollama was installed but its executable was not found in PATH." >&2
  exit 1
fi

if ! curl -fsS --max-time 2 http://127.0.0.1:11434/api/version >/dev/null 2>&1; then
  say "Starting the local Ollama service"
  nohup "$OLLAMA_BIN" serve >"$PROJECT_ROOT/ollama-local.log" 2>&1 &
fi

for attempt in $(seq 1 30); do
  if curl -fsS --max-time 2 http://127.0.0.1:11434/api/version >/dev/null 2>&1; then
    break
  fi
  if [[ "$attempt" == "30" ]]; then
    echo "Ollama did not become available at http://127.0.0.1:11434." >&2
    exit 1
  fi
  sleep 2
done

if [[ "$OFFLINE" == "1" ]]; then
  if ! "$OLLAMA_BIN" list | grep -Fq "$MODEL"; then
    echo "The requested model '$MODEL' is not installed locally. Run without OFFLINE=1 once to download it." >&2
    exit 1
  fi
else
  say "Downloading the local model: $MODEL"
  "$OLLAMA_BIN" pull "$MODEL"
fi

printf '\nLocal agent installation is complete.\n'
printf 'Model: %s\n' "$MODEL"
printf 'Start the complete local workflow with:\n'
printf '  ./run_local_agent.sh --demo --model %s\n' "$MODEL"
printf '\nFor a real HELICS federation, use:\n'
printf '  ./run_local_agent.sh --live-helics --model %s\n' "$MODEL"
