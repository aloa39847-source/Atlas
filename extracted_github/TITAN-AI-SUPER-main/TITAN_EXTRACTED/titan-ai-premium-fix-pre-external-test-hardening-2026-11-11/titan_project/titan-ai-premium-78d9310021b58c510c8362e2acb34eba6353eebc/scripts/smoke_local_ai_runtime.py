"""Real local-model smoke test; run only after the Ollama model has been pulled."""
from types import SimpleNamespace

from app.integrations import ai_providers, local_ai


def main() -> None:
    status = local_ai.local_model_status(timeout=5)
    if not (status.get("connected") and status.get("model_available") and status.get("ready")):
        raise SystemExit(f"Local model is not ready: {status}")

    # Simulate an unavailable OpenAI network path without sending a request or using a fake key.
    openai = SimpleNamespace(
        enabled=True, provider="openai", model="offline-simulated", tasks=["analysis"],
    )
    ai_providers.list_configs = lambda _db: [openai]
    ai_providers.invoke_provider = lambda *_args, **_kwargs: ai_providers.ProviderResult(
        False, "openai", "offline-simulated", error="simulated_network_outage",
    )
    ai_providers.record_usage = lambda *_args, **_kwargs: None

    context = {
        "symbol": "BTC/USDT",
        "data_source": "preloaded_context_offline_smoke",
        "decision": {"action": "wait", "reason": "offline runtime smoke test"},
        "independent_risk_result": {"ok": False, "reason": "test-only context"},
        "timeframe_evidence": {"1h": {"trend": "unknown", "signals": []}},
    }
    result = ai_providers.evaluate_decision_advisory(object(), context)
    if result.get("primary_provider") != "local" or not result.get("fallback_used"):
        raise SystemExit(f"Local fallback did not complete: {result}")
    if not result.get("analysis_context_preserved"):
        raise SystemExit("Analysis context preservation flag missing")
    if len(result.get("analysis_context_sha256", "")) != 64:
        raise SystemExit("Analysis context fingerprint missing")
    if result.get("action_authority") != "none" or result.get("decision_modified") is not False:
        raise SystemExit("Local model exceeded advisory-only authority")
    print({
        "ok": True,
        "local_model": status.get("model"),
        "local_runtime_ready": status.get("ready"),
        "provider": result.get("primary_provider"),
        "fallback_used": result.get("fallback_used"),
        "context_sha256": result.get("analysis_context_sha256"),
        "action_authority": result.get("action_authority"),
        "decision_modified": result.get("decision_modified"),
    })


if __name__ == "__main__":
    main()
