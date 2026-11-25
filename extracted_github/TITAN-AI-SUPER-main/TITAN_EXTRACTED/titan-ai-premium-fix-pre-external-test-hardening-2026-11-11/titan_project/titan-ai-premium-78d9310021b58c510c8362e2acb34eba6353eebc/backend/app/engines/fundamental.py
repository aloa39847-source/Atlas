"""Fundamental/tokenomics metrics from public market-data snapshots."""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

from app.core.enums import EngineStatus
from app.engines.base import Engine
from app.data.coingecko_public import CoinGeckoPublicData
from app.data.defillama_public import DefiLlamaPublicData


class FundamentalAnalysisEngine(Engine):
    key = "fundamental_analysis"
    name = "Fundamental and Tokenomics Analysis Engine"

    def __init__(self) -> None:
        self.provider = CoinGeckoPublicData()
        self.protocol_provider = DefiLlamaPublicData()

    def status(self):
        return EngineStatus.IMPLEMENTED_PARTIAL

    @staticmethod
    def _number(data: dict[str, Any], key: str) -> float | None:
        value = data.get(key)
        if value is None or isinstance(value, bool):
            return None
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
        return number if math.isfinite(number) else None

    @staticmethod
    def _safe_ratio(numerator: float | None, denominator: float | None) -> float | None:
        if numerator is None or denominator is None or denominator == 0:
            return None
        try:
            result = numerator / denominator
        except (ArithmeticError, OverflowError):
            return None
        return result if math.isfinite(result) else None

    @classmethod
    def _safe_percent_difference(cls, reference: float | None, current: float | None) -> float | None:
        if reference is None or current is None or reference <= 0:
            return None
        try:
            ratio = cls._safe_ratio(reference - current, reference)
            result = ratio * 100.0 if ratio is not None else None
        except (ArithmeticError, OverflowError):
            return None
        return result if result is not None and math.isfinite(result) else None

    def analyze_snapshot(self, data: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(data, dict) or not data.get("id"):
            return {"ok": False, "error": "coin_market_snapshot_required"}
        current = self._number(data, "current_price")
        market_cap = self._number(data, "market_cap")
        volume = self._number(data, "total_volume")
        fdv = self._number(data, "fully_diluted_valuation")
        circulating = self._number(data, "circulating_supply")
        total_supply = self._number(data, "total_supply")
        max_supply = self._number(data, "max_supply")
        ath = self._number(data, "ath")
        if current is None or current <= 0:
            return {"ok": False, "error": "valid_current_market_price_required"}
        if any(value is not None and value < 0 for value in (market_cap, volume, fdv, circulating, total_supply, max_supply)):
            return {"ok": False, "error": "negative_market_or_supply_metrics_rejected"}
        supply_warnings = []
        market_data_warnings = []
        if circulating is not None and total_supply is not None and circulating > total_supply:
            supply_warnings.append("circulating_supply_exceeds_total_supply")
        if circulating is not None and max_supply is not None and circulating > max_supply:
            supply_warnings.append("circulating_supply_exceeds_max_supply")
        if total_supply is not None and max_supply is not None and total_supply > max_supply:
            supply_warnings.append("total_supply_exceeds_max_supply")
        supply_ratio = self._safe_ratio(circulating, max_supply)
        fdv_to_market_cap = self._safe_ratio(fdv, market_cap)
        market_cap_to_volume = self._safe_ratio(market_cap, volume)
        ath_drawdown = self._safe_percent_difference(ath, current)
        if ath is not None and ath > 0 and current > ath:
            market_data_warnings.append("current_price_exceeds_reported_all_time_high")
        changes = {
            "24h": self._number(data, "price_change_percentage_24h_in_currency"),
            "7d": self._number(data, "price_change_percentage_7d_in_currency"),
            "30d": self._number(data, "price_change_percentage_30d_in_currency"),
        }
        return {
            "ok": True,
            "coin_id": data.get("id"),
            "symbol": str(data.get("symbol") or "").upper(),
            "name": data.get("name"),
            "currency": "usd",
            "current_price": current,
            "market_cap": market_cap,
            "market_cap_rank": data.get("market_cap_rank"),
            "volume_24h": volume,
            "fully_diluted_valuation": fdv,
            "circulating_supply": circulating,
            "total_supply": total_supply,
            "max_supply": max_supply,
            "circulating_to_max_supply_ratio": round(supply_ratio, 6) if supply_ratio is not None else None,
            "fdv_to_market_cap_ratio": round(fdv_to_market_cap, 6) if fdv_to_market_cap is not None else None,
            "market_cap_to_volume_24h_ratio": round(market_cap_to_volume, 6) if market_cap_to_volume is not None else None,
            "all_time_high": ath,
            "drawdown_from_ath_percent": round(ath_drawdown, 4) if ath_drawdown is not None else None,
            "price_change_percent": changes,
            "supply_consistency_warnings": supply_warnings,
            "market_data_warnings": market_data_warnings,
            "last_updated": data.get("last_updated"),
            "source": "coingecko_public_market_data",
            "signal": "not_generated",
            "execution": "none",
            "limitations": [
                "market/tokenomics snapshot only; not a full project due-diligence review",
                "provider coverage and supply fields may be missing or estimated",
                "does not verify team, code audits, unlock schedules, treasury, governance, or legal risks",
                "no buy/sell recommendation is generated",
            ],
        }

    def analyze_protocol_snapshot(
        self, protocol: dict[str, Any], fees: dict[str, Any],
    ) -> dict[str, Any]:
        if (not isinstance(protocol, dict) or not isinstance(fees, dict)
                or not protocol.get("slug") or not isinstance(protocol.get("tvl"), list)):
            return {"ok": False, "error": "protocol_tvl_and_fee_snapshots_required"}
        slug = str(protocol.get("slug")).strip().lower()
        tvl_points = []
        for point in protocol["tvl"]:
            if not isinstance(point, dict) or any(isinstance(point.get(key), bool) for key in ("date", "totalLiquidityUSD")):
                continue
            try:
                stamp = float(point["date"])
                value = float(point["totalLiquidityUSD"])
            except (KeyError, TypeError, ValueError, OverflowError):
                continue
            if math.isfinite(stamp) and stamp > 0 and stamp.is_integer() and math.isfinite(value) and value >= 0:
                tvl_points.append((int(stamp), value))
        tvl_points.sort(key=lambda item: item[0])
        if not tvl_points or any(tvl_points[i][0] <= tvl_points[i - 1][0] for i in range(1, len(tvl_points))):
            return {"ok": False, "error": "valid_unique_protocol_tvl_history_required"}
        latest_stamp, current_tvl = tvl_points[-1]
        if current_tvl <= 0:
            return {"ok": False, "error": "positive_current_protocol_tvl_required"}
        age_seconds = datetime.now(timezone.utc).timestamp() - latest_stamp
        if not math.isfinite(age_seconds) or age_seconds < -300 or age_seconds > 3 * 86400:
            return {"ok": False, "error": "stale_or_future_protocol_tvl_snapshot"}
        chains = protocol.get("chains")
        if not isinstance(chains, list):
            chains = []

        def change_over(days: int) -> float | None:
            target = latest_stamp - days * 86400
            previous = next((value for stamp, value in reversed(tvl_points[:-1]) if stamp <= target and value > 0), None)
            if previous is None:
                return None
            ratio = self._safe_ratio(current_tvl - previous, previous)
            result = ratio * 100.0 if ratio is not None else None
            return round(result, 4) if result is not None and math.isfinite(result) else None

        fee_metrics = {}
        for source_key, output_key in (
            ("total24h", "fees_24h_usd"), ("total7d", "fees_7d_usd"),
            ("total30d", "fees_30d_usd"), ("totalAllTime", "fees_all_time_usd"),
            ("totalRevenue24h", "revenue_24h_usd"), ("totalRevenue7d", "revenue_7d_usd"),
            ("totalRevenue30d", "revenue_30d_usd"), ("totalRevenueAllTime", "revenue_all_time_usd"),
        ):
            value = self._number(fees, source_key)
            if value is not None and value >= 0:
                fee_metrics[output_key] = value
        fees_30d = fee_metrics.get("fees_30d_usd")
        fees_to_tvl = self._safe_ratio(fees_30d, current_tvl)
        if fees_to_tvl is not None:
            fees_to_tvl *= 100.0
            if not math.isfinite(fees_to_tvl):
                fees_to_tvl = None
        return {
            "ok": True,
            "protocol_slug": slug,
            "name": protocol.get("name"),
            "category": protocol.get("category"),
            "chains": sorted(set(str(chain) for chain in chains if isinstance(chain, str))),
            "current_tvl_usd": current_tvl,
            "tvl_change_7d_percent": change_over(7),
            "tvl_change_30d_percent": change_over(30),
            **fee_metrics,
            "fees_30d_to_current_tvl_percent": round(fees_to_tvl, 6) if fees_to_tvl is not None else None,
            "tvl_as_of": latest_stamp,
            "source": "defillama_public_protocol_and_fees",
            "signal": "not_generated",
            "execution": "none",
            "limitations": [
                "protocol-level on-chain/DeFi TVL and fee metrics; not token-holder or wallet-level analysis",
                "provider classifications and protocol attribution may be incomplete or revised",
                "TVL changes may reflect price changes, flows, migrations or methodology changes",
                "no token unlock calendar, governance, audit or legal due diligence is included",
                "no buy/sell recommendation is generated",
            ],
        }

    def fetch_protocol_and_analyze(self, slug: str) -> dict[str, Any]:
        try:
            protocol = self.protocol_provider.get_protocol_overview(slug)
            fees = self.protocol_provider.get_protocol_fees(slug)
            return self.analyze_protocol_snapshot(protocol, fees)
        except Exception as exc:
            return {"ok": False, "error": "protocol_fundamental_data_unavailable", "detail": str(exc)[:200]}

    def fetch_and_analyze(self, coin_id: str) -> dict[str, Any]:
        try:
            return self.analyze_snapshot(self.provider.get_coin_market_data(coin_id))
        except Exception as exc:
            return {"ok": False, "error": "fundamental_data_unavailable", "detail": str(exc)[:200]}
