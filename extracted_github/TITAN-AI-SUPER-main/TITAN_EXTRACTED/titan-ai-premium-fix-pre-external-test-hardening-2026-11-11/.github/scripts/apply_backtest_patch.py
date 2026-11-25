from pathlib import Path
import sys

root = Path(sys.argv[1])

BACKTEST_SOURCE = r'''"""Candle-driven backtesting with next-bar entries and conservative stop handling.
This is a basic simulator, not a walk-forward or exchange-accurate engine.
"""
from __future__ import annotations

from typing import Any
from dataclasses import dataclass
import math


@dataclass
class BacktestResult:
    total_trades: int
    win_rate: float
    profit_factor: float | None
    expectancy: float
    max_drawdown: float
    total_return: float
    fees_paid: float
    slippage_cost: float
    sharpe: float | None


class BacktestEngine:
    def __init__(self, fee_rate: float = 0.001, slippage_rate: float = 0.0005):
        self.fee_rate = max(0.0, float(fee_rate))
        self.slippage_rate = max(0.0, float(slippage_rate))

    @staticmethod
    def _signal_index(signal: dict, timestamp_index: dict) -> int | None:
        raw = signal.get("index", signal.get("candle_index"))
        if raw is not None:
            try:
                return int(raw)
            except (TypeError, ValueError):
                return None
        ts = signal.get("timestamp")
        return timestamp_index.get(str(ts)) if ts is not None else None

    def run(
        self,
        candles: list[dict],
        signals: list[dict],
        *,
        initial_equity: float = 10_000.0,
        market_type: str = "spot",
    ) -> dict[str, Any]:
        try:
            initial_equity = float(initial_equity)
        except (TypeError, ValueError):
            initial_equity = 0.0
        if not math.isfinite(initial_equity) or initial_equity <= 0:
            return {"error": "invalid_initial_equity", "total_trades": 0, "trades": []}

        required = ("open", "high", "low", "close")
        clean = []
        invalid_candles = 0
        for candle in candles or []:
            try:
                row = {k: float(candle[k]) for k in required}
            except (KeyError, TypeError, ValueError):
                invalid_candles += 1
                continue
            if any(not math.isfinite(v) or v <= 0 for v in row.values()):
                invalid_candles += 1
                continue
            row["timestamp"] = candle.get("timestamp")
            clean.append(row)
        if invalid_candles:
            return {
                "error": "invalid_candle_data",
                "invalid_candles": invalid_candles,
                "total_trades": 0,
                "trades": [],
                "walk_forward_ready": False,
                "out_of_sample_ready": False,
                "no_lookahead": False,
            }

        equity = initial_equity
        peak = equity
        max_dd = 0.0
        fees_total = 0.0
        slippage_total = 0.0
        trades: list[dict[str, Any]] = []
        skipped_unanchored = 0
        skipped_spot_shorts = 0
        timestamp_index = {str(c.get("timestamp")): i for i, c in enumerate(clean) if c.get("timestamp") is not None}
        indexed = []
        for sig in signals or []:
            if not isinstance(sig, dict):
                skipped_unanchored += 1
                continue
            idx = self._signal_index(sig, timestamp_index)
            if idx is None or idx < 0 or idx >= len(clean):
                skipped_unanchored += 1
                continue
            action = str(sig.get("action", "")).strip().lower()
            if action in {"buy", "long"}:
                side = "long"
            elif action in {"sell", "short"}:
                side = "short"
            else:
                continue
            if side == "short" and market_type.lower() != "futures":
                skipped_spot_shorts += 1
                continue
            indexed.append((idx, side, sig))
        indexed.sort(key=lambda item: item[0])

        next_entry_after = -1
        for signal_pos, (sig_idx, side, sig) in enumerate(indexed):
            entry_idx = sig_idx + 1
            if entry_idx >= len(clean) or sig_idx <= next_entry_after:
                continue
            entry_raw = clean[entry_idx]["open"]
            try:
                qty = float(sig.get("quantity", 1.0) or 0.0)
                stop = float(sig["stop_loss"]) if sig.get("stop_loss") is not None else None
                target = float(sig["take_profit"]) if sig.get("take_profit") is not None else None
            except (TypeError, ValueError):
                continue
            if qty <= 0 or not math.isfinite(qty):
                continue

            opposite_idx = None
            for later_idx, later_side, _ in indexed[signal_pos + 1:]:
                if later_idx >= entry_idx and later_side != side:
                    opposite_idx = later_idx
                    break
            scan_end = opposite_idx if opposite_idx is not None else len(clean) - 1
            exit_raw = None
            exit_idx = min(opposite_idx + 1, len(clean) - 1) if opposite_idx is not None else len(clean) - 1
            exit_reason = "opposite_signal" if opposite_idx is not None else "end_of_data"

            for bar_idx in range(entry_idx, scan_end + 1):
                bar = clean[bar_idx]
                hit_stop = hit_target = False
                stop_px = target_px = None
                if side == "long":
                    if stop is not None and (bar["open"] <= stop or bar["low"] <= stop):
                        hit_stop = True
                        stop_px = bar["open"] if bar["open"] <= stop else stop
                    if target is not None and (bar["open"] >= target or bar["high"] >= target):
                        hit_target = True
                        target_px = bar["open"] if bar["open"] >= target else target
                else:
                    if stop is not None and (bar["open"] >= stop or bar["high"] >= stop):
                        hit_stop = True
                        stop_px = bar["open"] if bar["open"] >= stop else stop
                    if target is not None and (bar["open"] <= target or bar["low"] <= target):
                        hit_target = True
                        target_px = bar["open"] if bar["open"] <= target else target
                if hit_stop:
                    exit_raw, exit_idx, exit_reason = stop_px, bar_idx, "stop_loss"
                    break
                if hit_target:
                    exit_raw, exit_idx, exit_reason = target_px, bar_idx, "take_profit"
                    break

            if exit_raw is None:
                if opposite_idx is not None and opposite_idx + 1 < len(clean):
                    exit_idx = opposite_idx + 1
                    exit_raw = clean[exit_idx]["open"]
                else:
                    exit_idx = len(clean) - 1
                    exit_raw = clean[exit_idx]["close"]

            if side == "long":
                entry_exec = entry_raw * (1 + self.slippage_rate)
                exit_exec = exit_raw * (1 - self.slippage_rate)
                gross = (exit_exec - entry_exec) * qty
            else:
                entry_exec = entry_raw * (1 - self.slippage_rate)
                exit_exec = exit_raw * (1 + self.slippage_rate)
                gross = (entry_exec - exit_exec) * qty
            fees = (entry_exec + exit_exec) * qty * self.fee_rate
            net = gross - fees
            slip = (abs(entry_exec - entry_raw) + abs(exit_exec - exit_raw)) * qty
            equity += net
            peak = max(peak, equity)
            drawdown = (peak - equity) / peak if peak > 0 else 0.0
            max_dd = max(max_dd, drawdown)
            fees_total += fees
            slippage_total += slip
            trades.append({
                "side": side, "signal_index": sig_idx, "entry_index": entry_idx,
                "exit_index": exit_idx, "entry_price": entry_exec, "exit_price": exit_exec,
                "quantity": qty, "gross_pnl": gross, "fees": fees,
                "slippage_cost": slip, "net_pnl": net, "exit_reason": exit_reason,
            })
            next_entry_after = exit_idx

        pnls = [t["net_pnl"] for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        gross_profit = sum(wins)
        gross_loss = abs(sum(losses))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else (None if gross_profit > 0 else 0.0)
        mean_pnl = sum(pnls) / len(pnls) if pnls else 0.0
        variance = sum((p - mean_pnl) ** 2 for p in pnls) / len(pnls) if pnls else 0.0
        sharpe = (mean_pnl / math.sqrt(variance) * math.sqrt(len(pnls))) if len(pnls) > 1 and variance > 0 else None
        return {
            "total_trades": len(trades),
            "win_rate": round(len(wins) / len(trades), 4) if trades else 0.0,
            "profit_factor": round(profit_factor, 4) if profit_factor is not None else None,
            "expectancy": round(mean_pnl, 8),
            "max_drawdown": round(max_dd, 4),
            "total_return": round((equity - initial_equity) / initial_equity, 6),
            "fees_paid": round(fees_total, 8),
            "slippage_cost": round(slippage_total, 8),
            "sharpe": round(sharpe, 4) if sharpe is not None else None,
            "trades": trades,
            "skipped_unanchored_signals": skipped_unanchored,
            "skipped_spot_shorts": skipped_spot_shorts,
            "walk_forward_ready": False,
            "out_of_sample_ready": False,
            "no_lookahead": True,
            "assumptions": [
                "Signals are evaluated at candle close and entries occur at the next candle open.",
                "When stop and target are both touched in one candle, stop-loss is assumed first.",
                "Fees and slippage are simplified configurable estimates; funding and exchange filters are not modeled.",
                "This engine does not perform walk-forward or out-of-sample validation.",
            ],
        }
'''

TEST_SOURCE = r'''from app.engines.backtest import BacktestEngine


def _candles():
    return [
        {"timestamp": str(i), "open": o, "high": h, "low": l, "close": c}
        for i, (o, h, l, c) in enumerate([
            (100, 101, 99, 100),
            (100, 103, 99, 102),
            (102, 106, 101, 105),
            (105, 107, 104, 106),
            (106, 108, 105, 107),
        ])
    ]


def test_backtest_enters_next_bar_and_uses_real_candle_exit():
    result = BacktestEngine(fee_rate=0, slippage_rate=0).run(
        _candles(), [{"index": 0, "action": "buy", "quantity": 1}],
    )
    assert result["total_trades"] == 1
    trade = result["trades"][0]
    assert trade["entry_index"] == 1
    assert trade["entry_price"] == 100
    assert trade["exit_price"] == 107
    assert trade["exit_reason"] == "end_of_data"
    assert result["no_lookahead"] is True
    assert result["walk_forward_ready"] is False
    assert result["out_of_sample_ready"] is False


def test_backtest_stop_loss_wins_if_stop_and_target_touch_same_candle():
    candles = _candles()
    candles[1].update({"high": 110, "low": 90})
    result = BacktestEngine(fee_rate=0, slippage_rate=0).run(
        candles, [{"index": 0, "action": "buy", "quantity": 1, "stop_loss": 95, "take_profit": 108}],
    )
    assert result["trades"][0]["exit_reason"] == "stop_loss"
    assert result["trades"][0]["exit_price"] == 95


def test_backtest_spot_skips_short_signals_but_futures_accepts_them():
    signals = [{"index": 0, "action": "short", "quantity": 1}]
    spot = BacktestEngine(fee_rate=0, slippage_rate=0).run(_candles(), signals, market_type="spot")
    futures = BacktestEngine(fee_rate=0, slippage_rate=0).run(_candles(), signals, market_type="futures")
    assert spot["total_trades"] == 0
    assert spot["skipped_spot_shorts"] == 1
    assert futures["total_trades"] == 1


def test_backtest_skips_unanchored_signals_instead_of_guessing_timing():
    result = BacktestEngine().run(_candles(), [{"action": "buy", "price": 100}])
    assert result["total_trades"] == 0
    assert result["skipped_unanchored_signals"] == 1
'''

(root / "app/engines/backtest.py").write_text(BACKTEST_SOURCE)
(root / "tests/test_backtest_engine.py").write_text(TEST_SOURCE)

core = root / "app/services/core_service.py"
text = core.read_text()
text = text.replace(
    '("backtesting", "Backtesting Engine", "not_implemented"),',
    '("backtesting", "Backtesting Engine", "implemented_partial"),',
)
core.write_text(text)
