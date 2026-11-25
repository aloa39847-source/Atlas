"""Public CoinGecko market/tokenomics metadata adapter; no authenticated trading access."""
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from typing import Any


class CoinGeckoPublicData:
    BASE = "https://api.coingecko.com/api/v3"

    def _get_json(self, path: str) -> Any:
        request = urllib.request.Request(
            f"{self.BASE}{path}",
            headers={"User-Agent": "TITAN-AI-PREMIUM/1.0", "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                raw = response.read(2_000_001)
            if len(raw) > 2_000_000:
                raise RuntimeError("coingecko_response_too_large")
            return json.loads(raw.decode("utf-8"))
        except Exception as exc:
            raise RuntimeError(f"coingecko_public_data_unavailable:{type(exc).__name__}") from exc

    def get_coin_market_data(self, coin_id: str, vs_currency: str = "usd") -> dict[str, Any]:
        coin_id = str(coin_id or "").strip().lower()
        vs_currency = str(vs_currency or "usd").strip().lower()
        if not re.fullmatch(r"[a-z0-9-]{1,100}", coin_id) or not re.fullmatch(r"[a-z]{3,8}", vs_currency):
            raise ValueError("invalid_coin_id_or_vs_currency")
        query = urllib.parse.urlencode({
            "vs_currency": vs_currency,
            "ids": coin_id,
            "price_change_percentage": "24h,7d,30d",
        })
        data = self._get_json("/coins/markets?" + query)
        if (not isinstance(data, list) or not data or not isinstance(data[0], dict)
                or data[0].get("id") != coin_id):
            raise RuntimeError("coingecko_coin_not_found_or_mismatched")
        return data[0]
