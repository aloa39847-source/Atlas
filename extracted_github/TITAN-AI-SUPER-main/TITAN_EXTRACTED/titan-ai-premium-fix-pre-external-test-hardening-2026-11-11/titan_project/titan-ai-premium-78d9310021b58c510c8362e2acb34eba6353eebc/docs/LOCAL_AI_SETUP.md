# TITAN local AI runtime (Ollama)

TITAN supports a local OpenAI-compatible inference endpoint as a **read-only AI adviser**. The suggested starter model is `qwen2.5:1.5b`; it is intentionally smaller than the previous 3B default for constrained CPU/RAM hosts. Model quality and speed depend on the host.

## Install a real model

Run from the project root on the **same Linux host/network namespace as TITAN's backend**:

```bash
bash scripts/setup_local_ollama.sh
```

The helper installs Ollama using its official Linux installer if needed, starts it bound to `127.0.0.1:11434`, downloads the model weights, and prints the environment values. The initial installation/model download needs internet. Once the weights are present, local inference itself does not need internet.

Set these values in the backend environment (or `.env`):

```dotenv
TITAN_LOCAL_AI_ENABLED=true
TITAN_LOCAL_AI_BASE_URL=http://127.0.0.1:11434/v1
TITAN_LOCAL_AI_MODEL=qwen2.5:1.5b
```

Restart the backend, then check:

- `GET /api/ai/local/status` must report `connected=true`, `model_available=true`, and `ready=true`.
- `POST /api/ai/local/test` must return `ok=true` and model text.

A reachable Ollama server without the selected model is **not ready**. TITAN will not attempt a local fallback until the configured model is actually listed by the local runtime.

## Offline behavior and important boundary

- After model weights are downloaded, Ollama inference and TITAN's deterministic calculations can run without internet when supplied with an already available analysis context.
- OpenAI, Binance public market-data refresh, news feeds, scanners, and other remote services cannot refresh while disconnected.
- The existing synthetic-data analysis path can run offline, but its output remains explicitly labeled synthetic and must never be mistaken for real prices or used as evidence of live market conditions.
- This local-model integration does not yet provide a persistent cache of the last real Binance candles for a full fresh-looking market analysis during an outage. Do not interpret a completed local AI response as proof that market data is current.

## OpenAI and failover

OpenAI remains the preferred read-only evaluator when an enabled OpenAI provider is configured. Enter the API key through TITAN's protected provider settings so it is encrypted server-side; do not commit it, paste it into source files, or place it in this repository. The key cannot be genuinely tested until the user supplies it and the provider test succeeds.

When OpenAI is unavailable, TITAN may call the local model only if the loopback runtime is reachable and the selected model is installed. Both providers receive the same serialized analysis messages; TITAN returns a SHA-256 context fingerprint so tests can verify that failover did not change the analysis payload. The LLM remains advisory only: TITAN's deterministic decision engine and independent risk manager retain authority.

## Android / Termux (same-phone backend)

For a backend that actually runs on the same Android phone, use the separate Termux `llama.cpp` helper instead of Ollama:

```bash
bash scripts/setup_local_llama_termux.sh
```

Run it directly in Termux (not inside Debian proot). It builds the CPU `llama-server`, downloads the official Qwen2.5-1.5B-Instruct GGUF Q4_K_M weights (about 1.12 GB), and binds an OpenAI-compatible API to `127.0.0.1:8080`. It asks before installing packages/building/downloading. The Qwen model card and llama.cpp server options are documented at [Qwen's official GGUF model](https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF) and [llama.cpp server README](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md).

Use these TITAN backend settings:

```dotenv
TITAN_LOCAL_AI_ENABLED=true
TITAN_LOCAL_AI_BASE_URL=http://127.0.0.1:8080/v1
TITAN_LOCAL_AI_MODEL=qwen2.5:1.5b
```

If TITAN's Python backend runs inside Debian proot, verify from that same proot shell that `curl http://127.0.0.1:8080/v1/models` works before enabling the adapter. The model and backend must share a reachable loopback network namespace. Keep the server bound to `127.0.0.1`; do not expose it publicly. Model inference after the download is local and does not require internet, but the initial build and download do.

## Android / hosted deployment

The local endpoint is loopback from the backend process, not automatically the Android phone's loopback. If TITAN's backend runs on Render or another remote host, installing Ollama on the phone does not make it reachable as `127.0.0.1` from that server. Run the backend and Ollama together on a supported Linux host, or keep local AI disabled until a supported private runtime arrangement is configured. The setup helper deliberately refuses to claim direct Termux support.
