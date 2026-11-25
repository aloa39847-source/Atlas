"""Read-only Binance USD-M Futures public market data; no keys or execution."""
from __future__ import annotations

import json
import math
import os
import time
import urllib.request
from urllib.parse import urlencode

from app.data.binance_public import BinancePublicData


class BinanceFuturesPublicData:
    BASE = os.getenv("BINANCE_FUTURES_PUBLIC_BASE_URL", "https://fapi.binance.com")
    ALLOWED_INTERVALS = {"1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d", "3d", "1w", "1M"}
    ALLOWED_DEPTH_LIMITS = {5, 10, 20, 50, 100, 500, 1000}

    def _get_json(self, endpoint: str):
        url = f"{self.BASE.rstrip('/')}{endpoint}"
        request = urllib.request.Request(url, headers={"User-Agent": "TITAN-AI-PREMIUM/1.0"})
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                raw = response.read(5_000_001)
            if len(raw) > 5_000_000:
                raise RuntimeError("binance_futures_public_response_too_large")
            return json.loads(raw.decode("utf-8"))
        except Exception as exc:
            raise RuntimeError(f"binance_futures_public_error:{type(exc).__name__}") from exc

    @staticmethod
    def _symbol(symbol: str) -> str:
        normalized = str(symbol or "").replace("/", "").upper()
        if not normalized.isascii() or not normalized.isalnum() or not 5 <= len(normalized) <= 30:
            raise ValueError("invalid_futures_symbol")
        return normalized

    def get_exchange_info(self):
        """Return public USD-M contract metadata for safe universe discovery."""
        data = self._get_json("/fapi/v1/exchangeInfo")
        if not isinstance(data, dict) or not isinstance(data.get("symbols"), list):
            raise RuntimeError("binance_futures_public_invalid_exchange_info")
        return data

    def get_24h_tickers(self):
        """Return public 24h USD-M ticker statistics; callers must validate each row."""
        data = self._get_json("/fapi/v1/ticker/24hr")
        if not isinstance(data, list) or any(not isinstance(item, dict) for item in data):
            raise RuntimeError("binance_futures_public_invalid_24h_tickers")
        return data

    def get_klines(self, symbol: str, interval: str, limit: int = 500):
        normalized = self._symbol(symbol)
        if (not isinstance(interval, str) or interval not in self.ALLOWED_INTERVALS or isinstance(limit, bool)
                or not isinstance(limit, int) or not 30 <= limit <= 1000):
            raise ValueError("invalid_futures_klines_interval_or_limit")
        data = self._get_json("/fapi/v1/klines?" + urlencode({
            "symbol": normalized, "interval": interval, "limit": limit,
        }))
        if not isinstance(data, list):
            raise RuntimeError("binance_futures_public_invalid_klines")
        return data

    def klines_to_candles(self, klines):
        return BinancePublicData().klines_to_candles(klines)

    def get_order_book(self, symbol: str, limit: int = 100):
        normalized = self._symbol(symbol)
        if isinstance(limit, bool) or not isinstance(limit, int) or limit not in self.ALLOWED_DEPTH_LIMITS:
            raise ValueError("invalid_futures_order_book_limit")
        data = self._get_json("/fapi/v1/depth?" + urlencode({
            "symbol": normalized, "limit": limit,
        }))
        if not isinstance(data, dict) or not isinstance(data.get("bids"), list) or not isinstance(data.get("asks"), list):
            raise RuntimeError("binance_futures_public_invalid_order_book")
        return data

    def get_aggregate_trades(self, symbol: str, limit: int = 500):
        normalized = self._symbol(symbol)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise ValueError("invalid_futures_aggregate_trade_limit")
        data = self._get_json("/fapi/v1/aggTrades?" + urlencode({
            "symbol": normalized, "limit": limit,
        }))
        if not isinstance(data, list) or any(not isinstance(item, dict) for item in data):
            raise RuntimeError("binance_futures_public_invalid_aggregate_trades")
        return data

    def get_aggregate_trades_history(
        self, symbol: str, *, start_time: int, end_time: int, max_trades: int = 20000,
    ) -> list[dict]:
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
            data = self._get_json("/fapi/v1/aggTrades?" + urlencode(params))
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

    def get_funding_rate_history(
        self, symbol: str, *, start_time: int, end_time: int, limit: int = 1000,
    ) -> list[dict]:
        normalized = self._symbol(symbol)
        if (isinstance(start_time, bool) or isinstance(end_time, bool)
                or not isinstance(start_time, int) or not isinstance(end_time, int)
                or start_time <= 0 or end_time < start_time
                or isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000):
            raise ValueError("invalid_futures_funding_history_range_or_limit")
        data = self._get_json("/fapi/v1/fundingRate?" + urlencode({
            "symbol": normalized, "startTime": start_time,
            "endTime": end_time, "limit": limit,
        }))
        if not isinstance(data, list):
            raise RuntimeError("binance_futures_public_invalid_funding_history")
        output = []
        for row in data:
            if (not isinstance(row, dict)
                    or any(isinstance(row.get(key), bool) for key in ("fundingTime", "fundingRate"))):
                raise RuntimeError("binance_futures_public_invalid_funding_row")
            try:
                stamp = float(row["fundingTime"])
                rate = float(row["fundingRate"])
            except (KeyError, TypeError, ValueError, OverflowError) as exc:
                raise RuntimeError("binance_futures_public_invalid_funding_row") from exc
            if (not math.isfinite(stamp) or not stamp.is_integer()
                    or not math.isfinite(rate) or stamp < start_time or stamp > end_time
                    or abs(rate) > 0.05):
                raise RuntimeError("binance_futures_public_invalid_funding_row")
            output.append({
                "symbol": normalized, "timestamp": int(stamp), "rate": rate,
                "source": "binance_usdm_futures_public_read_only",
            })
        output.sort(key=lambda item: item["timestamp"])
        if any(output[i]["timestamp"] <= output[i - 1]["timestamp"] for i in range(1, len(output))):
            raise RuntimeError("binance_futures_public_duplicate_funding_timestamps")
        return output

    def get_mark_prices(self, symbols: set[str]) -> dict[str, float]:
        if not symbols:
            return {}
        wanted = {self._symbol(symbol): symbol for symbol in symbols}
        data = self._get_json("/fapi/v1/premiumIndex")
        if not isinstance(data, list):
            raise RuntimeError("binance_futures_public_invalid_mark_prices")
        now_ms = time.time() * 1000.0
        prices: dict[str, float] = {}
        for row in data:
            if (not isinstance(row, dict) or str(row.get("symbol", "")).upper() not in wanted
                    or any(isinstance(row.get(key), bool) for key in ("markPrice", "indexPrice", "lastFundingRate", "time"))):
                continue
            try:
                mark = float(row["markPrice"])
                index = float(row["indexPrice"])
                funding = float(row["lastFundingRate"])
                stamp = float(row["time"])
            except (KeyError, TypeError, ValueError, OverflowError):
                continue
            age = now_ms - stamp
            if (not all(math.isfinite(value) for value in (mark, index, funding, stamp))
                    or mark <= 0 or index <= 0 or stamp <= 0 or age < -60_000 or age > 120_000):
                continue
            prices[wanted[str(row["symbol"]).upper()]] = mark
        return prices

    def get_mark_price(self, symbol: str):
        normalized = self._symbol(symbol)
        data = self._get_json("/fapi/v1/premiumIndex?" + urlencode({"symbol": normalized}))
        if not isinstance(data, dict):
            raise RuntimeError("binance_futures_public_invalid_mark_price")
        if any(isinstance(data.get(key), bool) for key in ("markPrice", "indexPrice", "lastFundingRate", "time")):
            raise RuntimeError("binance_futures_public_invalid_mark_price")
        try:
            mark = float(data["markPrice"])
            index = float(data["indexPrice"])
            funding = float(data["lastFundingRate"])
            stamp = float(data["time"])
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise RuntimeError("binance_futures_public_invalid_mark_price") from exc
        age = time.time() * 1000.0 - stamp
        if (not all(math.isfinite(value) for value in (mark, index, funding, stamp, age))
                or mark <= 0 or index <= 0 or stamp <= 0 or age < -60_000 or age > 120_000):
            raise RuntimeError("binance_futures_public_invalid_or_stale_mark_price")
        return {
            "symbol": normalized, "mark_price": mark, "index_price": index,
            "last_funding_rate": funding, "next_funding_time": data.get("nextFundingTime"),
            "time": stamp, "source": "binance_usdm_futures_public_read_only",
        }
