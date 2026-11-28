from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class KnowledgeConcept:
    key: str
    category: str
    title: str
    summary: str
    details: list[str]
    tags: list[str]
    caution: str = ""
    confidence_note: str = "Educational/system knowledge entry. Validate in current market context and with tested data."


# Curated foundational knowledge (extensible). Not exhaustive; sources/verification workflow managed via KnowledgeSource records.
SEED_CONCEPTS: list[KnowledgeConcept] = [
    KnowledgeConcept("market_structure_bos", "market_structure", "Break of Structure (BOS)",
        "BOS indicates potential continuation when price breaks a relevant structural high/low in trend direction.",
        ["Confirm with candle close/context, not just wick.", "Check higher-timeframe bias (1D/4H).", "Evaluate volume, momentum, and retest behavior.", "Distinguish from liquidity sweep/fakeout."],
        ["BOS", "HH", "HL", "LH", "LL", "trend"], "A raw level break can be a trap; use multi-factor confirmation."),
    KnowledgeConcept("market_structure_choch", "market_structure", "Change of Character (CHOCH/MSS)",
        "CHOCH/MSS suggests possible trend shift when structure breaks against the prevailing sequence.",
        ["Look for break of key swing after liquidity grab or momentum loss.", "Validate with displacement, retest, and timeframe alignment.", "Do not assume reversal from one signal."],
        ["CHOCH", "MSS", "reversal"], "Early reversal signals have lower reliability in strong trends."),
    KnowledgeConcept("liquidity_sweep", "liquidity", "Liquidity Sweep / Stop Hunt",
        "Price may probe areas where stops/liquidity rest (equal highs/lows, obvious swings) before moving.",
        ["Watch reaction after sweep: rejection, close back inside range, structure shift.", "Combine with volume/spread and session timing.", "Equal highs/lows can act as liquidity pools."],
        ["liquidity", "sweep", "equal highs", "equal lows"], "Liquidity concepts are often inferred; distinguish observable data from interpretation."),
    KnowledgeConcept("breakout_quality", "price_action", "True vs False Breakout",
        "Breakout quality depends on close, volume, momentum, retest, context, and liquidity behavior.",
        ["True breakout: acceptance beyond level, supportive volume/momentum, successful retest possible.", "False breakout/fakeout: wick beyond level then rejection back into range.", "Bull/Bear traps occur when breakout participants are forced to exit."],
        ["breakout", "fakeout", "retest", "trap"], "No breakout rule is universal; always manage risk."),
    KnowledgeConcept("candlestick_context", "candlesticks", "Candlestick Analysis in Context",
        "Candlestick patterns must be read with body/wicks, close location, volume, and market structure.",
        ["Hammer/shooting star need context near key levels.", "Engulfing patterns are stronger with displacement and volume.", "Doji indicates indecision, not automatic reversal.", "Inside bar often signals compression; breakout direction needs confirmation."],
        ["candles", "pin bar", "engulfing", "doji"], "Pattern names alone are insufficient for trade decisions."),
    KnowledgeConcept("risk_management_core", "risk", "Core Risk Management",
        "Risk management defines survival: position sizing, stop placement, leverage limits, and total exposure.",
        ["Risk per trade should be bounded and configurable.", "Use stop-loss based on invalidation, not arbitrary distance.", "Calculate net outcome after fees/slippage/funding.", "Futures leverage increases liquidation risk; caps must be enforced."],
        ["risk", "position sizing", "stop loss", "R:R", "leverage"], "High confidence does not justify excessive risk."),
    KnowledgeConcept("multi_timeframe", "analysis", "Multi-Timeframe Analysis (1D/4H/1H/15M/5M)",
        "Use higher timeframes for bias/context and lower timeframes for setup/timing.",
        ["1D: primary trend/regime. 4H: confirmation. 1H: structure/zones. 15M: setup. 5M: execution timing/scalping.", "Handle conflicts by reducing risk or waiting.", "Prefer closed candles for confirmation when avoiding noise."],
        ["MTF", "1D", "4H", "1H", "15M", "5M"], "Timeframe alignment improves clarity but does not guarantee outcomes."),
    KnowledgeConcept("volume_orderflow", "volume", "Volume and Order Flow (Data-Dependent)",
        "Volume and order flow can confirm participation, absorption, exhaustion, and breakout quality when reliable data exists.",
        ["Relative volume and spikes add context.", "Order book/CVD/delta require reliable exchange data feeds.", "Label inferred liquidity/order-flow assumptions clearly."],
        ["volume", "order flow", "CVD", "order book"], "Many microstructure signals require data not available in synthetic/demo mode."),
    KnowledgeConcept("fees_execution", "execution", "Fees, Slippage, and Execution Quality",
        "Real profitability depends on execution quality and total costs.",
        ["Account for maker/taker fees, spread, slippage, and futures funding.", "Large orders can move price in thin liquidity.", "Measure expected vs actual fill price when live data is available."],
        ["fees", "slippage", "maker", "taker", "funding"], "Use verified exchange fee schedules for live trading."),
    KnowledgeConcept("strategy_selection", "strategies", "Strategy Selection by Market Regime",
        "No single strategy fits all conditions; match strategy to trend/range/volatility/liquidity/costs.",
        ["Trend following performs differently than mean reversion/range strategies.", "Test strategies across regimes and out-of-sample periods.", "Disable or review strategies that degrade."],
        ["strategy", "regime", "trend", "range"], "Historical performance is not proof of future performance."),
    KnowledgeConcept("demo_live_safety", "operations", "Demo/Live Separation and Safety Controls",
        "Demo and Live environments must remain strictly separated. Live order execution is hard-disabled in this release.",
        ["Never auto-switch from Demo to Live.", "Live credentials may be used for read-only monitoring only; no approval or dashboard control can enable Live orders in this release.", "Any future Live re-enablement requires a separate release, explicit user decision, and independent safety review.", "No withdrawal/transfer permissions for API keys.", "Emergency stop should prevent new risk-taking actions."],
        ["demo", "live", "safety", "permissions"], "Operational safety overrides automation convenience."),
]


def list_concepts(category: str | None = None, query: str | None = None) -> list[dict[str, Any]]:
    items = SEED_CONCEPTS
    if category:
        c = category.strip().lower()
        items = [x for x in items if x.category.lower() == c]
    if query:
        q = query.strip().lower()
        items = [x for x in items if q in x.title.lower() or q in x.summary.lower() or q in " ".join(x.tags).lower() or q in " ".join(x.details).lower()]
    return [asdict(x) for x in items]


def categories() -> list[str]:
    return sorted({x.category for x in SEED_CONCEPTS})
