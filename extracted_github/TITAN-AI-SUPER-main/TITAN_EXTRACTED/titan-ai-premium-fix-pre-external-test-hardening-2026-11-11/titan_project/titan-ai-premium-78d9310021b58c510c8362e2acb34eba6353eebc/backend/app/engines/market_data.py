from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence

from app.core.enums import DataSourceKind, EngineStatus, Timeframe
from app.data.binance_public import BinancePublicData
from app.data.binance_futures_public import BinanceFuturesPublicData
from .base import Engine


class Candles:
    """Lightweight pure-Python candle container (pandas-free for portability)."""

    def __init__(self, rows: list[dict[str, Any]]):
        self.rows = rows
        self._cols = {k: [r.get(k) for r in rows] for k in (rows[0].keys() if rows else ["timestamp", "open", "high", "low", "close", "volume"])}
        self.iloc = _ILoc(self)

    def __len__(self) -> int:
        return len(self.rows)

    def __getitem__(self, key: str):
        return self._cols[key]

    @property
    def columns(self):
        return list(self._cols.keys())

    def to_records(self) -> list[dict[str, Any]]:
        return list(self.rows)


class _ILoc:
    def __init__(self, c: Candles):
        self.c = c

    def __getitem__(self, idx: int) -> dict[str, Any]:
        return self.c.rows[idx]


@dataclass
class CandleBatch:
    symbol: str
    timeframe: Timeframe
    df: Candles
    source: DataSourceKind
    note: str
    market_type: str = "spot"


class MarketDataEngine(Engine):
    """Market data access layer.

    Provides clearly labeled synthetic candles and read-only Binance Public klines.
    Public market data needs no API key; authenticated account data and order execution are separate.
    """

    key = "market_data"
    name = "Market Data Engine"

    def status(self) -> EngineStatus:
        return EngineStatus.IMPLEMENTED_PARTIAL

    def get_synthetic_candles(self, symbol: str, timeframe: Timeframe, limit: int = 250, seed: int | None = None, market_type: str = "spot") -> CandleBatch:
        if not isinstance(timeframe, Timeframe):
            timeframe = Timeframe(str(timeframe))
        rng = random.Random(seed)
        tf_steps = {
            Timeframe.M1: (45000.0, 0.00025),
            Timeframe.M3: (45000.0, 0.00040),
            Timeframe.M5: (45000.0, 0.00060),
            Timeframe.M15: (45000.0, 0.00120),
            Timeframe.M30: (45000.0, 0.00150),
            Timeframe.H1: (45000.0, 0.00200),
            Timeframe.H2: (45000.0, 0.00300),
            Timeframe.H4: (45000.0, 0.00400),
            Timeframe.H6: (45000.0, 0.00500),
            Timeframe.H8: (45000.0, 0.00600),
            Timeframe.H12: (45000.0, 0.00700),
            Timeframe.D1: (45000.0, 0.01200),
            Timeframe.D3: (45000.0, 0.01800),
            Timeframe.W1: (45000.0, 0.02500),
            Timeframe.MO1: (45000.0, 0.03500),
        }
        base, vol = tf_steps[timeframe]
        sym_offset = (sum(ord(c) for c in symbol) % 5000) - 2500
        price = base + sym_offset
        rows = []
        ts = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        step_minutes = {
            "1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30,
            "1h": 60, "2h": 120, "4h": 240, "6h": 360, "8h": 480,
            "12h": 720, "1d": 1440, "3d": 4320, "1w": 10080,
            "1M": 43200,
        }[timeframe.value]
        for i in range(limit):
            drift = 0.0001 if (i % 11) < 6 else -0.00008
            shock = rng.gauss(0, vol)
            close = max(0.01, price * (1 + drift + shock))
            open_ = price
            high = max(open_, close) * (1 + abs(rng.gauss(0, vol / 2)))
            low = min(open_, close) * (1 - abs(rng.gauss(0, vol / 2)))
            volume = max(1.0, 100 + rng.gauss(0, 15) + (i % 7) * 3)
            rows.append(
                {
                    "timestamp": (ts - timedelta(minutes=step_minutes * (limit - i))).isoformat(),
                    "open": open_, "high": high, "low": min(low, open_, close), "close": close, "volume": volume,
                }
            )
            price = close
        return CandleBatch(
            symbol=symbol, timeframe=timeframe, df=Candles(rows),
            source=DataSourceKind.SYNTHETIC_LABELED,
            note="SYNTHETIC_LABELED data for development/testing only. NOT live market data.",
            market_type=market_type,
        )

    def get_multi_timeframe(
        self,
        symbol: str,
        limit: int = 250,
        seed: int | None = 42,
        market_type: str = "spot",
        timeframes: Sequence[Timeframe] | None = None,
    ) -> dict[Timeframe, CandleBatch]:
        selected = timeframes if timeframes is not None else tuple(Timeframe)
        normalized = []
        for value in selected:
            tf = value if isinstance(value, Timeframe) else Timeframe(str(value))
            if tf not in normalized:
                normalized.append(tf)
        if not normalized:
            raise ValueError("at_least_one_timeframe_required")
        return {
            tf: self.get_synthetic_candles(
                symbol, tf, limit=limit,
                seed=(seed + i) if seed is not None else None,
                market_type=market_type,
            )
            for i, tf in enumerate(normalized)
        }

    def scan_spot_universe(self, *, max_symbols: int = 50, min_quote_volume_usdt: float = 1_000_000.0) -> dict[str, Any]:
        """Rank liquid Binance USDT Spot symbols using public 24h data only.

        This is a discovery shortlist, not a trade signal. It never reads account
        balances or places orders.
        """
        max_symbols = max(1, min(int(max_symbols), 100))
        min_volume = max(0.0, float(min_quote_volume_usdt))
        adapter = BinancePublicData()
        exchange_info = adapter.get_spot_exchange_info()
        tickers = adapter.get_24h_tickers()
        ticker_by_symbol = {
            str(item.get("symbol", "")).upper(): item
            for item in tickers if isinstance(item, dict) and item.get("symbol")
        }
        excluded_suffixes = ("UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT")
        candidates: list[dict[str, Any]] = []
        for item in exchange_info.get("symbols", []):
            if not isinstance(item, dict):
                continue
            symbol = str(item.get("symbol", "")).upper()
            if (item.get("status") != "TRADING" or item.get("quoteAsset") != "USDT"
                    or item.get("isSpotTradingAllowed") is False or not symbol
                    or symbol.endswith(excluded_suffixes)):
                continue
            ticker = ticker_by_symbol.get(symbol)
            if not ticker:
                continue
            try:
                quote_volume = float(ticker.get("quoteVolume", 0) or 0)
                last_price = float(ticker.get("lastPrice", 0) or 0)
                price_change = float(ticker.get("priceChangePercent", 0) or 0)
                base_volume = float(ticker.get("volume", 0) or 0)
                trade_count = int(ticker.get("count", 0) or 0)
            except (TypeError, ValueError):
                continue
            if (not all(math.isfinite(value) for value in (quote_volume, last_price, price_change, base_volume))
                    or quote_volume < min_volume or last_price <= 0 or trade_count < 0):
                continue
            candidates.append({
                "symbol": f"{item.get('baseAsset')}/USDT",
                "exchange_symbol": symbol,
                "base_asset": item.get("baseAsset"),
                "quote_asset": "USDT",
                "last_price": last_price,
                "price_change_percent_24h": price_change,
                "base_volume_24h": base_volume,
                "quote_volume_usdt_24h": quote_volume,
                "trades_24h": trade_count,
                "rank_metric": "quote_volume_usdt_24h",
            })
        candidates.sort(key=lambda row: row["quote_volume_usdt_24h"], reverse=True)
        return {
            "ok": True,
            "source": "binance_public_read_only",
            "market": "spot",
            "quote_asset": "USDT",
            "min_quote_volume_usdt": min_volume,
            "returned": min(len(candidates), max_symbols),
            "available_candidates": len(candidates),
            "items": candidates[:max_symbols],
            "note": "Liquidity-ranked discovery shortlist only; not a buy/sell signal. Public market data only; no account access or orders.",
        }

    def scan_futures_universe(self, *, max_symbols: int = 50, min_quote_volume_usdt: float = 1_000_000.0) -> dict[str, Any]:
        """Rank liquid, active USDT-margined perpetual contracts using public data only."""
        if isinstance(max_symbols, bool) or not isinstance(max_symbols, int) or not 1 <= max_symbols <= 100:
            raise ValueError("max_symbols_must_be_between_1_and_100")
        if isinstance(min_quote_volume_usdt, bool):
            raise ValueError("invalid_min_quote_volume_usdt")
        min_volume = float(min_quote_volume_usdt)
        if not math.isfinite(min_volume) or min_volume < 0:
            raise ValueError("invalid_min_quote_volume_usdt")
        adapter = BinanceFuturesPublicData()
        exchange_info = adapter.get_exchange_info()
        tickers = adapter.get_24h_tickers()
        ticker_by_symbol = {
            str(item.get("symbol", "")).upper(): item
            for item in tickers if isinstance(item, dict) and item.get("symbol")
        }
        candidates: list[dict[str, Any]] = []
        for contract in exchange_info.get("symbols", []):
            if not isinstance(contract, dict):
                continue
            symbol = str(contract.get("symbol", "")).upper()
            if (contract.get("status") != "TRADING"
                    or contract.get("contractType") != "PERPETUAL"
                    or contract.get("quoteAsset") != "USDT"
                    or contract.get("marginAsset") != "USDT"
                    or contract.get("symbol") != symbol or not symbol.endswith("USDT")):
                continue
            ticker = ticker_by_symbol.get(symbol)
            if not ticker:
                continue
            try:
                quote_volume = float(ticker.get("quoteVolume", 0) or 0)
                last_price = float(ticker.get("lastPrice", 0) or 0)
                price_change = float(ticker.get("priceChangePercent", 0) or 0)
                base_volume = float(ticker.get("volume", 0) or 0)
                trade_count = int(ticker.get("count", 0) or 0)
            except (TypeError, ValueError, OverflowError):
                continue
            if (not all(math.isfinite(value) for value in (quote_volume, last_price, price_change, base_volume))
                    or quote_volume < min_volume or last_price <= 0 or base_volume < 0 or trade_count < 0):
                continue
            candidates.append({
                "symbol": f"{symbol[:-4]}/USDT",
                "exchange_symbol": symbol,
                "base_asset": contract.get("baseAsset"),
                "quote_asset": "USDT",
                "contract_type": "PERPETUAL",
                "last_price": last_price,
                "price_change_percent_24h": price_change,
                "base_volume_24h": base_volume,
                "quote_volume_usdt_24h": quote_volume,
                "trades_24h": trade_count,
                "rank_metric": "quote_volume_usdt_24h",
            })
        candidates.sort(key=lambda row: row["quote_volume_usdt_24h"], reverse=True)
        return {
            "ok": True,
            "source": "binance_futures_public_read_only",
            "market": "futures",
            "contract_type": "PERPETUAL",
            "quote_asset": "USDT",
            "min_quote_volume_usdt": min_volume,
            "returned": min(len(candidates), max_symbols),
            "available_candidates": len(candidates),
            "items": candidates[:max_symbols],
            "note": "Liquidity-ranked active USDT perpetual discovery only; public data, no account access or orders.",
        }

    def get_public_last_prices(self, symbols: set[str], market_type: str = "spot") -> dict[str, float]:
        """Fetch recent public ticker marks for already-open portfolio symbols."""
        if not symbols:
            return {}
        if market_type == "futures":
            # Futures PnL and protection must use the exchange mark price,
            # not the last traded price from the 24h ticker.
            return BinanceFuturesPublicData().get_mark_prices(symbols)
        if market_type != "spot":
            raise ValueError("unsupported_public_market_type")
        wanted = {symbol.replace("/", "").upper(): symbol for symbol in symbols}
        tickers = BinancePublicData().get_24h_tickers()
        now_ms = int(time.time() * 1000)
        prices: dict[str, float] = {}
        for ticker in tickers:
            if not isinstance(ticker, dict):
                continue
            exchange_symbol = str(ticker.get("symbol", "")).upper()
            original_symbol = wanted.get(exchange_symbol)
            if not original_symbol:
                continue
            try:
                price = float(ticker.get("lastPrice", 0) or 0)
                close_time = int(ticker.get("closeTime", 0) or 0)
            except (TypeError, ValueError):
                continue
            age_ms = now_ms - close_time
            if math.isfinite(price) and price > 0 and close_time > 0 and -60_000 <= age_ms <= 120_000:
                prices[original_symbol] = price
        return prices

    def get_public_funding_rates(self, symbol: str, *, start_time: int, end_time: int) -> list[dict[str, Any]]:
        """Fetch complete, paginated Binance USD-M funding history for a bounded range."""
        if (isinstance(start_time, bool) or isinstance(end_time, bool)
                or not isinstance(start_time, int) or not isinstance(end_time, int)
                or start_time <= 0 or end_time < start_time):
            raise ValueError("invalid_funding_history_time_range")
        adapter = BinanceFuturesPublicData()
        output: list[dict[str, Any]] = []
        cursor = start_time
        for _ in range(10):
            page = adapter.get_funding_rate_history(
                symbol, start_time=cursor, end_time=end_time, limit=1000,
            )
            if not page:
                break
            output.extend(page)
            last_stamp = page[-1]["timestamp"]
            if last_stamp < cursor:
                raise RuntimeError("funding_history_pagination_did_not_advance")
            if len(page) < 1000 or last_stamp >= end_time:
                break
            cursor = last_stamp + 1
        else:
            raise RuntimeError("funding_history_page_limit_exceeded")
        if any(output[i]["timestamp"] <= output[i - 1]["timestamp"] for i in range(1, len(output))):
            raise RuntimeError("funding_history_contains_duplicate_or_unsorted_timestamps")
        return output

    def get_public_order_book(self, symbol: str, limit: int = 100, market_type: str = "spot") -> dict[str, Any]:
        """Return validated public Spot or USD-M Futures depth metrics; no account access."""
        if market_type == "futures":
            snapshot = BinanceFuturesPublicData().get_order_book(symbol, limit=limit)
            source = "binance_futures_public_read_only"
        elif market_type == "spot":
            snapshot = BinancePublicData().get_order_book(symbol, limit=limit)
            source = "binance_public_read_only"
        else:
            raise ValueError("unsupported_public_market_type")
        def levels(name: str) -> list[tuple[float, float]]:
            output = []
            for row in snapshot.get(name, []):
                if (not isinstance(row, (list, tuple)) or len(row) < 2
                        or isinstance(row[0], bool) or isinstance(row[1], bool)):
                    continue
                try:
                    price, quantity = float(row[0]), float(row[1])
                except (TypeError, ValueError, OverflowError):
                    continue
                if math.isfinite(price) and math.isfinite(quantity) and price > 0 and quantity > 0:
                    output.append((price, quantity))
            return output

        bids, asks = levels("bids"), levels("asks")
        if not bids or not asks:
            raise RuntimeError("public_order_book_missing_valid_bid_or_ask")
        bids.sort(key=lambda item: item[0], reverse=True)
        asks.sort(key=lambda item: item[0])
        best_bid, best_ask = bids[0][0], asks[0][0]
        if best_bid >= best_ask:
            raise RuntimeError("public_order_book_crossed_or_invalid")
        mid = (best_bid + best_ask) / 2.0
        bid_notional = sum(price * qty for price, qty in bids)
        ask_notional = sum(price * qty for price, qty in asks)
        total_notional = bid_notional + ask_notional
        bid_base = sum(qty for _, qty in bids)
        ask_base = sum(qty for _, qty in asks)
        top5_bid = sum(price * qty for price, qty in bids[:5])
        top5_ask = sum(price * qty for price, qty in asks[:5])
        spread_percent = ((best_ask - best_bid) / mid) * 100.0
        if not all(math.isfinite(value) for value in (
            mid, bid_notional, ask_notional, total_notional, bid_base,
            ask_base, top5_bid, top5_ask, spread_percent,
        )):
            raise RuntimeError("public_order_book_depth_overflow")
        return {
            "symbol": symbol,
            "source": source,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "last_update_id": snapshot.get("lastUpdateId"),
            "best_bid": best_bid,
            "best_ask": best_ask,
            "mid_price": mid,
            "spread_absolute": best_ask - best_bid,
            "spread_percent": spread_percent,
            "bid_levels": len(bids),
            "ask_levels": len(asks),
            "bid_depth_base": bid_base,
            "ask_depth_base": ask_base,
            "bid_depth_quote": bid_notional,
            "ask_depth_quote": ask_notional,
            "depth_imbalance": ((bid_notional - ask_notional) / total_notional) if total_notional > 0 else 0.0,
            "top5_bid_depth_quote": top5_bid,
            "top5_ask_depth_quote": top5_ask,
            "bids": [[price, quantity] for price, quantity in bids],
            "asks": [[price, quantity] for price, quantity in asks],
            "execution": "not_performed",
            "note": "Single public REST depth snapshot; not a synchronized order-book stream or a trade signal.",
        }

    def get_public_order_flow(self, symbol: str, limit: int = 500, market_type: str = "spot") -> dict[str, Any]:
        """Summarize recent public aggregate trades by taker side."""
        if market_type == "futures":
            trades = BinanceFuturesPublicData().get_aggregate_trades(symbol, limit=limit)
            source = "binance_futures_public_read_only"
        elif market_type == "spot":
            trades = BinancePublicData().get_aggregate_trades(symbol, limit=limit)
            source = "binance_public_read_only"
        else:
            raise ValueError("unsupported_public_market_type")
        buy_base = sell_base = buy_quote = sell_quote = 0.0
        buy_count = sell_count = 0
        timestamps = []
        valid_count = 0
        now_ms = int(time.time() * 1000)
        for trade in trades:
            if not isinstance(trade, dict) or isinstance(trade.get("T"), bool):
                continue
            try:
                price = float(trade.get("p"))
                quantity = float(trade.get("q"))
                raw_timestamp = float(trade.get("T"))
                buyer_is_maker = trade.get("m")
            except (TypeError, ValueError, OverflowError):
                continue
            if (not all(math.isfinite(value) for value in (price, quantity, raw_timestamp))
                    or not raw_timestamp.is_integer()):
                continue
            timestamp = int(raw_timestamp)
            age_ms = now_ms - timestamp
            if (price <= 0 or quantity <= 0 or timestamp <= 0
                    or age_ms < -60_000 or age_ms > 120_000
                    or not isinstance(buyer_is_maker, bool)):
                continue
            quote = price * quantity
            if not math.isfinite(quote):
                continue
            if buyer_is_maker:
                sell_base += quantity
                sell_quote += quote
                sell_count += 1
            else:
                buy_base += quantity
                buy_quote += quote
                buy_count += 1
            timestamps.append(timestamp)
            valid_count += 1
        total_quote = buy_quote + sell_quote
        if not all(math.isfinite(value) for value in (
            buy_base, sell_base, buy_quote, sell_quote, total_quote,
        )):
            raise RuntimeError("public_aggregate_trade_quote_overflow")
        if valid_count == 0 or total_quote <= 0:
            raise RuntimeError("public_aggregate_trades_stale_or_contain_no_valid_rows")
        return {
            "symbol": symbol,
            "source": source,
            "trade_count": valid_count,
            "buy_taker_count": buy_count,
            "sell_taker_count": sell_count,
            "buy_taker_base_volume": buy_base,
            "sell_taker_base_volume": sell_base,
            "buy_taker_quote_volume": buy_quote,
            "sell_taker_quote_volume": sell_quote,
            "taker_flow_delta_quote": buy_quote - sell_quote,
            "taker_flow_imbalance": (buy_quote - sell_quote) / total_quote,
            "taker_buy_quote_ratio": buy_quote / total_quote,
            "first_trade_time": datetime.fromtimestamp(min(timestamps) / 1000.0, timezone.utc).isoformat(),
            "last_trade_time": datetime.fromtimestamp(max(timestamps) / 1000.0, timezone.utc).isoformat(),
            "execution": "not_performed",
            "note": "Recent aggregate-trade snapshot; buyer-is-maker=false denotes buyer taker, true denotes seller taker. Not a full market-wide order-flow feed.",
        }

    def get_public_candles(self, symbol: str, timeframe: Timeframe, limit: int = 250, market_type: str = "spot") -> CandleBatch:
        """Fetch read-only public Spot or USD-M Futures klines; never submits orders."""
        if market_type == "futures":
            adapter = BinanceFuturesPublicData()
            rows = adapter.klines_to_candles(adapter.get_klines(symbol, timeframe.value, limit=limit))
            note = "BINANCE_USDM_FUTURES_PUBLIC read-only candles. No account access and no order execution."
        elif market_type == "spot":
            adapter = BinancePublicData()
            rows = adapter.klines_to_candles(adapter.get_klines(symbol, timeframe.value, limit=limit))
            note = "BINANCE_SPOT_PUBLIC read-only candles. No account access and no order execution."
        else:
            raise ValueError("unsupported_public_market_type")
        now_ms = int(time.time() * 1000)
        if rows and rows[-1].get("close_time") is not None and int(rows[-1]["close_time"]) > now_ms:
            rows = rows[:-1]
        if len(rows) < 30:
            raise RuntimeError(f"insufficient_binance_public_candles:{timeframe.value}:{len(rows)}")
        return CandleBatch(
            symbol=symbol, timeframe=timeframe, df=Candles(rows),
            source=DataSourceKind.LIVE_PUBLIC, note=note, market_type=market_type,
        )

    def get_public_multi_timeframe(
        self,
        symbol: str,
        limit: int = 250,
        market_type: str = "spot",
        timeframes: Sequence[Timeframe] | None = None,
    ) -> dict[Timeframe, CandleBatch]:
        explicit_selection = timeframes is not None
        selected = timeframes if explicit_selection else tuple(Timeframe)
        normalized = []
        for value in selected:
            tf = value if isinstance(value, Timeframe) else Timeframe(str(value))
            if tf not in normalized:
                normalized.append(tf)
        if not normalized:
            raise ValueError("at_least_one_timeframe_required")

        # An explicit caller selection is strict. With the default all-frame
        # request, only the approved five core intervals are mandatory; other
        # intervals enrich the decision when available but cannot break a scan.
        core_order = (
            Timeframe.D1, Timeframe.H4, Timeframe.H1, Timeframe.M15, Timeframe.M5,
        )
        required = set(normalized) if explicit_selection else set(core_order).intersection(normalized)
        ordered = [tf for tf in core_order if tf in required]
        ordered.extend(tf for tf in normalized if tf not in ordered)
        batches: dict[Timeframe, CandleBatch] = {}
        for tf in ordered:
            try:
                batches[tf] = self.get_public_candles(
                    symbol, tf, limit=limit, market_type=market_type,
                )
            except Exception:
                if tf in required:
                    raise
                # Optional timeframe failure is intentionally non-fatal.
                continue
        return batches

    def get_public_mark_price(self, symbol: str) -> dict[str, Any]:
        return BinanceFuturesPublicData().get_mark_price(symbol)
