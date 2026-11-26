from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

from .enums import EnvironmentMode, MarketType


_PROJECT_ROOT = Path(__file__).resolve().parents[3]


def load_project_environment(project_root: Path | None = None) -> None:
    """Load the project-root .env without overriding deployment environment variables."""
    load_dotenv((project_root or _PROJECT_ROOT) / ".env", override=False)


# Load the example-backed project configuration before Settings reads os.environ.
load_project_environment()


def _bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    app_env: str = field(default_factory=lambda: os.getenv("TITAN_APP_ENV", "development"))
    app_name: str = "TITAN SUPER AI PREMIUM"
    secret_key: str = field(default_factory=lambda: os.getenv("TITAN_SECRET_KEY", ""))
    database_url: str = field(default_factory=lambda: os.getenv("TITAN_DATABASE_URL", "sqlite:///" + (_PROJECT_ROOT / "backend" / "data" / "titan.db").as_posix()))

    # Live credentials may connect for read-only monitoring. Order execution
    # remains gated by the user's explicit per-start confirmation in the terminal.
    default_environment: EnvironmentMode = EnvironmentMode.DEMO
    default_market: MarketType = MarketType.SPOT
    live_trading_enabled: bool = field(default_factory=lambda: _bool("TITAN_LIVE_TRADING_ENABLED", True))
    allow_demo_to_live_auto: bool = False  # MUST remain False
    allow_withdrawal_permissions: bool = False  # MUST remain False

    # Risk safety caps (user-configurable later; system must not exceed)
    max_leverage_cap: float = 10.0
    max_risk_per_trade_cap: float = 0.02
    max_daily_loss_cap: float = 0.07
    max_spot_trade_notional: float = 100.0
    max_futures_trade_notional: float = 300.0
    max_futures_trade_margin: float = 30.0
    max_portfolio_exposure_multiple: float = 2.0
    max_open_positions_cap: int = 10

    # Auth (private access). No passwords/secrets stored in code.
    auth_required: bool = field(default_factory=lambda: _bool("TITAN_AUTH_REQUIRED", True))
    allowed_emails: str = field(default_factory=lambda: os.getenv("TITAN_ALLOWED_EMAILS", ""))
    session_ttl_hours: int = 24 * 7

    # External services (empty by default)
    binance_api_key: str = field(default_factory=lambda: os.getenv("BINANCE_API_KEY", ""))
    binance_api_secret: str = field(default_factory=lambda: os.getenv("BINANCE_API_SECRET", ""))
    binance_env: str = field(default_factory=lambda: os.getenv("BINANCE_ENV", "demo"))
    telegram_bot_token: str = field(default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", ""))
    telegram_allowed_chat_ids: str = field(default_factory=lambda: os.getenv("TELEGRAM_ALLOWED_CHAT_IDS", ""))
    ai_provider: str = field(default_factory=lambda: os.getenv("AI_PROVIDER", ""))
    ai_api_key: str = field(default_factory=lambda: os.getenv("AI_API_KEY", ""))
    allowed_origins: str = field(default_factory=lambda: os.getenv("TITAN_ALLOWED_ORIGINS", "*"))


settings = Settings()
