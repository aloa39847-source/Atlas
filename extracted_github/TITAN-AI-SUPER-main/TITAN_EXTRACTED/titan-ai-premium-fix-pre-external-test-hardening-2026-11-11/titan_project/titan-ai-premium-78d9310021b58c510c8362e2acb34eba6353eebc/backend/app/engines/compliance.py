from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.core.enums import MarketType

# Official Binance sources to review/verify before enabling live operations (terms change):
# - Terms of Use: https://www.binance.com/en/terms
# - API Terms: https://www.binance.com/en/terms-api
# - API docs/limits (developer portal): https://developers.binance.com/
# This engine provides safety/compliance checks we can enforce in software. It does NOT guarantee
# legal/regulatory compliance, account eligibility, KYC status, or geographic permissibility.
# When uncertain, it blocks the action and requires human review / official verification.


@dataclass
class ComplianceFinding:
    ok: bool
    action: str  # allow, block, review_required
    reason: str
    policy_area: str
    sources: list[str] = field(default_factory=list)
    alternative: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    checked_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "action": self.action,
            "reason": self.reason,
            "policy_area": self.policy_area,
            "sources": self.sources,
            "alternative": self.alternative,
            "details": self.details,
            "checked_at": self.checked_at,
            "disclaimer": "Software compliance check only; not legal advice and not a guarantee of Binance/regulatory compliance.",
        }


class TitanCompliancePolicyEngine:
    key = "compliance_policy"
    name = "TITAN COMPLIANCE & POLICY ENGINE"

    FORBIDDEN_API_PERMISSIONS = {"enableWithdrawals", "withdraw", "withdrawals", "transfer", "universalTransfer", "subAccountTransfer"}

    def _sources(self) -> list[str]:
        return [
            "https://www.binance.com/en/terms",
            "https://www.binance.com/en/terms-api",
            "https://developers.binance.com/",
        ]

    def check_api_permissions(self, permissions: list[str] | None) -> ComplianceFinding:
        perms = {str(p).strip() for p in (permissions or []) if str(p).strip()}
        bad = sorted(perms.intersection(self.FORBIDDEN_API_PERMISSIONS))
        if bad:
            return ComplianceFinding(False, "block", "withdrawal_or_transfer_api_permissions_forbidden", "api_permissions", self._sources(),
                                     "Use read/trade permissions only as needed; never enable withdrawals/transfers.", {"forbidden": bad})
        return ComplianceFinding(True, "allow", "api_permissions_basic_check_passed", "api_permissions", self._sources(), details={"note": "Verify exact Binance permission names and account settings on official platform."})

    def check_market_conduct(self, flags: dict[str, Any] | None) -> ComplianceFinding:
        """Reject explicit order intent that indicates prohibited market manipulation."""
        if flags is None:
            return ComplianceFinding(True, "allow", "no_prohibited_conduct_intent_declared",
                                     "market_conduct", self._sources())
        if not isinstance(flags, dict):
            return ComplianceFinding(False, "block", "invalid_market_conduct_metadata",
                                     "market_conduct", self._sources())
        prohibited = {
            "wash_trading": "wash_trading_intent",
            "self_trade": "self_trade_intent",
            "spoofing": "spoofing_intent",
            "layering": "layering_intent",
            "quote_stuffing": "quote_stuffing_intent",
            "front_running": "front_running_intent",
            "pump_and_dump": "pump_and_dump_intent",
        }
        active = sorted(name for name, key in prohibited.items() if flags.get(key) is True)
        if active:
            return ComplianceFinding(False, "block", "prohibited_market_conduct_intent",
                                     "market_conduct", self._sources(),
                                     "Only genuine orders intended for legitimate execution are permitted.",
                                     {"blocked_behaviors": active})
        return ComplianceFinding(True, "allow", "market_conduct_intent_check_passed",
                                 "market_conduct", self._sources())

    def check_request_integrity(self, *, user_requested: bool, description: str = "") -> ComplianceFinding:
        # Placeholder for future NLP/rule checks on user requests; always prefer safety/compliance.
        return ComplianceFinding(True, "allow", "request_recorded_for_review", "request_integrity", self._sources(),
                                 details={"user_requested": user_requested, "description": description[:500]})

    def check_order_action(self, *, market: MarketType, symbol: str, side: str, quantity: float, price: float | None,
                           environment_mode: str, live_enabled: bool, api_permissions: list[str] | None = None,
                           bypass_attempt: bool = False, geo_verified: bool | None = None,
                           kyc_verified: bool | None = None, rate_limit_ok: bool | None = None,
                           market_conduct_flags: dict[str, Any] | None = None) -> ComplianceFinding:
        if bypass_attempt:
            return ComplianceFinding(False, "block", "circumvention_attempt_blocked", "platform_integrity", self._sources(),
                                     "Use only official, supported Binance interfaces and comply with limits/rules.")
        perm = self.check_api_permissions(api_permissions)
        if not perm.ok:
            return perm
        conduct = self.check_market_conduct(market_conduct_flags)
        if not conduct.ok:
            return conduct
        if environment_mode == "live" and not live_enabled:
            return ComplianceFinding(False, "block", "live_not_explicitly_enabled", "execution_control", self._sources(),
                                     "Use Demo/Testnet or enable Live explicitly after review and approval.")
        if quantity <= 0 or (price is not None and price <= 0):
            return ComplianceFinding(False, "block", "invalid_order_parameters", "order_integrity", self._sources(),
                                     "Correct order quantity/price and recheck symbol filters.")
        # Market manipulation/spoofing/wash-trading behaviors are prohibited; system must not generate them.
        if side.lower() in {"self_trade", "wash", "spoof"}:
            return ComplianceFinding(False, "block", "prohibited_trading_behavior", "market_conduct", self._sources())
        # Uncertainty checks: cannot verify account geo/KYC from local config alone.
        if geo_verified is False:
            return ComplianceFinding(False, "block", "geographic_or_eligibility_restriction", "eligibility", self._sources(),
                                     "Verify account/service eligibility for your location on official Binance channels.")
        if kyc_verified is False:
            return ComplianceFinding(False, "block", "kyc_or_account_verification_required", "eligibility", self._sources(),
                                     "Complete required verification on official Binance platform.")
        if rate_limit_ok is False:
            return ComplianceFinding(False, "block", "api_rate_limit_or_service_limit", "api_limits", self._sources(),
                                     "Back off, respect rate limits, and retry only according to official API rules.")
        uncertain = []
        if geo_verified is None:
            uncertain.append("geo_eligibility_unverified")
        if kyc_verified is None:
            uncertain.append("kyc_status_unverified")
        if rate_limit_ok is None:
            uncertain.append("rate_limit_state_unverified")
        details = {"market": market.value, "symbol": symbol, "side": side, "environment_mode": environment_mode, "uncertain": uncertain,
                   "exchange_symbol_filters_checked": False, "official_terms_version_checked": False}
        if environment_mode == "live" and uncertain:
            return ComplianceFinding(False, "review_required", "live_preconditions_unverified", "pre_trade_review", self._sources(),
                                     "Verify eligibility, KYC, API limits, symbol filters, and official terms before any Live action.", details)
        return ComplianceFinding(True, "allow_with_checks", "basic_compliance_precheck_passed", "pre_trade", self._sources(),
                                 "Complete exchange filter/reconciliation/rate-limit checks before sending any real order.", details)


compliance_engine = TitanCompliancePolicyEngine()
