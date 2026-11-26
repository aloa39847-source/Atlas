from app.services.core_service import (
    ENGINE_INTEGRATION_MAP,
    ENGINE_REGISTRY,
    ENGINE_RUNTIME_CONTRACTS,
    inspect_engine_runtime_contract,
)


def test_every_registered_engine_has_an_explicit_runtime_and_integration_contract():
    registered = {key for key, _name, _status in ENGINE_REGISTRY}
    assert registered == set(ENGINE_INTEGRATION_MAP)
    assert registered == set(ENGINE_RUNTIME_CONTRACTS)

    results = {key: inspect_engine_runtime_contract(key) for key in sorted(registered)}
    broken = {
        key: result for key, result in results.items()
        if result["status"] != "wired"
    }
    assert not broken, broken
    assert all(result["scope"] == "import_and_callable_only" for result in results.values())


def test_runtime_contract_check_does_not_claim_external_connectivity():
    result = inspect_engine_runtime_contract("trade_execution")
    assert result["status"] == "wired"
    assert result["scope"] == "import_and_callable_only"
    assert "external connectivity" in result["note"]


def test_unknown_engine_is_reported_as_unmapped():
    result = inspect_engine_runtime_contract("not_a_real_engine")
    assert result["status"] == "unmapped"
