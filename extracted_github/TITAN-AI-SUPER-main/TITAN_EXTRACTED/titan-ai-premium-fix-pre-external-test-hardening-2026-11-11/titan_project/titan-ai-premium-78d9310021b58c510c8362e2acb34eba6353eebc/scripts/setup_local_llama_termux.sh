#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${PREFIX:-}" || ! -x "$(command -v pkg 2>/dev/null || true)" ]]; then
  echo "Run this helper directly in Termux, not inside Debian proot or a normal Linux shell."
  exit 2
fi

MODEL_ALIAS="${TITAN_LOCAL_AI_MODEL:-qwen2.5:1.5b}"
MODEL_DIR="${TITAN_LOCAL_MODEL_DIR:-$HOME/models}"
MODEL_FILE="$MODEL_DIR/qwen2.5-1.5b-instruct-q4_k_m.gguf"
LLAMA_DIR="${TITAN_LLAMA_CPP_DIR:-$HOME/llama.cpp}"
SERVER="$LLAMA_DIR/build/bin/llama-server"

echo "This will build llama.cpp and download about 1.12 GB of model weights."
echo "Available storage:"
df -h "$HOME"
echo
read -r -p "Continue? [y/N] " answer
case "$answer" in y|Y|yes|YES) ;; *) echo "Cancelled."; exit 0 ;; esac

pkg update -y
pkg install -y git cmake clang make curl

if [[ ! -d "$LLAMA_DIR/.git" ]]; then
  git clone --depth 1 https://github.com/ggml-org/llama.cpp.git "$LLAMA_DIR"
fi

cmake -S "$LLAMA_DIR" -B "$LLAMA_DIR/build" \
  -DCMAKE_BUILD_TYPE=Release \
  -DGGML_NATIVE=OFF \
  -DGGML_OPENMP=OFF \
  -DGGML_BLAS=OFF \
  -DLLAMA_BUILD_SERVER=ON \
  -DLLAMA_BUILD_TESTS=OFF \
  -DLLAMA_BUILD_EXAMPLES=OFF

cmake --build "$LLAMA_DIR/build" --target llama-server -j2

mkdir -p "$MODEL_DIR"
if [[ ! -s "$MODEL_FILE" ]]; then
  echo "Downloading official Qwen GGUF Q4_K_M model..."
  curl -L --fail --retry 3 \
    "https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/qwen2.5-1.5b-instruct-q4_k_m.gguf" \
    -o "$MODEL_FILE"
fi

if ! curl --silent --fail http://127.0.0.1:8080/v1/models >/dev/null; then
  echo "Starting llama.cpp server on loopback only..."
  nohup "$SERVER" -m "$MODEL_FILE" \
    --host 127.0.0.1 --port 8080 --alias "$MODEL_ALIAS" \
    -c 4096 -t 4 >"$HOME/.titan-llama-server.log" 2>&1 </dev/null &
  for _ in $(seq 1 60); do
    if curl --silent --fail http://127.0.0.1:8080/v1/models >/dev/null; then break; fi
    sleep 1
  done
fi

if ! curl --silent --fail http://127.0.0.1:8080/v1/models >/dev/null; then
  echo "llama-server did not become ready. Check $HOME/.titan-llama-server.log"
  exit 1
fi

echo
echo "Local model server is responding:"
curl --silent --fail http://127.0.0.1:8080/v1/models
echo
echo
echo "Configure TITAN backend environment:"
echo "TITAN_LOCAL_AI_ENABLED=true"
echo "TITAN_LOCAL_AI_BASE_URL=http://127.0.0.1:8080/v1"
echo "TITAN_LOCAL_AI_MODEL=$MODEL_ALIAS"
echo
echo "Restart TITAN backend and check /api/ai/local/status then /api/ai/local/test."
echo "If backend runs in Debian proot, verify loopback access there with:"
echo "curl --fail http://127.0.0.1:8080/v1/models"
