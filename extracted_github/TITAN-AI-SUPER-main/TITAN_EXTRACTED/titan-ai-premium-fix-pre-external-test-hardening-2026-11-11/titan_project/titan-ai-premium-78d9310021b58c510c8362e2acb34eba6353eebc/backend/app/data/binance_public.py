"""Binance Public Data Adapter - Read-only, no API keys
Only public endpoints. No order placement.
"""
from __future__ import annotations

import json
import math
import os
import urllib.request
from urllib.parse import urlencode
from typing import Any


class BinancePublicData:
    BASE = os.getenv("BINANCE_PUBLIC_BASE_URL", "https://data-api.binance.vision")

    def _get_json(self, endpoint: str) -> Any:
        url = f"{self.BASE}{endpoint}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "TITAN-AI-PREMIUM/1.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read().decode())
        except Exception as e:
            raise RuntimeError(f"binance_public_error:{e}")

    def get_spot_exchange_info(self) -> dict[str, Any]:
        """Read-only Spot symbol metadata from Binance public API."""
        data = self._get_json("/api/v3/exchangeInfo")
        if not isinstance(data, dict) or not isinstance(data.get("symbols"), list):
            raise RuntimeError("binance_public_invalid_exchange_info")
        return data

    def get_24h_tickers(self) -> list[dict[str, Any]]:
        """Read-only 24h ticker statistics for all symbols."""
        data = self._get_json("/api/v3/ticker/24hr")
        if not isinstance(data, list):
            raise RuntimeError("binance_public_invalid_24h_tickers")
        return data

    def get_klines(self, symbol: str, interval: str, limit: int = 500) -> list[list[Any]]:
        # Keep Spot public candles aligned with all 15 project timeframes.
        normalized = str(symbol or "").replace("/", "").upper()
        allowed_intervals = {
            "1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h",
            "6h", "8h", "12h", "1d", "3d", "1w", "1M",
        }
        if (not normalized.isascii() or not normalized.isalnum() or not 5 <= len(normalized) <= 30
                or not isinstance(interval, str) or interval not in allowed_intervals
                or isinstance(limit, bool) or not isinstance(limit, int) or not 30 <= limit <= 1000):
            raise ValueError("invalid_klines_symbol_interval_or_limit")
        url = f"{self.BASE}/api/v3/klines?" + urlencode({
            "symbol": normalized, "interval": interval, "limit": limit,
        })
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "TITAN-AI-PREMIUM/1.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
            if not isinstance(data, list):
                raise RuntimeError("binance_public_invalid_klines")
            return data
        except Exception as e:
            raise RuntimeError(f"binance_public_error:{e}")

    def get_order_book(self, symbol: str, limit: int = 100) -> dict[str, Any]:
        """Read-only Spot order-book depth snapshot."""
        normalized = str(symbol or "").replace("/", "").upper()
        allowed_limits = {5, 10, 20, 50, 100, 500, 1000}
        if (not normalized.isascii() or not normalized.isalnum() or not 5 <= len(normalized) <= 30
                or isinstance(limit, bool) or not isinstance(limit, int) or limit not in allowed_limits):
            raise ValueError("invalid_order_book_symbol_or_limit")
        data = self._get_json("/api/v3/depth?" + urlencode({"symbol": normalized, "limit": limit}))
        if not isinstance(data, dict) or not isinstance(data.get("bids"), list) or not isinstance(data.get("asks"), list):
            raise RuntimeError("binance_public_invalid_order_book")
        return data

    def get_aggregate_trades(self, symbol: str, limit: int = 500) -> list[dict[str, Any]]:
        """Read-only recent aggregate trades for taker-flow analysis."""
        normalized = str(symbol or "").replace("/", "").upper()
        if (not normalized.isascii() or not normalized.isalnum() or not 5 <= len(normalized) <= 30
                or isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000):
            raise ValueError("invalid_aggregate_trade_symbol_or_limit")
        data = self._get_json("/api/v3/aggTrades?" + urlencode({"symbol": normalized, "limit": limit}))
        if not isinstance(data, list) or any(not isinstance(item, dict) for item in data):
            raise RuntimeError("binance_public_invalid_aggregate_trades")
        return data

    def get_aggregate_trades_history(
        self, symbol: str, *, start_time: int, end_time: int, max_trades: int = 20000,
    ) -> list[dict[str, Any]]:
        """Fetch bounded historical aggregate trades with ID-based pagination."""
        normalized = str(symbol or "").replace("/", "").upper()
        if (not normalized.isascii() or not normalized.isalnum() or not 5 <= len(normalized) <= 30
                or isinstance(start_time, bool) or isinstance(end_time, bool)
                or not isinstance(start_time, int) or not isinstance(end_time, int)
                or start_time <= 0 or end_time <= start_time
                or end_time - start_time > 7 * 24 * 60 * 60 * 1000
                or isinstance(max_trades, bool) or not isinstance(max_trades, int)
                or not 100 <= max_trades <= 50000):
            raise ValueError("invalid_historical_aggregate_trade_range_or_limit")
        output: list[dict[str, Any]] = []
        from_id = None
        for page_number in range((max_trades + 999) // 1000):
            params = {"symbol": normalized, "limit": min(1000, max_trades - len(output))}
            if from_id is None:
                params["startTime"] = start_time
            else:
                params["fromId"] = from_id
            data = self._get_json("/api/v3/aggTrades?" + urlencode(params))
            if not isinstance(data, list) or any(not isinstance(item, dict) for item in data):
                raise RuntimeError("binance_public_invalid_historical_aggregate_trades")
            if not data:
                break
            last_id = None
            reached_end = False
            for item in data:
                if any(isinstance(item.get(key), bool) for key in ("a", "T")):
                    raise RuntimeError("binance_public_invalid_historical_aggregate_trade")
                try:
                    trade_id = int(item["a"])
                    stamp = int(item["T"])
                except (KeyError, TypeError, ValueError, OverflowError) as exc:
                    raise RuntimeError("binance_public_invalid_historical_aggregate_trade") from exc
                if trade_id < 0 or stamp <= 0:
                    raise RuntimeError("binance_public_invalid_historical_aggregate_trade")
                last_id = trade_id
                if stamp > end_time:
                    reached_end = True
                    break
                if stamp >= start_time:
                    output.append(item)
                    if len(output) >= max_trades:
                        break
            if len(output) >= max_trades or reached_end or len(data) < 1000:
                break
            if last_id is None:
                break
            from_id = last_id + 1
        output.sort(key=lambda row: (int(row["T"]), int(row["a"])))
        if any(int(output[i]["a"]) <= int(output[i - 1]["a"]) for i in range(1, len(output))):
            raise RuntimeError("binance_public_historical_aggregate_trade_ids_not_increasing")
        return output

    def klines_to_candles(self, klines: list[list[Any]]) -> list[dict]:
        if not isinstance(klines, list):
            raise RuntimeError("binance_public_invalid_klines_container")
        candles = []
        previous_timestamp = None
        for index, row in enumerate(klines):
            if not isinstance(row, (list, tuple)) or len(row) < 7:
                raise RuntimeError(f"binance_public_invalid_kline_row:{index}")
            if any(isinstance(value, bool) for value in row[:7]):
                raise RuntimeError(f"binance_public_invalid_kline_boolean:{index}")
            try:
                timestamp = int(row[0])
                close_time = int(row[6])
                open_price, high, low, close, volume = (
                    float(row[1]), float(row[2]), float(row[3]), float(row[4]), float(row[5])
                )
            except (TypeError, ValueError, OverflowError):
                raise RuntimeError(f"binance_public_invalid_kline_values:{index}")
            numeric = (open_price, high, low, close, volume)
            if (timestamp <= 0 or close_time < timestamp
                    or (previous_timestamp is not None and timestamp <= previous_timestamp)
                    or not all(math.isfinite(value) for value in numeric)
                    or min(open_price, high, low, close) <= 0 or volume < 0
                    or high < max(open_price, close, low)
                    or low > min(open_price, close, high)):
                raise RuntimeError(f"binance_public_invalid_kline_integrity:{index}")
            candles.append({
                "timestamp": timestamp, "close_time": close_time,
                "open": open_price, "high": high, "low": low,
                "close": close, "volume": volume, "source": "binance_public",
            })
            previous_timestamp = timestamp
        return candles
