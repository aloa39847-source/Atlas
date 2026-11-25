from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    actor: Mapped[str] = mapped_column(String(64), default="system")
    action: Mapped[str] = mapped_column(String(128), index=True)
    entity_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    entity_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    environment_mode: Mapped[str | None] = mapped_column(String(16), index=True)  # demo/live
    market_type: Mapped[str | None] = mapped_column(String(16), index=True)  # spot/futures/earn
    severity: Mapped[str] = mapped_column(String(16), default="info")
    message: Mapped[str] = mapped_column(Text, default="")
    details: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class SystemState(Base):
    __tablename__ = "system_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # single row (id=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    environment_mode: Mapped[str] = mapped_column(String(16), default="demo")  # demo/live
    account_target: Mapped[str] = mapped_column(String(16), default="demo")    # demo/testnet/live
    market_type: Mapped[str] = mapped_column(String(16), default="spot")
    bot_run_state: Mapped[str] = mapped_column(String(24), default="stopped")  # stopped,starting,running,analyzing,executing,waiting_data,error
    bot_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    bot_stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    bot_stop_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    autonomous_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    live_trading_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    live_confirmed_by_user: Mapped[bool] = mapped_column(Boolean, default=False)
    emergency_stop: Mapped[bool] = mapped_column(Boolean, default=False)
    selected_timeframes: Mapped[list | None] = mapped_column(JSON, nullable=True)
    risk_limits: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status_summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class DecisionRecord(Base):
    __tablename__ = "decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    environment_mode: Mapped[str] = mapped_column(String(16), index=True)
    market_type: Mapped[str] = mapped_column(String(16), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    action: Mapped[str] = mapped_column(String(16), index=True)  # BUY/SELL/LONG/SHORT/WAIT
    strategy: Mapped[str | None] = mapped_column(String(64), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    entry_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_loss: Mapped[float | None] = mapped_column(Float, nullable=True)
    take_profit: Mapped[float | None] = mapped_column(Float, nullable=True)
    risk_reward: Mapped[float | None] = mapped_column(Float, nullable=True)
    leverage: Mapped[float | None] = mapped_column(Float, nullable=True)
    rationale: Mapped[str] = mapped_column(Text, default="")
    timeframe_context: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    indicators: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    costs: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    news_context: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    data_source: Mapped[str] = mapped_column(String(32), default="unknown")
    reproducibility: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # inputs/config/versions snapshot for replay review


class TradeRecord(Base):
    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    environment_mode: Mapped[str] = mapped_column(String(16), index=True)
    market_type: Mapped[str] = mapped_column(String(16), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    side: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    strategy: Mapped[str | None] = mapped_column(String(64), nullable=True)
    quantity: Mapped[float | None] = mapped_column(Float, nullable=True)
    entry_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_loss: Mapped[float | None] = mapped_column(Float, nullable=True)
    take_profit: Mapped[float | None] = mapped_column(Float, nullable=True)
    leverage: Mapped[float | None] = mapped_column(Float, nullable=True)
    fees_total: Mapped[float | None] = mapped_column(Float, nullable=True)
    pnl_gross: Mapped[float | None] = mapped_column(Float, nullable=True)
    pnl_net: Mapped[float | None] = mapped_column(Float, nullable=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    metadata_json: Mapped[dict | None] = mapped_column("metadata", JSON, nullable=True)


class KnowledgeSource(Base):
    __tablename__ = "knowledge_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    title: Mapped[str] = mapped_column(String(256))
    source_type: Mapped[str] = mapped_column(String(64), default="unknown")
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    reliability_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str] = mapped_column(Text, default="")
    metadata_json: Mapped[dict | None] = mapped_column("metadata", JSON, nullable=True)


class LearningRecord(Base):
    __tablename__ = "learning_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    environment_mode: Mapped[str | None] = mapped_column(String(16), index=True)
    market_type: Mapped[str | None] = mapped_column(String(16), index=True)
    category: Mapped[str] = mapped_column(String(64), index=True)
    subject: Mapped[str] = mapped_column(String(256))
    outcome: Mapped[str | None] = mapped_column(String(32), nullable=True)
    lesson: Mapped[str] = mapped_column(Text, default="")
    evidence: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)


class EngineState(Base):
    __tablename__ = "engine_states"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    engine_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), default="not_implemented")
    message: Mapped[str] = mapped_column(Text, default="")
    details: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class ConfigChange(Base):
    __tablename__ = "config_changes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    actor: Mapped[str] = mapped_column(String(128), default="system")
    scope: Mapped[str] = mapped_column(String(64), index=True)  # risk, auth, trading, system, integration
    key: Mapped[str] = mapped_column(String(128), index=True)
    old_value: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    new_value: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    approved: Mapped[bool] = mapped_column(Boolean, default=False)
    metadata_json: Mapped[dict | None] = mapped_column("metadata", JSON, nullable=True)


class AppUser(Base):
    __tablename__ = "app_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str | None] = mapped_column(String(512), nullable=True)
    password_salt: Mapped[str | None] = mapped_column(String(256), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    two_factor_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    two_factor_secret: Mapped[str | None] = mapped_column(String(256), nullable=True)
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)


class PredictionRecord(Base):
    __tablename__ = "predictions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    environment_mode: Mapped[str] = mapped_column(String(16), index=True)
    market_type: Mapped[str] = mapped_column(String(16), index=True)
    horizon: Mapped[str] = mapped_column(String(32), default="unknown")
    scenario: Mapped[str] = mapped_column(String(64), default="base")
    predicted_direction: Mapped[str | None] = mapped_column(String(16), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    assumptions: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    outcome: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)


class ExperimentRecord(Base):
    __tablename__ = "experiments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    name: Mapped[str] = mapped_column(String(256))
    kind: Mapped[str] = mapped_column(String(64), default="strategy")  # strategy, model, hypothesis
    lifecycle: Mapped[str] = mapped_column(String(32), default="draft")  # draft, shadow, demo, canary, approved, rejected, archived
    strategy_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    hypothesis: Mapped[str] = mapped_column(Text, default="")
    config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    metrics: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    approved_by_user: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str] = mapped_column(Text, default="")


class OrderBookSnapshotRecord(Base):
    """Persisted synchronized public Spot depth snapshots captured by TITAN."""

    __tablename__ = "orderbook_snapshot_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    captured_at_ms: Mapped[int] = mapped_column(Integer, index=True)
    market_type: Mapped[str] = mapped_column(String(16), default="spot", index=True)
    route: Mapped[list] = mapped_column(JSON)
    quotes: Mapped[dict] = mapped_column(JSON)
    source: Mapped[str] = mapped_column(String(64), default="binance_public_read_only")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AIProviderConfig(Base):
    __tablename__ = "ai_provider_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    name: Mapped[str] = mapped_column(String(128))
    provider: Mapped[str] = mapped_column(String(64), index=True)  # openai, anthropic, google, xai, mistral, deepseek, openai_compatible
    base_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    model: Mapped[str] = mapped_column(String(128))
    api_key_ref: Mapped[str | None] = mapped_column(String(256), nullable=True)  # e.g. env:OPENAI_API_KEY; never store raw secrets in logs
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    tasks: Mapped[list | None] = mapped_column(JSON, nullable=True)  # e.g. ["analysis","news","code","risk","review"]
    daily_budget_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    monthly_budget_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column("metadata", JSON, nullable=True)


class AIUsageRecord(Base):
    __tablename__ = "ai_usage_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    provider_config_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    provider: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(128))
    task: Mapped[str | None] = mapped_column(String(64), nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, default=False)
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    estimated_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class OrderRecord(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    environment_mode: Mapped[str] = mapped_column(String(16), index=True)
    market_type: Mapped[str] = mapped_column(String(16), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    side: Mapped[str] = mapped_column(String(16))
    order_type: Mapped[str] = mapped_column(String(24), default="limit")
    quantity: Mapped[float] = mapped_column(Float)
    filled_quantity: Mapped[float] = mapped_column(Float, default=0.0)
    price: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_fill_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)  # pending,submitted,partially_filled,filled,cancelled,rejected,failed
    shadow: Mapped[bool] = mapped_column(Boolean, default=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), unique=True, nullable=True, index=True)
    client_order_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    exchange_order_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    fees_paid: Mapped[float] = mapped_column(Float, default=0.0)
    fee_asset: Mapped[str | None] = mapped_column(String(16), nullable=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    metadata_json: Mapped[dict | None] = mapped_column("metadata", JSON, nullable=True)


class PositionRecord(Base):
    __tablename__ = "positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    environment_mode: Mapped[str] = mapped_column(String(16), index=True)
    market_type: Mapped[str] = mapped_column(String(16), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    side: Mapped[str] = mapped_column(String(16))  # long/short/long_spot
    quantity: Mapped[float] = mapped_column(Float, default=0.0)
    entry_avg_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    mark_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    leverage: Mapped[float] = mapped_column(Float, default=1.0)
    unrealized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    realized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    fees_total: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(24), default="open", index=True)  # open,closed
    shadow: Mapped[bool] = mapped_column(Boolean, default=True)
    metadata_json: Mapped[dict | None] = mapped_column("metadata", JSON, nullable=True)


class PortfolioSnapshot(Base):
    __tablename__ = "portfolio_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    environment_mode: Mapped[str] = mapped_column(String(16), index=True)
    market_type: Mapped[str] = mapped_column(String(16), index=True)
    quote_asset: Mapped[str] = mapped_column(String(16), default="USDT")
    cash_balance: Mapped[float] = mapped_column(Float, default=0.0)
    equity_estimate: Mapped[float] = mapped_column(Float, default=0.0)
    unrealized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    realized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    fees_total: Mapped[float] = mapped_column(Float, default=0.0)
    exposure_notional: Mapped[float] = mapped_column(Float, default=0.0)
    risk_used: Mapped[float] = mapped_column(Float, default=0.0)
    positions_open: Mapped[int] = mapped_column(Integer, default=0)
    details: Mapped[dict | None] = mapped_column(JSON, nullable=True)
