import asyncio
import json
import time
from types import SimpleNamespace

from app import star_app
from app.engines.backtest import BacktestEngine
from app.engines.market_data import Candles
from app.learning.persistent import PersistentStrategyLearning
from app.models.entities import LearningRecord


def _history(count=320):
    rows = []
    start = int(time.time() * 1000) - count * 3_600_000
    previous = 100.0
    for index in range(count):
        close = previous + 0.08 + (0.03 if index % 3 else -0.02)
        stamp = start + index * 3_600_000
        rows.append({
            "timestamp": stamp, "close_time": stamp + 3_600_000 - 1,
            "open": previous, "high": max(previous, close) + 0.3,
            "low": min(previous, close) - 0.3, "close": close,
            "volume": 1000.0 + index,
        })
        previous = close
    rows[-1]["close_time"] = int(time.time() * 1000) - 60_000
    return rows


def _json(response):
    return json.loads(response.body.decode("utf-8"))


def test_historical_learning_walk_forward_compares_both_parameter_sets_on_same_public_history(monkeypatch):
    proposal = SimpleNamespace(
        category="strategy_proposal",
        subject="trend_following_ema",
        evidence={
            "current_params": {"min_confirmations": 2},
            "proposed_params": {"min_confirmations": 3},
        },
    )

    class FakeDB:
        def get(self, model, record_id):
            assert model is LearningRecord and record_id == 41
            return proposal
        def add(self, row): pass
        def commit(self): pass
        def rollback(self): pass
        def close(self): pass

    rows = _history()
    batch = SimpleNamespace(
        df=Candles(rows), source=SimpleNamespace(value="live_public"), market_type="spot",
    )
    captured = {}
    monkeypatch.setattr(star_app, "db_sess", lambda: FakeDB())
    monkeypatch.setattr(star_app, "require_user", lambda request, db: (None, None))
    monkeypatch.setattr(star_app, "body", lambda request: None)
    async def request_body(_request):
        return {
            "proposal_id": 41, "symbol": "BTC/USDT", "timeframe": "1h",
            "limit": 320, "train_window": 150, "test_window": 50, "step": 50,
        }
    monkeypatch.setattr(star_app, "body", request_body)
    monkeypatch.setattr(
        star_app.core_service.market_data, "get_public_candles",
        lambda symbol, timeframe, limit, market_type: batch,
    )

    def fake_replay(self, candles, *, strategy_key, timeframe, train_window,
                    test_window, step, strategy_params):
        captured.setdefault("params", []).append(dict(strategy_params))
        candidate = strategy_params["min_confirmations"] == 3
        expectancy = 0.2 if candidate else 0.1
        drawdown = 0.08 if candidate else 0.1
        return {
            "ok": True, "strategy_key": strategy_key, "timeframe": timeframe.value,
            "fold_count": 3, "total_trades": 12 if candidate else 10,
            "expectancy": expectancy, "max_fold_drawdown": drawdown,
            "compounded_oos_return": 0.12 if candidate else 0.06,
            "no_lookahead": True,
            "folds": [
                {"testing_candles": 50, "result": {"expectancy": expectancy, "max_drawdown": drawdown}}
                for _ in range(3)
            ],
        }
    monkeypatch.setattr(BacktestEngine, "run_strategy_walk_forward", fake_replay)

    def fake_validate(self, db, proposal_id, baseline_folds, candidate_folds,
                      *, min_folds=3, min_samples_per_fold=30):
        captured["baseline_folds"] = baseline_folds
        captured["candidate_folds"] = candidate_folds
        captured["minimums"] = (min_folds, min_samples_per_fold)
        return {"ok": True, "walk_forward_validated": True, "test_passed": True}
    monkeypatch.setattr(PersistentStrategyLearning, "validate_walk_forward", fake_validate)

    response = asyncio.run(star_app.strategy_proposal_historical_walk_forward(None))
    result = _json(response)
    assert response.status_code == 200
    assert captured["params"] == [{"min_confirmations": 2}, {"min_confirmations": 3}]
    assert captured["baseline_folds"][0] == {
        "expectancy": 0.1, "max_drawdown": 0.1, "sample_count": 50,
    }
    assert captured["candidate_folds"][0] == {
        "expectancy": 0.2, "max_drawdown": 0.08, "sample_count": 50,
    }
    assert captured["minimums"] == (3, 30)
    assert result["data_source"] == "binance_spot_public_read_only"
    assert result["no_live_orders"] is True
    assert proposal.evidence["walk_forward"]["no_lookahead"] is True
    assert proposal.evidence["metrics_before"]["expectancy"] == 0.1


def test_historical_learning_walk_forward_requires_three_oos_folds_and_valid_proposal(monkeypatch):
    proposal = SimpleNamespace(category="strategy_proposal", subject="trend_following_ema",
                               evidence={"current_params": {"min_confirmations": 2},
                                         "proposed_params": {"min_confirmations": 3}})
    class FakeDB:
        def get(self, model, record_id): return proposal
        def close(self): pass
        def rollback(self): pass
    monkeypatch.setattr(star_app, "db_sess", lambda: FakeDB())
    monkeypatch.setattr(star_app, "require_user", lambda request, db: (None, None))
    async def request_body(_request):
        return {"proposal_id": 1, "limit": 120, "train_window": 80, "test_window": 30, "step": 30}
    monkeypatch.setattr(star_app, "body", request_body)
    response = asyncio.run(star_app.strategy_proposal_historical_walk_forward(None))
    assert response.status_code == 400
    assert _json(response)["error"] == "historical_limit_and_step_must_cover_at_least_three_oos_folds"


def test_strategy_proposal_create_accepts_only_supported_different_parameter_sets(monkeypatch):
    class FakeDB:
        def close(self): pass
    captured = {}
    monkeypatch.setattr(star_app, "db_sess", lambda: FakeDB())
    monkeypatch.setattr(star_app, "require_user", lambda request, db: (None, None))
    async def request_body(_request):
        return {
            "strategy_key": "trend_following_ema",
            "current_params": {"min_confirmations": 2},
            "proposed_params": {"min_confirmations": 3},
            "rationale": "Validate a stricter trend confirmation threshold",
            "metrics_before": {},
        }
    monkeypatch.setattr(star_app, "body", request_body)
    def create(self, db, strategy_key, current_params, proposed_params, rationale, metrics_before):
        captured.update({
            "strategy_key": strategy_key, "current_params": current_params,
            "proposed_params": proposed_params, "rationale": rationale,
        })
        return {"ok": True, "proposal_id": 9}
    monkeypatch.setattr(PersistentStrategyLearning, "create_proposal", create)
    response = asyncio.run(star_app.strategy_proposal_create(None))
    assert response.status_code == 200
    assert captured["current_params"] == {"min_confirmations": 2}
    assert captured["proposed_params"] == {"min_confirmations": 3}

    async def invalid_body(_request):
        return {
            "strategy_key": "trend_following_ema",
            "current_params": {"fast": 9},
            "proposed_params": {"min_confirmations": 3},
            "rationale": "invalid parameter set", "metrics_before": {},
        }
    monkeypatch.setattr(star_app, "body", invalid_body)
    rejected = asyncio.run(star_app.strategy_proposal_create(None))
    assert rejected.status_code == 400
    assert _json(rejected)["error"] == "invalid_strategy_parameter_set"


def test_client_supplied_walk_forward_metrics_cannot_validate_a_proposal(monkeypatch):
    class FakeDB:
        def close(self): pass
    monkeypatch.setattr(star_app, "db_sess", lambda: FakeDB())
    monkeypatch.setattr(star_app, "require_user", lambda request, db: (None, None))
    response = asyncio.run(star_app.strategy_proposal_walk_forward(None))
    assert response.status_code == 410
    result = _json(response)
    assert result["error"] == "historical_walk_forward_required"
    assert result["manual_fold_metrics_accepted"] is False
