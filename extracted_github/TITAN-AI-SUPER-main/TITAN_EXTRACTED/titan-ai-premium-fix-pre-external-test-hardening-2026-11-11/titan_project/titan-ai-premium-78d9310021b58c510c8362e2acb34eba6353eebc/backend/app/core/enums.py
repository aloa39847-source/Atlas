from enum import Enum


class EnvironmentMode(str, Enum):
    DEMO = "demo"
    LIVE = "live"


class MarketType(str, Enum):
    SPOT = "spot"
    FUTURES = "futures"
    EARN = "earn"


class DecisionAction(str, Enum):
    BUY = "buy"
    SELL = "sell"
    LONG = "long"
    SHORT = "short"
    WAIT = "wait"


class EngineStatus(str, Enum):
    NOT_IMPLEMENTED = "not_implemented"
    IMPLEMENTED_PARTIAL = "implemented_partial"
    READY = "ready"
    NEEDS_EXTERNAL_SERVICE = "needs_external_service"
    ERROR = "error"
    DISABLED = "disabled"


class OrderStatus(str, Enum):
    PENDING = "pending"
    SUBMITTED = "submitted"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    FAILED = "failed"


class DataSourceKind(str, Enum):
    SYNTHETIC_LABELED = "synthetic_labeled"  # Never present as live market data
    HISTORICAL = "historical"
    LIVE_PUBLIC = "live_public"
    LIVE_PRIVATE = "live_private"
    NEWS = "news"
    MANUAL = "manual"
    TEST = "test"


class Timeframe(str, Enum):
    M1 = "1m"
    M3 = "3m"
    M5 = "5m"
    M15 = "15m"
    M30 = "30m"
    H1 = "1h"
    H2 = "2h"
    H4 = "4h"
    H6 = "6h"
    H8 = "8h"
    H12 = "12h"
    D1 = "1d"
    D3 = "3d"
    W1 = "1w"
    MO1 = "1M"
