#!/usr/bin/env bash
set -euo pipefail

MODEL="${TITAN_LOCAL_AI_MODEL:-qwen2.5:1.5b}"
HOST="127.0.0.1:11434"

if [[ "${PREFIX:-}" == *"com.termux"* ]]; then
  echo "Ollama's official Linux installer is not supported directly in Termux."
  echo "Run this script on the Linux host that runs TITAN's backend, or use a supported llama.cpp server there."
  exit 2
fi
if [[ "$(uname -s)" != "Linux" ]]; then
  echo "This setup helper currently supports Linux hosts only."
  exit 2
fi
if ! command -v curl >/dev/null 2>&1; then
  echo "curl is required. Install it, then rerun this script."
  exit 2
fi

if ! command -v ollama >/dev/null 2>&1; then
  echo "Installing Ollama from its official installer..."
  installer="$(mktemp)"
  trap 'rm -f "$installer"' EXIT
  curl -fsSL https://ollama.com/install.sh -o "$installer"
  sh "$installer"
fi

if ! curl --silent --fail "http://$HOST/api/tags" >/dev/null; then
  echo "Starting Ollama bound to loopback only..."
  (OLLAMA_HOST="$HOST" nohup ollama serve >"$HOME/.titan-ollama.log" 2>&1 </dev/null &)
  for _ in $(seq 1 30); do
    if curl --silent --fail "http://$HOST/api/tags" >/dev/null; then break; fi
    sleep 1
  done
fi
if ! curl --silent --fail "http://$HOST/api/tags" >/dev/null; then
  echo "Ollama did not become ready. Check $HOME/.titan-ollama.log"
  exit 1
fi

echo "Downloading model weights: $MODEL"
ollama pull "$MODEL"
echo
echo "Installed local model:"
ollama list
echo
echo "Add these values to the backend environment (.env); never add API keys here:"
echo "TITAN_LOCAL_AI_ENABLED=true"
echo "TITAN_LOCAL_AI_BASE_URL=http://127.0.0.1:11434/v1"
echo "TITAN_LOCAL_AI_MODEL=$MODEL"
echo
echo "Restart TITAN's backend, then verify GET /api/ai/local/status and POST /api/ai/local/test."
echo "Keep Ollama on loopback. The backend and Ollama must run on the same host/network namespace."
