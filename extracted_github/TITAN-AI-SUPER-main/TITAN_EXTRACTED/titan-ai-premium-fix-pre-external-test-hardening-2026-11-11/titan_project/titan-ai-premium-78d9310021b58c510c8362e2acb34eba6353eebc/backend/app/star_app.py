from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, HTMLResponse, FileResponse
from starlette.staticfiles import StaticFiles
from starlette.routing import Route, Mount

from app.core.config import settings
from app.core.enums import MarketType, Timeframe
from app.engines.compliance import compliance_engine
from app.engines.order_gateway import PreTradeOrderGateway
from app.integrations.ai_providers import SUPPORTED_PROVIDERS, invoke_provider, list_configs, record_usage, provider_status
from app.integrations.binance_adapter import BinanceTestnetAdapter, BinanceLiveAdapter, BinanceConfig
from app.services.credentials import (save_testnet_credentials, save_live_credentials, load_testnet_credentials, load_binance_credentials, delete_testnet_credentials, delete_live_credentials, public_status as creds_public_status)
from app.models import db as dbmod
from app.models.entities import AIProviderConfig, AuditLog, ConfigChange, DecisionRecord, KnowledgeSource, LearningRecord, OrderRecord, PositionRecord, OrderBookSnapshotRecord, TradeRecord, PortfolioSnapshot
from app.services import auth_service
from app.services.audit import log_event
from app.services.core_service import core_service
from app.services.shadow_trading import ShadowTradingService
from app.services.self_diagnosis import get_engine
from sqlalchemy import desc, select
from fastapi.encoders import jsonable_encoder


class SecurityHeadersMiddleware:
    """Add conservative browser security headers to every Starlette response."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_security_headers(message):
            if message.get("type") == "http.response.start":
                headers = list(message.get("headers", []))
                present = {key.lower() for key, _ in headers}
                defaults = (
                    (b"x-content-type-options", b"nosniff"),
                    (b"x-frame-options", b"DENY"),
                    (b"referrer-policy", b"strict-origin-when-cross-origin"),
                    (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
                    (b"content-security-policy", b"default-src 'self'; base-uri 'self'; object-src 'none'; frame-ancestors 'none'; form-action 'self'; img-src 'self' data: blob:; font-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self' https://api.binance.com https://fapi.binance.com wss://stream.binance.com:9443 wss://fstream.binance.com"),
                )
                for key, value in defaults:
                    if key not in present:
                        headers.append((key, value))
                if settings.app_env.strip().lower() in {"production", "prod"} and b"strict-transport-security" not in present:
                    headers.append((b"strict-transport-security", b"max-age=31536000; includeSubDomains"))
                request_path = scope.get("path", "")
                if request_path.startswith("/api/"):
                    for key, value in (
                        (b"cache-control", b"no-store, private"),
                        (b"pragma", b"no-cache"),
                        (b"expires", b"0"),
                    ):
                        if key not in present:
                            headers.append((key, value))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_security_headers)


def J(data: Any, status: int = 200) -> JSONResponse:
    return JSONResponse(jsonable_encoder(data), status_code=status)


async def body(request: Request) -> dict:
    try:
        raw = await request.body()
        return json.loads(raw.decode("utf-8")) if raw else {}
    except Exception:
        return {}


def db_sess():
    return dbmod.SessionLocal()


def _bearer(request: Request) -> str | None:
    a = request.headers.get("authorization")
    if a and a.lower().startswith("bearer "):
        return a[7:].strip()
    return request.cookies.get("titan_session")


def require_user(request: Request, db):
    token = _bearer(request)
    user = auth_service.get_session_user(db, token)
    if auth_service.auth_required() and not user:
        return None, J({"detail": "Authentication required"}, 401)
    return user, None


def _binance_auth_error(request: Request):
    """Always require a valid user session for private Binance Testnet endpoints."""
    db = db_sess()
    try:
        user = auth_service.get_session_user(db, _bearer(request))
        if not user:
            return J({"detail": "Authentication required"}, 401)
        return None
    finally:
        db.close()


async def health(request: Request):
    return J({"ok": True, "service": "titan-ai-premium", "version": "0.1.0-foundation", "http_stack": "starlette"})


async def system_status(request: Request):
    db = db_sess()
    try:
        # This endpoint may query the authenticated Testnet account status.
        # Do not let anonymous callers trigger private credential use or infer account connectivity.
        user, err = require_user(request, db)
        if err:
            return err
        state_snapshot = core_service.get_state(db)
        binance_status = {"status": "needs_external_service", "configured": False}
        live_status = {"status": "disabled", "configured": False, "execution_ready": False}
        try:
            binance_status = _get_binance_adapter(state_snapshot.get("market_type", "spot")).status()
        except Exception:
            pass
        try:
            live_status = _get_binance_live_adapter(state_snapshot.get("market_type", "spot"), db).status()
            live_status["server_live_enabled"] = False
            live_status["user_live_confirmed"] = bool(state_snapshot.get("live_confirmed_by_user"))
        except Exception:
            pass
        return J({
            "state": state_snapshot,
            "engines": core_service.engines(db),
            "integrations": {
                "binance_public": {"status": "ready", "configured": True, "note": "Read-only public data"},
                "binance_testnet": binance_status,
                "binance_live": live_status,
                "telegram": _telegram_adapter().status(),
                "coingecko_public": {"status": "ready", "configured": True, "note": "Public tokenomics/market metadata; rate limits apply."},
                "crypto_news_rss": {"status": "partial", "configured": True, "note": "Allowlisted public RSS feeds; source coverage may vary."},
                "ai_providers": provider_status(db),
            },
            "demo_worker": __import__("app.services.demo_worker_supervisor", fromlist=["demo_worker_supervisor"]).demo_worker_supervisor.status(),
            "data_policy": {"live_market_data": False, "synthetic_labeled_available": True, "binance_public_available": True, "note": "Distinguish public read-only market data, supervised Binance Testnet execution, and Live read-only monitoring; Live order execution is hard-disabled in this release."},
        })
    finally:
        db.close()


async def update_state(request: Request):
    db = db_sess()
    try:
        user, err = require_user(request, db)
        if err: return err
        p = await body(request)
        return J(core_service.set_state(db, environment_mode=p.get("environment_mode"),
                                        account_target=p.get("account_target"), live_confirm=p.get("live_confirm"),
                                        market_type=p.get("market_type"), autonomous_enabled=p.get("autonomous_enabled"),
                                        emergency_stop=p.get("emergency_stop")))
    except ValueError as e:
        return J({"detail": str(e)}, 400)
    finally:
        db.close()


async def scanner_spot(request: Request):
    """Read-only liquid USDT Spot market discovery endpoint."""
    try:
        max_symbols = int(request.query_params.get("limit", "50"))
        min_volume = float(request.query_params.get("min_quote_volume_usdt", "1000000"))
        if not 1 <= max_symbols <= 100 or min_volume < 0:
            return J({"ok": False, "error": "invalid_scanner_parameters",
                      "limits": {"limit": "1..100", "min_quote_volume_usdt": ">=0"}}, 400)
        return J(core_service.market_data.scan_spot_universe(
            max_symbols=max_symbols, min_quote_volume_usdt=min_volume,
        ))
    except (TypeError, ValueError):
        return J({"ok": False, "error": "invalid_scanner_parameters"}, 400)
    except Exception as e:
        return J({"ok": False, "error": "scanner_data_unavailable", "detail": str(e)}, 503)


async def scanner_futures(request: Request):
    """Read-only liquid active USDT perpetual discovery endpoint."""
    try:
        max_symbols = int(request.query_params.get("limit", "50"))
        min_volume = float(request.query_params.get("min_quote_volume_usdt", "10000000"))
        if not 1 <= max_symbols <= 100 or not math.isfinite(min_volume) or min_volume < 0:
            return J({"ok": False, "error": "invalid_futures_scanner_parameters",
                      "limits": {"limit": "1..100", "min_quote_volume_usdt": ">=0"}}, 400)
        return J(core_service.market_data.scan_futures_universe(
            max_symbols=max_symbols, min_quote_volume_usdt=min_volume,
        ))
    except (TypeError, ValueError):
        return J({"ok": False, "error": "invalid_futures_scanner_parameters"}, 400)
    except Exception as exc:
        return J({"ok": False, "error": "futures_scanner_data_unavailable", "detail": str(exc)[:240]}, 503)


async def smart_scanner_futures(request: Request):
    """Analyze a bounded USD-M perpetual shortlist; analysis only, no orders."""
    db = db_sess()
    try:
        user, err = require_user(request, db)
        if err:
            return err
        selected_state = core_service.get_state(db)
        if selected_state.get("environment_mode") != "demo" or selected_state.get("market_type") != "futures":
            return J({"ok": False, "error": "demo_futures_market_must_be_selected"}, 409)
        from app.services.smart_futures_scanner import SmartFuturesScannerService
        max_candidates = int(request.query_params.get("limit", "10"))
        min_volume = float(request.query_params.get("min_quote_volume_usdt", "10000000"))
        balance = float(request.query_params.get("balance", "150"))
        risk_per_trade = float(request.query_params.get("risk_per_trade", "0.005"))
        max_leverage = float(request.query_params.get("max_leverage", "10"))
        result = SmartFuturesScannerService().scan(
            db, max_candidates=max_candidates, min_quote_volume_usdt=min_volume,
            balance=balance, risk_per_trade=risk_per_trade, max_leverage=max_leverage,
        )
        return J(result)
    except (TypeError, ValueError) as exc:
        return J({"ok": False, "error": "invalid_futures_smart_scanner_parameters", "detail": str(exc)}, 400)
    except Exception as exc:
        return J({"ok": False, "error": "futures_smart_scanner_failed", "detail": str(exc)[:240]}, 503)
    finally:
        db.close()


async def smart_scanner(request: Request):
    """Analyze a bounded Spot USDT shortlist with public candles and existing decision logic."""
    db = db_sess()
    try:
        user, err = require_user(request, db)
        if err:
            return err
        from app.services.smart_scanner import SmartScannerService
        max_candidates = int(request.query_params.get("limit", "10"))
        min_volume = float(request.query_params.get("min_quote_volume_usdt", "1000000"))
        balance = float(request.query_params.get("balance", "150"))
        risk_per_trade = float(request.query_params.get("risk_per_trade", "0.005"))
        result = SmartScannerService().scan(
            db,
            max_candidates=max_candidates,
            min_quote_volume_usdt=min_volume,
            balance=balance,
            risk_per_trade=risk_per_trade,
        )
        return J(result)
    except (TypeError, ValueError) as e:
        return J({"ok": False, "error": "invalid_smart_scanner_parameters", "detail": str(e)}, 400)
    except Exception as e:
        db.rollback()
        return J({"ok": False, "error": "smart_scanner_unavailable", "detail": str(e)}, 503)
    finally:
        db.close()


def _validate_ml_public_batch(timeframe: Timeframe, batch):
    """Apply identical provenance, sample-size, timestamp and freshness checks to ML data."""
    if batch is None or getattr(batch.source, "value", None) != "live_public" or len(batch.df) < 50:
        raise ValueError("verified_public_candles_required")
    last = batch.df.rows[-1]
    stamp = last.get("close_time") or last.get("timestamp")
    if isinstance(stamp, bool) or not isinstance(stamp, (int, float)):
        raise ValueError("public_candle_timestamp_required")
    stamp = float(stamp)
    if not __import__("math").isfinite(stamp) or stamp <= 0:
        raise ValueError("public_candle_timestamp_required")
    age = __import__("time").time() - stamp / 1000.0
    duration = {
        Timeframe.D1: 86400, Timeframe.H4: 14400, Timeframe.H1: 3600,
        Timeframe.M15: 900, Timeframe.M5: 300,
    }[timeframe]
    if age < -60 or age > max(300, duration * 2):
        raise ValueError("stale_public_candles")
    return timeframe, batch


def _ml_timeframe_batch(symbol: str, timeframe_value: str):
    timeframe = Timeframe(timeframe_value)
    batches = core_service.market_data.get_public_multi_timeframe(symbol)
    return _validate_ml_public_batch(timeframe, batches.get(timeframe))


def _ml_drift_timeframe_batch(symbol: str, timeframe_value: str):
    timeframe = Timeframe(timeframe_value)
    batch = core_service.market_data.get_public_candles(
        symbol, timeframe, limit=600, market_type="spot",
    )
    return _validate_ml_public_batch(timeframe, batch)


def _telegram_adapter():
    from app.integrations.telegram_adapter import TelegramAdapter, TelegramConfig
    enabled = os.getenv("TITAN_TELEGRAM_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
    return TelegramAdapter(TelegramConfig(
        enabled=enabled,
        bot_token_ref=os.getenv("TITAN_TELEGRAM_BOT_TOKEN_REF", "env:TELEGRAM_BOT_TOKEN"),
        allowed_chat_ids_ref=os.getenv("TITAN_TELEGRAM_ALLOWED_CHAT_IDS_REF", "env:TELEGRAM_ALLOWED_CHAT_IDS"),
    ))


async def telegram_status(request: Request):
    db = db_sess()
    try:
        user, err = require_user(request, db)
        if err: return err
        return J(_telegram_adapter().status())
    finally:
        db.close()


async def telegram_poll(request: Request):
    db = db_sess()
    try:
        user, err = require_user(request, db)
        if err: return err
        adapter = _telegram_adapter()
        result = adapter.get_updates(
            offset=int(request.query_params["offset"]) if "offset" in request.query_params else None,
            timeout=int(request.query_params.get("timeout", "0")),
            limit=int(request.query_params.get("limit", "100")),
        )
        return J(result, 200 if result.get("ok") else 503)
    except (TypeError, ValueError) as exc:
        return J({"ok": False, "error": "invalid_telegram_poll_parameters", "detail": str(exc)}, 400)
    finally:
        db.close()


async def telegram_send(request: Request):
    db = db_sess()
    try:
        user, err = require_user(request, db)
        if err: return err
        p = await body(request)
        result = _telegram_adapter().send_message(str(p.get("chat_id", "")), str(p.get("text", "")))
        return J(result, 200 if result.get("ok") else 400)
    finally:
        db.close()


async def prediction_evaluations(request: Request):
    db = db_sess()
    try:
        user, err = require_user(request, db)
        if err: return err
        from app.services.prediction_tracker import PredictionTrackerService
        items = PredictionTrackerService().list_evaluations(
            db, limit=int(request.query_params.get("limit", "100")),
        )
        return J({"items": items, "count": len(items), "note": "Mark-to-market decision observations, not realized trade PnL."})
    except (TypeError, ValueError) as exc:
        return J({"ok": False, "error": "invalid_prediction_limit", "detail": str(exc)}, 400)
    finally:
        db.close()


async def evaluate_predictions(request: Request):
    db = db_sess()
    try:
        user, err = require_user(request, db)
        if err: return err
        p = await body(request)
        from app.services.prediction_tracker import PredictionTrackerService
        result = PredictionTrackerService().evaluate_matured(
            db,
            environment_mode=str(p.get("environment_mode", "demo")).lower(),
            horizon_seconds=int(p.get("horizon_seconds", 3600)),
            max_records=int(p.get("max_records", 200)),
        )
        return J(result, 200 if result.get("ok") else 503)
    except (TypeError, ValueError) as exc:
        db.rollback()
        return J({"ok": False, "error": "invalid_prediction_evaluation_parameters", "detail": str(exc)}, 400)
    finally:
        db.close()


async def performance_analytics(request: Request):
    db = db_sess()
    try:
        user, err = require_user(request, db)
        if err: return err
        environment_mode = str(request.query_params.get("environment_mode", "demo")).lower()
        market_type = str(request.query_params.get("market_type", "spot")).lower()
        from app.services.performance_analytics import PerformanceAnalyticsService
        result = PerformanceAnalyticsService().evaluate(
            db, environment_mode=environment_mode, market_type=market_type,
        )
        return J(result, 200 if result.get("ok") else 400)
    finally:
        db.close()


async def backtest_strategy(request: Request):
    db = db_sess()
    try:
        user, err = require_user(request, db)
        if err: return err
        p = await body(request)
        symbol = str(p.get("symbol", "BTC/USDT")).strip().upper().replace("-", "/")
        if not symbol.endswith("/USDT") or len(symbol) > 32:
            return J({"ok": False, "error": "usdt_spot_symbol_required"}, 400)
        timeframe_value = str(p.get("timeframe", "1h")).lower()
        try:
            timeframe = Timeframe(timeframe_value)
        except ValueError:
            return J({"ok": False, "error": "unsupported_timeframe"}, 400)
        limit = int(p.get("limit", 240))
        if not 60 <= limit <= 500:
            return J({"ok": False, "error": "candle_limit_must_be_60_to_500"}, 400)
        batch = core_service.market_data.get_public_candles(symbol, timeframe, limit=limit)
        if getattr(batch.source, "value", None) != "live_public" or len(batch.df) < 60:
            return J({"ok": False, "error": "verified_public_historical_candles_required"}, 409)
        rows = batch.df.rows
        stamp = rows[-1].get("close_time") or rows[-1].get("timestamp")
        if not isinstance(stamp, (int, float)):
            return J({"ok": False, "error": "historical_candle_timestamp_required"}, 409)
        duration = {
            Timeframe.D1: 86400, Timeframe.H4: 14400, Timeframe.H1: 3600,
            Timeframe.M15: 900, Timeframe.M5: 300,
        }[timeframe]
        age = __import__("time").time() - float(stamp) / 1000.0
        if age < -60 or age > max(300, duration * 2):
            return J({"ok": False, "error": "stale_historical_candles"}, 409)

        from app.engines.backtest import BacktestEngine
        engine = BacktestEngine(
            fee_rate=float(p.get("fee_rate", 0.001)),
            slippage_rate=float(p.get("slippage_rate", 0.0005)),
            initial_cash=float(p.get("initial_cash", 150.0)),
        )
        strategy_key = str(p.get("strategy_key", "trend_following_ema"))
        mode = str(p.get("mode", "historical")).lower()
        if mode == "walk_forward":
            result = engine.run_strategy_walk_forward(
                rows, strategy_key=strategy_key, timeframe=timeframe,
                train_window=int(p.get("train_window", 60)),
                test_window=int(p.get("test_window", 30)),
                step=int(p.get("step", p.get("test_window", 30))),
            )
        elif mode == "historical":
            result = engine.run_strategy(rows, strategy_key=strategy_key, timeframe=timeframe)
        else:
            return J({"ok": False, "error": "invalid_backtest_mode"}, 400)
        return J({
            **result,
            "symbol": symbol,
            "timeframe": timeframe.value,
            "candles": len(rows),
            "data_source": "binance_public_read_only",
            "initial_cash": engine.initial_cash,
            "fee_rate": engine.fee_rate,
            "slippage_rate": engine.slippage_rate,
            "execution": "simulated_only",
            "note": "Historical simulation only. It does not use account balances, send orders, or prove future profitability.",
        }, 200 if result.get("ok", True) else 422)
    except (TypeError, ValueError) as exc:
        db.rollback()
        return J({"ok": False, "error": "invalid_backtest_parameters", "detail": str(exc)}, 400)
    except Exception as exc:
        db.rollback()
        return J({"ok": False, "error": "backtest_failed", "detail": str(exc)[:240]}, 503)
    finally:
        db.close()


async def futures_backtest(request: Request):
    db = db_sess()
    try:
        user, err = require_user(request, db)
        if err: return err
        p = await body(request)
        symbol = str(p.get("symbol", "BTC/USDT")).strip().upper().replace("-", "/")
        if not symbol.endswith("/USDT") or len(symbol) > 32:
            return J({"ok": False, "error": "usdt_futures_symbol_required"}, 400)
        timeframe_value = str(p.get("timeframe", "1h")).lower()
        try:
            timeframe = Timeframe(timeframe_value)
        except ValueError:
            return J({"ok": False, "error": "unsupported_timeframe"}, 400)
        limit = p.get("limit", 240)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 60 <= limit <= 500:
            return J({"ok": False, "error": "candle_limit_must_be_60_to_500"}, 400)
        batch = core_service.market_data.get_public_candles(
            symbol, timeframe, limit=limit, market_type="futures",
        )
        if (getattr(batch.source, "value", None) != "live_public"
                or getattr(batch, "market_type", None) != "futures" or len(batch.df) < 60):
            return J({"ok": False, "error": "verified_public_futures_candles_required"}, 409)
        rows = batch.df.rows
        start_stamp = rows[0].get("timestamp") or rows[0].get("close_time")
        end_stamp = rows[-1].get("close_time") or rows[-1].get("timestamp")
        if (isinstance(start_stamp, bool) or isinstance(end_stamp, bool)
                or not isinstance(start_stamp, (int, float)) or not isinstance(end_stamp, (int, float))):
            return J({"ok": False, "error": "futures_candle_timestamps_required"}, 409)
        duration = {
            Timeframe.D1: 86400, Timeframe.H4: 14400, Timeframe.H1: 3600,
            Timeframe.M15: 900, Timeframe.M5: 300,
        }[timeframe]
        age = __import__("time").time() - float(end_stamp) / 1000.0
        if age < -60 or age > max(300, duration * 2):
            return J({"ok": False, "error": "stale_futures_historical_candles"}, 409)
        funding_rows = core_service.market_data.get_public_funding_rates(
            symbol, start_time=int(start_stamp), end_time=int(end_stamp),
        )
        from app.engines.futures_backtest import FuturesBacktestEngine
        requested_cap = p.get("max_notional", settings.max_futures_trade_notional)
        if isinstance(requested_cap, bool):
            return J({"ok": False, "error": "invalid_max_notional"}, 400)
        max_notional = min(float(requested_cap), float(settings.max_futures_trade_notional))
        requested_leverage = p.get("max_leverage", settings.max_leverage_cap)
        if isinstance(requested_leverage, bool):
            return J({"ok": False, "error": "invalid_max_leverage"}, 400)
        max_leverage = min(float(requested_leverage), float(settings.max_leverage_cap))
        engine = FuturesBacktestEngine(
            initial_cash=p.get("initial_cash", 150.0),
            fee_rate=p.get("fee_rate", 0.0004),
            slippage_rate=p.get("slippage_rate", 0.0005),
            max_notional=max_notional,
            max_leverage=max_leverage,
            maintenance_margin_rate=p.get("maintenance_margin_rate", 0.005),
            liquidation_fee_rate=p.get("liquidation_fee_rate", 0.005),
        )
        strategy_key = str(p.get("strategy_key", "trend_following_ema"))
        result = engine.run_strategy(
            rows,
            strategy_key=strategy_key,
            timeframe=timeframe,
            funding_events=funding_rows,
            leverage=p.get("leverage", 3.0),
            notional=p.get("notional"),
            entry_fill_ratio=p.get("entry_fill_ratio", 1.0),
            exit_fill_ratio=p.get("exit_fill_ratio", 1.0),
        )
        return J({
            **result, "symbol": symbol, "timeframe": timeframe.value,
            "candles": len(rows), "funding_events": len(funding_rows),
            "data_source": "binance_usdm_futures_public_read_only",
            "initial_cash": engine.initial_cash, "max_notional": engine.max_notional,
            "max_leverage": engine.max_leverage,
            "execution": "simulated_only",
            "note": "Futures research simulation only. Funding is fetched from public history; liquidation and margin are estimates, not Binance exchange-calculated outcomes.",
        }, 200 if result.get("ok") else 422)
    except (TypeError, ValueError) as exc:
        db.rollback()
        return J({"ok": False, "error": "invalid_futures_backtest_parameters", "detail": str(exc)}, 400)
    except Exception as exc:
        db.rollback()
        return J({"ok": False, "error": "futures_backtest_failed", "detail": str(exc)[:240]}, 503)
    finally:
        db.close()


async def compare_strategy_backtests(request: Request):
    """Compare all requested candle-based strategies on the same public history."""
    db = db_sess()
    try:
        user, err = require_user(request, db)
        if err:
            return err
        p = await body(request)
        for numeric_key in ("initial_cash", "fee_rate", "slippage_rate", "max_notional", "max_leverage", "leverage"):
            if numeric_key in p and isinstance(p[numeric_key], bool):
                return J({"ok": False, "error": f"boolean_{numeric_key}_not_allowed"}, 400)
        market_type = str(p.get("market_type", "spot")).strip().lower()
        if market_type not in {"spot", "futures"}:
            return J({"ok": False, "error": "market_type_must_be_spot_or_futures"}, 400)
        symbol = str(p.get("symbol", "BTC/USDT")).strip().upper().replace("-", "/")
        if not symbol.endswith("/USDT") or len(symbol) > 32:
            return J({"ok": False, "error": "usdt_symbol_required"}, 400)
        try:
            timeframe = Timeframe(str(p.get("timeframe", "1h")).lower())
        except ValueError:
            return J({"ok": False, "error": "unsupported_timeframe"}, 400)
        limit = p.get("limit", 240)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 60 <= limit <= 500:
            return J({"ok": False, "error": "candle_limit_must_be_60_to_500"}, 400)
        batch = core_service.market_data.get_public_candles(
            symbol, timeframe, limit=limit, market_type=market_type,
        )
        if (getattr(batch.source, "value", None) != "live_public"
                or len(batch.df) < 60
                or (market_type == "futures" and getattr(batch, "market_type", None) != "futures")):
            return J({"ok": False, "error": "verified_public_historical_candles_required"}, 409)
        rows = batch.df.rows
        stamp = rows[-1].get("close_time") or rows[-1].get("timestamp")
        if isinstance(stamp, bool) or not isinstance(stamp, (int, float)):
            return J({"ok": False, "error": "historical_candle_timestamp_required"}, 409)
        duration = {
            Timeframe.D1: 86400, Timeframe.H4: 14400, Timeframe.H1: 3600,
            Timeframe.M15: 900, Timeframe.M5: 300,
        }[timeframe]
        age = __import__("time").time() - float(stamp) / 1000.0
        if age < -60 or age > max(300, duration * 2):
            return J({"ok": False, "error": "stale_historical_candles"}, 409)
        funding = []
        if market_type == "futures":
            start_stamp = rows[0].get("timestamp") or rows[0].get("close_time")
            funding = core_service.market_data.get_public_funding_rates(
                symbol, start_time=int(start_stamp), end_time=int(stamp),
            )
        from app.engines.strategy_backtest_suite import StrategyBacktestSuite
        if market_type == "futures":
            max_notional = min(
                float(p.get("max_notional", settings.max_futures_trade_notional)),
                float(settings.max_futures_trade_notional),
            )
        else:
            max_notional = min(float(p.get("max_notional", 80.0)), 80.0)
        suite = StrategyBacktestSuite()
        result = suite.compare(
            rows, market_type=market_type, timeframe=timeframe.value,
            strategy_keys=p.get("strategy_keys"),
            initial_cash=p.get("initial_cash", 150.0),
            fee_rate=p.get("fee_rate"),
            slippage_rate=p.get("slippage_rate", 0.0005),
            max_notional=max_notional,
            max_leverage=min(float(p.get("max_leverage", settings.max_leverage_cap)),
                             float(settings.max_leverage_cap)),
            leverage=p.get("leverage", 1.0),
            funding_events=funding,
        )
        return J({
            **result, "symbol": symbol, "candles": len(rows),
            "funding_events": len(funding),
            "data_source": "binance_usdm_futures_public_read_only" if market_type == "futures" else "binance_public_read_only",
            "initial_cash": p.get("initial_cash", 150.0),
            "execution": "simulated_only", "no_live_orders": True,
        }, 200 if result.get("ok") else 422)
    except (TypeError, ValueError) as exc:
        db.rollback()
        return J({"ok": False, "error": "invalid_strategy_comparison_parameters", "detail": str(exc)[:200]}, 400)
    except Exception as exc:
        db.rollback()
        return J({"ok": False, "error": "strategy_comparison_failed", "detail": str(exc)[:200]}, 503)
    finally:
        db.close()


async def capture_triangle_orderbook(request: Request):
    """Capture and persist one synchronized three-market Spot depth snapshot."""
    db = db_sess()
    try:
        user, err = require_user(request, db)
        if err:
            return err
        p = await body(request)
        route = p.get("route", ["USDT", "BTC", "ETH", "USDT"])
        if (not isinstance(route, list) or len(route) != 4
                or route[0] != route[-1] or len(set(route[:3])) != 3
                or any(not isinstance(asset, str) or not asset.isascii()
                       or not asset.isalnum() or not 2 <= len(asset) <= 12 for asset in route)):
            return J({"ok": False, "error": "route_must_be_three_distinct_closed_assets"}, 400)
        limit = p.get("depth_limit", 100)
        if isinstance(limit, bool) or limit not in {5, 10, 20, 50, 100, 500, 1000}:
            return J({"ok": False, "error": "unsupported_orderbook_depth_limit"}, 400)
        quotes = {}
        stamps = []
        for index in range(3):
            left, right = route[index], route[index + 1]
            found = None
            errors = []
            for candidate in (left + right, right + left):
                try:
                    quote = core_service.market_data.get_public_order_book(candidate, limit=limit, market_type="spot")
                    if quote.get("source") != "binance_public_read_only" or not quote.get("bids") or not quote.get("asks"):
                        errors.append("unverified_or_empty_public_depth")
                        continue
                    stamp = datetime.fromisoformat(str(quote["fetched_at"]).replace("Z", "+00:00"))
                    if stamp.tzinfo is None:
                        errors.append("naive_snapshot_timestamp")
                        continue
                    found = (candidate, quote, int(stamp.timestamp() * 1000))
                    break
                except Exception as exc:
                    errors.append(str(exc)[:120])
            if found is None:
                return J({"ok": False, "error": f"public_orderbook_unavailable_for_leg_{index}",
                          "pair_candidates": [left + right, right + left], "details": errors}, 503)
            symbol, quote, stamp_ms = found
            if symbol in quotes:
                return J({"ok": False, "error": "triangle_requires_three_distinct_orderbook_markets"}, 400)
            quotes[symbol] = quote
            stamps.append(stamp_ms)
        skew_ms = max(stamps) - min(stamps)
        if skew_ms > 5000:
            return J({"ok": False, "error": "orderbook_snapshots_not_synchronized",
                      "snapshot_skew_ms": skew_ms, "maximum_skew_ms": 5000}, 503)
        captured_at_ms = int(sum(stamps) / len(stamps))
        row = OrderBookSnapshotRecord(
            captured_at_ms=captured_at_ms, market_type="spot",
            route=route, quotes=quotes, source="binance_public_read_only",
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return J({
            "ok": True, "snapshot_id": row.id, "captured_at_ms": captured_at_ms,
            "market_type": "spot", "route": route, "markets": list(quotes),
            "depth_limit": limit, "snapshot_skew_ms": skew_ms,
            "source": "binance_public_read_only", "persisted": True,
            "note": "Historical order-book recording starts at this capture; no retroactive depth history is available.",
        })
    except Exception as exc:
        db.rollback()
        return J({"ok": False, "error": "triangle_orderbook_capture_failed", "detail": str(exc)[:200]}, 503)
    finally:
        db.close()


async def capture_futures_orderbook(request: Request):
    """Capture one authenticated public USD-M Futures depth snapshot for forward history."""
    db = db_sess()
    try:
        user, err = require_user(request, db)
        if err:
            return err
        p = await body(request)
        symbol = str(p.get("symbol", "")).replace("/", "").upper()
        if not symbol.isascii() or not symbol.isalnum() or not 5 <= len(symbol) <= 30:
            return J({"ok": False, "error": "invalid_futures_symbol"}, 400)
        limit = p.get("depth_limit", 100)
        if isinstance(limit, bool) or not isinstance(limit, int) or limit not in {5, 10, 20, 50, 100, 500, 1000}:
            return J({"ok": False, "error": "unsupported_orderbook_depth_limit"}, 400)
        quote = core_service.market_data.get_public_order_book(symbol, limit=limit, market_type="futures")
        if quote.get("source") != "binance_futures_public_read_only":
            return J({"ok": False, "error": "unverified_futures_orderbook_source"}, 503)
        stamp = datetime.fromisoformat(str(quote["fetched_at"]).replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            return J({"ok": False, "error": "naive_orderbook_timestamp"}, 503)
        stamp_ms = int(stamp.timestamp() * 1000)
        row = OrderBookSnapshotRecord(
            captured_at_ms=stamp_ms, market_type="futures",
            route=[symbol], quotes={"symbol": quote},
            source="binance_futures_public_read_only",
        )
        db.add(row); db.commit(); db.refresh(row)
        return J({"ok": True, "snapshot_id": row.id, "symbol": symbol,
            "captured_at_ms": stamp_ms, "market_type": "futures",
            "depth_limit": limit, "bid_depth_quote": quote["bid_depth_quote"],
            "ask_depth_quote": quote["ask_depth_quote"], "depth_imbalance": quote["depth_imbalance"],
            "source": "binance_futures_public_read_only", "persisted": True,
            "historical_mode": "forward_capture_only",
            "note": "Binance REST does not provide retroactive historical depth snapshots; call this endpoint periodically to build history."})
    except Exception as exc:
        db.rollback()
        return J({"ok": False, "error": "futures_orderbook_capture_failed", "detail": str(exc)[:180]}, 503)
    finally:
        db.close()


async def historical_futures_orderbook_backtest(request: Request):
    """Replay previously captured USD-M Futures order-book snapshots offline."""
    db = db_sess()
    try:
        user, err = require_user(request, db)
        if err:
            return err
        p = await body(request)
        symbol = str(p.get("symbol", "")).replace("/", "").upper()
        if not symbol.isascii() or not symbol.isalnum() or not 5 <= len(symbol) <= 30:
            return J({"ok": False, "error": "invalid_futures_symbol"}, 400)
        start_time = p.get("start_time", 1)
        end_time = p.get("end_time", int(datetime.now(timezone.utc).timestamp() * 1000))
        limit = p.get("max_snapshots", 5000)
        if (isinstance(start_time, bool) or not isinstance(start_time, int) or start_time <= 0
                or isinstance(end_time, bool) or not isinstance(end_time, int) or end_time <= start_time
                or isinstance(limit, bool) or not isinstance(limit, int) or not 2 <= limit <= 20000):
            return J({"ok": False, "error": "invalid_futures_orderbook_replay_range"}, 400)
        rows = db.execute(
            select(OrderBookSnapshotRecord)
            .where(OrderBookSnapshotRecord.market_type == "futures",
                   OrderBookSnapshotRecord.captured_at_ms >= start_time,
                   OrderBookSnapshotRecord.captured_at_ms <= end_time)
            .order_by(OrderBookSnapshotRecord.captured_at_ms.asc())
            .limit(limit)
        ).scalars().all()
        selected = [row for row in rows if row.route == [symbol]]
        if len(selected) < 7:
            return J({"ok": False, "error": "insufficient_recorded_futures_orderbook_history",
                "snapshots_found": len(selected), "minimum_required": 7,
                "note": "Build prospective history by capturing snapshots periodically; no retroactive depth data is available."}, 404)
        from app.engines.specialized_backtest import SpecializedBacktestEngine
        engine = SpecializedBacktestEngine(
            initial_cash=p.get("initial_cash", 1000.0),
            fee_rate=p.get("fee_rate", 0.0004),
            slippage_rate=p.get("slippage_rate", 0.0002),
            max_notional=min(float(p.get("max_notional", 800.0)), 800.0),
        )
        snapshots = [{"timestamp": row.captured_at_ms, "symbol": symbol, "quotes": row.quotes}
                     for row in selected]
        funding_events = p.get("funding_events")
        funding_source = "user_supplied"
        if funding_events is None:
            try:
                funding_events = core_service.market_data.get_public_funding_rates(
                    symbol, start_time=start_time, end_time=end_time,
                )
                funding_source = "binance_usdm_public_funding_history"
            except Exception:
                funding_events = []
                funding_source = "unavailable_funding_history"
        result = engine.run_futures_orderbook_replay(
            snapshots, rolling_window=p.get("rolling_window", 5),
            entry_imbalance=p.get("entry_imbalance", 0.20),
            exit_imbalance=p.get("exit_imbalance", 0.02),
            leverage=p.get("leverage", 2.0),
            notional=min(float(p.get("max_notional", 800.0)), 800.0),
            funding_events=funding_events,
        )
        result["funding_data_source"] = funding_source
        result.update({"snapshots_fetched":len(selected),"requested_start_time":start_time,
            "requested_end_time":end_time,"data_source":"titan_recorded_binance_usdm_futures_depth",
            "execution":"simulated_only","no_live_orders":True})
        return J(result, 200 if result.get("ok") else 422)
    except (TypeError, ValueError) as exc:
        db.rollback()
        return J({"ok":False,"error":"invalid_futures_orderbook_replay_parameters","detail":str(exc)[:160]},400)
    except Exception as exc:
        db.rollback()
        return J({"ok":False,"error":"futures_orderbook_replay_failed","detail":str(exc)[:160]},503)
    finally:
        db.close()


async def risk_simulation_backtest(request: Request):
    """Authenticated demo-only risk replay; never submits exchange orders."""
    db = db_sess()
    try:
        user, err = require_user(request, db)
        if err:
            return err
        p = await body(request)
        if p.get("mode") == "cross_margin":
            positions = p.get("positions")
            if not isinstance(positions, list) or not 1 <= len(positions) <= 60:
                return J({"ok": False, "error": "cross_margin_positions_must_be_list_of_1_to_60"}, 400)
            from app.engines.cross_margin import simulate_cross_margin_account
            result = simulate_cross_margin_account(
                wallet_balance=p.get("wallet_balance", 0), positions=positions,
                liquidation_fee_rate=p.get("liquidation_fee_rate", 0.005),
            )
            return J({**result, "simulation_only": True, "no_live_orders": True},
                     200 if result.get("ok") else 400)
        for key in ("initial_balance", "risk_per_trade", "max_daily_loss", "max_exposure"):
            if key in p and isinstance(p[key], bool):
                return J({"ok": False, "error": f"boolean_{key}_not_allowed"}, 400)
        market_type = str(p.get("market_type", "spot")).strip().lower()
        if market_type not in {"spot", "futures"}:
            return J({"ok": False, "error": "market_type_must_be_spot_or_futures"}, 400)
        from app.core.enums import MarketType
        from app.engines.risk_simulation import RiskSimulationEngine
        simulator = RiskSimulationEngine()
        market = MarketType.SPOT if market_type == "spot" else MarketType.FUTURES
        if "candles" in p or p.get("symbol"):
            data_source = "user_supplied_ohlcv"
            if "candles" in p:
                candles = p.get("candles")
            else:
                symbol = str(p.get("symbol","")).replace("/","").upper()
                timeframe = p.get("timeframe","1h")
                limit = p.get("limit",500)
                if isinstance(limit,bool) or not isinstance(limit,int) or not 30<=limit<=1000:
                    return J({"ok":False,"error":"historical_risk_limit_must_be_30_to_1000"},400)
                tf = timeframe if isinstance(timeframe,Timeframe) else Timeframe(str(timeframe))
                batch = core_service.market_data.get_public_candles(
                    symbol, tf, limit=limit, market_type=market_type,
                )
                candles = batch.df.to_records()
                data_source = batch.source.value
            if not isinstance(candles, list) or not 30<=len(candles)<=100000:
                return J({"ok": False, "error": "candles_must_be_list_with_30_to_100000_items"}, 400)
            funding_events = p.get("funding_events")
            funding_source = "user_supplied" if funding_events is not None else "not_applicable"
            if funding_events is None and market_type=="futures":
                try:
                    start_stamp=int(candles[0].get("timestamp",candles[0].get("open_time")))
                    end_stamp=int(candles[-1].get("timestamp",candles[-1].get("open_time")))
                    funding_events=core_service.market_data.get_public_funding_rates(
                        str(p.get("symbol","")).replace("/","").upper(),
                        start_time=start_stamp,end_time=end_stamp,
                    )
                    funding_source="binance_usdm_public_funding_history"
                except Exception:
                    funding_events=[]
                    funding_source="unavailable_funding_history"
            if funding_events is None:
                funding_events=[]
            result = simulator.run_historical_price_path(
                candles, initial_balance=p.get("initial_balance",1000.0), market=market,
                risk_per_trade=p.get("risk_per_trade",0.005),
                max_daily_loss=p.get("max_daily_loss",0.07),
                fee_rate=p.get("fee_rate",0.001 if market_type=="spot" else 0.0004),
                slippage_rate=p.get("slippage_rate",0.0005),
                max_notional=p.get("max_notional",80.0 if market_type=="spot" else 800.0),
                leverage=p.get("leverage",1.0 if market_type=="spot" else 2.0),
                funding_events=funding_events,
                max_trades=p.get("max_trades",1000),
                session_ms=p.get("session_ms",86400000),
            )
            result.update({"data_source":data_source,"symbol":p.get("symbol"),
                "timeframe":p.get("timeframe"),"funding_data_source":funding_source})

        else:
            trades = p.get("trades")
            if not isinstance(trades, list) or len(trades) > 500:
                return J({"ok": False, "error": "trades_must_be_list_with_max_500_items"}, 400)
            max_open_positions = p.get("max_open_positions", 1)
            if isinstance(max_open_positions, bool) or not isinstance(max_open_positions, int) or not 0 <= max_open_positions <= 100:
                return J({"ok": False, "error": "invalid_max_open_positions"}, 400)
            result = simulator.run(
                trades, initial_balance=p.get("initial_balance",1000.0), market=market,
                risk_per_trade=p.get("risk_per_trade",0.005),
                max_daily_loss=p.get("max_daily_loss",0.07),
                max_open_positions=max_open_positions, max_exposure=p.get("max_exposure"),
            )
        return J({**result, "simulation_only": True, "no_live_orders": True},
                 200 if result.get("ok") else 400)
    except (TypeError, ValueError) as exc:
        return J({"ok": False, "error": "invalid_risk_simulation_parameters", "detail": str(exc)[:160]}, 400)
    except Exception as exc:
        return J({"ok": False, "error": "risk_simulation_failed", "detail": str(exc)[:160]}, 503)
    finally:
        db.close()


async def historical_orderbook_arbitrage_backtest(request: Request):
    """Replay only order-book snapshots previously captured and persisted by TITAN."""
    db = db_sess()
    try:
        user, err = require_user(request, db)
        if err:
            return err
        p = await body(request)
        route = p.get("route", ["USDT", "BTC", "ETH", "USDT"])
        if (not isinstance(route, list) or len(route) != 4
                or route[0] != route[-1] or len(set(route[:3])) != 3
                or any(not isinstance(asset, str) or not asset.isascii()
                       or not asset.isalnum() or not 2 <= len(asset) <= 12 for asset in route)):
            return J({"ok": False, "error": "route_must_be_three_distinct_closed_assets"}, 400)
        start_time = p.get("start_time", 1)
        end_time = p.get("end_time", int(datetime.now(timezone.utc).timestamp() * 1000))
        max_snapshots = p.get("max_snapshots", 5000)
        if (isinstance(start_time, bool) or not isinstance(start_time, int)
                or isinstance(end_time, bool) or not isinstance(end_time, int)
                or start_time <= 0 or end_time <= start_time
                or isinstance(max_snapshots, bool) or not isinstance(max_snapshots, int)
                or not 1 <= max_snapshots <= 5000):
            return J({"ok": False, "error": "invalid_orderbook_replay_range_or_limit"}, 400)
        rows = db.execute(
            select(OrderBookSnapshotRecord)
            .where(OrderBookSnapshotRecord.market_type == "spot",
                   OrderBookSnapshotRecord.captured_at_ms >= start_time,
                   OrderBookSnapshotRecord.captured_at_ms <= end_time)
            .order_by(OrderBookSnapshotRecord.captured_at_ms.asc())
            .limit(max_snapshots)
        ).scalars().all()
        selected = [row for row in rows if row.route == route]
        if not selected:
            return J({"ok": False, "error": "no_persisted_orderbook_snapshots_for_route",
                      "snapshots_found": 0, "note": "Start snapshot capture first; historical Binance order-book depth is not retroactively available."}, 404)
        from app.engines.specialized_backtest import SpecializedBacktestEngine
        engine = SpecializedBacktestEngine(
            initial_cash=p.get("initial_cash", 1000.0),
            fee_rate=p.get("fee_rate", 0.001),
            slippage_rate=0.0,
            max_notional=p.get("max_notional", 80.0),
        )
        snapshots = [{"timestamp": row.captured_at_ms, "quotes": row.quotes} for row in selected]
        symbol_filters = p.get("symbol_filters")
        if symbol_filters is None:
            # Current public exchange filters are the best available fallback.
            # Historical filter versions must be supplied explicitly for exact date fidelity.
            from app.data.binance_public import BinancePublicData
            exchange_info = BinancePublicData().get_spot_exchange_info()
            wanted_symbols = set()
            for row in selected:
                wanted_symbols.update(str(key).replace("/", "").upper() for key in row.quotes)
            symbol_filters = {}
            for market in exchange_info.get("symbols", []):
                symbol = str(market.get("symbol", "")).upper()
                if symbol not in wanted_symbols:
                    continue
                raw_filters = {item.get("filterType"): item for item in market.get("filters", [])}
                price_filter = raw_filters.get("PRICE_FILTER", {})
                lot_filter = raw_filters.get("LOT_SIZE", {})
                notional_filter = raw_filters.get("NOTIONAL") or raw_filters.get("MIN_NOTIONAL") or {}
                symbol_filters[symbol] = {
                    "tick_size": float(price_filter.get("tickSize", 0) or 0),
                    "step_size": float(lot_filter.get("stepSize", 0) or 0),
                    "min_qty": float(lot_filter.get("minQty", 0) or 0),
                    "min_notional": float(notional_filter.get("minNotional", 0) or 0),
                }
        result = engine.run_triangular_execution_replay(
            snapshots, route=route, min_profit_pct=p.get("min_profit_pct", 0.05),
            latency_ms=p.get("latency_ms", 250),
            symbol_filters=symbol_filters,
            max_cycles=p.get("max_cycles", 1000),
        )
        result.update({
            "data_source": "titan_persisted_binance_spot_orderbook_snapshots",
            "snapshots_fetched": len(selected),
            "requested_start_time": start_time, "requested_end_time": end_time,
            "execution": "simulated_only", "no_live_orders": True,
        })
        return J(result, 200 if result.get("ok") else 422)
    except (TypeError, ValueError) as exc:
        db.rollback()
        return J({"ok": False, "error": "invalid_orderbook_replay_parameters", "detail": str(exc)[:200]}, 400)
    except Exception as exc:
        db.rollback()
        return J({"ok": False, "error": "historical_orderbook_replay_failed", "detail": str(exc)[:200]}, 503)
    finally:
        db.close()


async def binance_triangular_arbitrage_backtest(request: Request):
    """Fetch synchronized public Binance candle histories for a three-leg Spot cycle."""
    db = db_sess()
    try:
        user, err = require_user(request, db)
        if err:
            return err
        p = await body(request)
        for key in ("initial_cash", "fee_rate", "slippage_rate", "min_profit_pct", "max_notional"):
            if isinstance(p.get(key), bool):
                return J({"ok": False, "error": f"boolean_{key}_not_allowed"}, 400)
        if str(p.get("market_type", "spot")).strip().lower() != "spot":
            return J({"ok": False, "error": "historical_triangle_candle_replay_is_spot_only"}, 400)
        route = p.get("route", ["USDT", "BTC", "ETH", "USDT"])
        if (not isinstance(route, list) or len(route) != 4
                or route[0] != route[-1] or len(set(route[:3])) != 3
                or any(not isinstance(asset, str) or not asset.isascii()
                       or not asset.isalnum() or not 2 <= len(asset) <= 12 for asset in route)):
            return J({"ok": False, "error": "route_must_be_three_distinct_closed_assets"}, 400)
        limit = p.get("limit", 240)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 60 <= limit <= 500:
            return J({"ok": False, "error": "candle_limit_must_be_60_to_500"}, 400)
        try:
            timeframe = Timeframe(str(p.get("timeframe", "1h")).lower())
        except ValueError:
            return J({"ok": False, "error": "unsupported_timeframe"}, 400)
        histories = {}
        pair_symbols = []
        for index in range(3):
            asset_in, asset_out = route[index], route[index + 1]
            found = None
            errors = []
            for candidate in (asset_in + asset_out, asset_out + asset_in):
                try:
                    batch = core_service.market_data.get_public_candles(
                        candidate, timeframe, limit=limit, market_type="spot",
                    )
                    if getattr(batch.source, "value", None) != "live_public" or len(batch.df) < 60:
                        errors.append("non_live_or_insufficient_public_history")
                        continue
                    found = (candidate, batch.df.rows)
                    break
                except Exception as exc:
                    errors.append(str(exc)[:120])
            if found is None:
                return J({
                    "ok": False, "error": f"historical_public_pair_data_unavailable_for_leg_{index}",
                    "pair_candidates": [asset_in + asset_out, asset_out + asset_in],
                    "details": errors,
                }, 503)
            pair_symbol, rows = found
            if pair_symbol in histories:
                return J({"ok": False, "error": "triangle_requires_three_distinct_market_pairs"}, 400)
            histories[pair_symbol] = rows
            pair_symbols.append(pair_symbol)
        from app.engines.specialized_backtest import SpecializedBacktestEngine
        engine = SpecializedBacktestEngine(
            initial_cash=p.get("initial_cash", 150.0),
            fee_rate=p.get("fee_rate", 0.001),
            slippage_rate=p.get("slippage_rate", 0.0005),
            max_notional=p.get("max_notional", 80.0),
        )
        result = engine.run_triangular_candle_replay(
            histories, route=route, pair_symbols=pair_symbols,
            min_profit_pct=p.get("min_profit_pct", 0.05),
        )
        result.update({
            "data_source": "binance_spot_public_ohlcv_read_only",
            "candles_per_pair_requested": limit,
            "pair_histories_fetched": len(histories),
            "execution": "simulated_only", "no_live_orders": True,
        })
        return J(result, 200 if result.get("ok") else 422)
    except (TypeError, ValueError) as exc:
        db.rollback()
        return J({"ok": False, "error": "invalid_binance_triangle_backtest_parameters",
                  "detail": str(exc)[:200]}, 400)
    except Exception as exc:
        db.rollback()
        return J({"ok": False, "error": "binance_triangle_history_unavailable",
                  "detail": str(exc)[:200]}, 503)
    finally:
        db.close()


async def portfolio_backtest(request: Request):
    """Backtest concurrent positions across multiple isolated symbol capital sleeves."""
    db = db_sess()
    try:
        user, err = require_user(request, db)
        if err:
            return err
        p = await body(request)
        for key in ("initial_cash", "fee_rate", "slippage_rate", "leverage",
                    "max_leverage", "max_notional_per_position", "add_fraction", "add_trigger_pct", "max_adds"):
            if isinstance(p.get(key), bool):
                return J({"ok": False, "error": f"boolean_{key}_not_allowed"}, 400)
        market_type = str(p.get("market_type", "spot")).strip().lower()
        if market_type not in {"spot", "futures"}:
            return J({"ok": False, "error": "market_type_must_be_spot_or_futures"}, 400)
        symbols = p.get("symbols")
        if (not isinstance(symbols, list) or not 2 <= len(symbols) <= 10
                or any(not isinstance(item, str) for item in symbols)):
            return J({"ok": False, "error": "symbols_must_contain_two_to_ten_strings"}, 400)
        normalized = []
        for raw in symbols:
            symbol = raw.strip().upper().replace("/", "")
            if not symbol.isascii() or not symbol.isalnum() or not symbol.endswith("USDT") or len(symbol) > 30:
                return J({"ok": False, "error": "only_valid_usdt_symbols_are_supported"}, 400)
            if symbol in normalized:
                return J({"ok": False, "error": "duplicate_portfolio_symbol"}, 400)
            normalized.append(symbol)
        limit = p.get("limit", 240)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 60 <= limit <= 500:
            return J({"ok": False, "error": "candle_limit_must_be_60_to_500"}, 400)
        try:
            timeframe = Timeframe(str(p.get("timeframe", "1h")).lower())
        except ValueError:
            return J({"ok": False, "error": "unsupported_timeframe"}, 400)
        allowed = {"trend_following_ema", "momentum_macd_rsi", "mean_reversion_bbands",
                   "breakout_structure", "scalping_m5", "swing_h1_h4"}
        raw_strategy_keys = p.get("strategy_keys")
        if raw_strategy_keys is None:
            selected_strategy_keys = [str(p.get("strategy_key", "trend_following_ema")).strip().lower()]
        elif not isinstance(raw_strategy_keys, list):
            return J({"ok": False, "error": "strategy_keys_must_be_a_list"}, 400)
        else:
            selected_strategy_keys = [str(key).strip().lower() for key in raw_strategy_keys]
        if (not selected_strategy_keys or len(selected_strategy_keys) > len(allowed)
                or any(key not in allowed for key in selected_strategy_keys)
                or len(set(selected_strategy_keys)) != len(selected_strategy_keys)):
            return J({"ok": False, "error": "unsupported_or_duplicate_candle_strategies"}, 400)
        required_slots = len(normalized) * len(selected_strategy_keys)
        max_positions = p.get("max_positions", required_slots)
        if (isinstance(max_positions, bool) or not isinstance(max_positions, int)
                or not required_slots <= max_positions <= 60):
            return J({
                "ok": False, "error": "max_positions_must_cover_all_strategy_symbol_sleeves_and_not_exceed_60",
                "required_position_slots": required_slots,
            }, 400)
        histories = {}
        funding_by_symbol = {}
        for symbol in normalized:
            batch = core_service.market_data.get_public_candles(
                symbol, timeframe, limit=limit, market_type=market_type,
            )
            if (getattr(batch.source, "value", None) != "live_public"
                    or len(batch.df) < 60
                    or (market_type == "futures" and getattr(batch, "market_type", None) != "futures")):
                return J({"ok": False, "error": f"verified_public_history_required_for_{symbol}"}, 409)
            histories[symbol] = batch.df.rows
            if market_type == "futures":
                first = batch.df.rows[0].get("timestamp")
                last = batch.df.rows[-1].get("close_time") or batch.df.rows[-1].get("timestamp")
                if not isinstance(first, int) or not isinstance(last, int):
                    return J({"ok": False, "error": f"valid_futures_history_timestamps_required_for_{symbol}"}, 409)
                funding_by_symbol[symbol] = core_service.market_data.get_public_funding_rates(
                    symbol, start_time=first, end_time=last,
                )
        strategy_outcomes = p.get("strategy_outcomes")
        allocation_regime = str(p.get("allocation_regime", "unknown"))
        dynamic_enabled = p.get("dynamic_allocation_enabled", False)
        if not isinstance(dynamic_enabled, bool):
            return J({"ok": False, "error": "dynamic_allocation_enabled_must_be_boolean"}, 400)
        if dynamic_enabled:
            strategy_outcomes = {key: [] for key in selected_strategy_keys}
            outcome_rows = db.execute(select(LearningRecord).where(
                LearningRecord.category == "trade_outcome",
                LearningRecord.environment_mode == "demo",
            )).scalars().all()
            for row in outcome_rows:
                evidence = row.evidence if isinstance(row.evidence, dict) else {}
                if (evidence.get("source") != "binance_demo_confirmed_fills"
                        or evidence.get("research_only") is not True):
                    continue
                strategy_key = str(evidence.get("strategy_key") or "")
                regime = str(evidence.get("market_regime") or "unknown")
                if (strategy_key not in strategy_outcomes or regime != allocation_regime
                        or str(evidence.get("market_type") or "").lower() != market_type):
                    continue
                try:
                    pnl = float(evidence["net_pnl"])
                    notional = float(evidence["entry_price"]) * float(evidence["quantity_closed"])
                except (KeyError, TypeError, ValueError, OverflowError):
                    continue
                if math.isfinite(pnl) and math.isfinite(notional) and notional > 0:
                    strategy_outcomes[strategy_key].append({
                        "closed": True, "regime": regime, "net_return": pnl / notional,
                    })
        from app.engines.portfolio_backtest import PortfolioBacktestEngine
        per_position_cap = (
            min(float(p.get("max_notional_per_position", settings.max_futures_trade_notional)),
                float(settings.max_futures_trade_notional))
            if market_type == "futures"
            else min(float(p.get("max_notional_per_position", 80.0)), 80.0)
        )
        result = PortfolioBacktestEngine().run(
            histories, market_type=market_type, strategy_keys=selected_strategy_keys,
            timeframe=timeframe.value,
            initial_cash=p.get("initial_cash", 150.0),
            max_positions=max_positions,
            max_notional_per_position=per_position_cap,
            fee_rate=p.get("fee_rate"),
            slippage_rate=p.get("slippage_rate", 0.0005),
            leverage=p.get("leverage", 1.0),
            max_leverage=min(float(p.get("max_leverage", settings.max_leverage_cap)),
                             float(settings.max_leverage_cap)),
            funding_by_symbol=funding_by_symbol,
            pyramiding_enabled=p.get("pyramiding_enabled", False),
            max_adds=p.get("max_adds", 2),
            add_fraction=p.get("add_fraction", 0.25),
            add_trigger_pct=p.get("add_trigger_pct", 0.01),
            strategy_allocation_weights=p.get("strategy_allocation_weights"),
            strategy_outcomes=strategy_outcomes,
            allocation_regime=allocation_regime,
        )
        result.update({
            "data_source": "binance_usdm_futures_public_read_only" if market_type == "futures" else "binance_spot_public_read_only",
            "candles_per_symbol": limit,
            "execution": "simulated_only", "no_live_orders": True,
        })
        return J(result, 200 if result.get("ok") else 422)
    except (TypeError, ValueError) as exc:
        db.rollback()
        return J({"ok": False, "error": "invalid_portfolio_backtest_parameters",
                  "detail": str(exc)[:200]}, 400)
    except Exception as exc:
        db.rollback()
        return J({"ok": False, "error": "portfolio_backtest_failed", "detail": str(exc)[:200]}, 503)
    finally:
        db.close()


async def binance_orderflow_backtest(request: Request):
    """Fetch genuine historical Binance aggregate trades and replay causal taker-flow signals."""
    db = db_sess()
    try:
        user, err = require_user(request, db)
        if err:
            return err
        p = await body(request)
        for key in ("start_time", "end_time", "max_trades", "initial_cash", "fee_rate",
                    "slippage_rate", "max_notional", "rolling_window", "entry_imbalance",
                    "exit_imbalance"):
            if isinstance(p.get(key), bool):
                return J({"ok": False, "error": f"boolean_{key}_not_allowed"}, 400)
        symbol = str(p.get("symbol", "BTCUSDT")).strip().upper().replace("/", "")
        if not symbol.isascii() or not symbol.isalnum() or not 5 <= len(symbol) <= 30:
            return J({"ok": False, "error": "invalid_binance_symbol"}, 400)
        market_type = str(p.get("market_type", "spot")).strip().lower()
        if market_type not in {"spot", "futures"}:
            return J({"ok": False, "error": "market_type_must_be_spot_or_futures"}, 400)
        start_time = p.get("start_time")
        end_time = p.get("end_time")
        if (not isinstance(start_time, int) or not isinstance(end_time, int)
                or start_time <= 0 or end_time <= start_time
                or end_time - start_time > 7 * 24 * 60 * 60 * 1000):
            return J({"ok": False, "error": "start_end_time_must_be_milliseconds_within_seven_days"}, 400)
        max_trades = p.get("max_trades", 20000)
        if not isinstance(max_trades, int) or not 100 <= max_trades <= 50000:
            return J({"ok": False, "error": "max_trades_must_be_100_to_50000"}, 400)
        if market_type == "spot":
            from app.data.binance_public import BinancePublicData
            raw_trades = BinancePublicData().get_aggregate_trades_history(
                symbol, start_time=start_time, end_time=end_time, max_trades=max_trades,
            )
            trades = [dict(item, s=symbol) for item in raw_trades]
        else:
            from app.data.binance_futures_public import BinanceFuturesPublicData
            futures_data = BinanceFuturesPublicData()
            raw_trades = futures_data.get_aggregate_trades_history(
                symbol, start_time=start_time, end_time=end_time, max_trades=max_trades,
            )
            trades = [dict(item, s=symbol) for item in raw_trades]
            funding_events = futures_data.get_funding_rate_history(
                symbol, start_time=start_time, end_time=end_time, limit=1000,
            )
        from app.engines.specialized_backtest import SpecializedBacktestEngine
        max_notional_cap = 80.0 if market_type == "spot" else 800.0
        engine = SpecializedBacktestEngine(
            initial_cash=p.get("initial_cash", 150.0),
            fee_rate=p.get("fee_rate", 0.001),
            slippage_rate=p.get("slippage_rate", 0.0005),
            max_notional=min(float(p.get("max_notional", max_notional_cap)), max_notional_cap),
        )
        if market_type == "spot":
            result = engine.run_aggregate_trade_flow_replay(
                trades,
                rolling_window=p.get("rolling_window", 30),
                entry_imbalance=p.get("entry_imbalance", 0.15),
                exit_imbalance=p.get("exit_imbalance", 0.0),
            )
            data_source = "binance_spot_historical_aggregate_trades_public_read_only"
        else:
            result = engine.run_futures_aggregate_trade_flow_replay(
                trades, funding_events=funding_events,
                rolling_window=p.get("rolling_window", 30),
                entry_imbalance=p.get("entry_imbalance", 0.15),
                exit_imbalance=p.get("exit_imbalance", 0.0),
                leverage=p.get("leverage", 2.0),
                max_leverage=p.get("max_leverage", 10.0),
                notional=min(float(p.get("max_notional", 800.0)), 800.0),
            )
            data_source = "binance_usdm_futures_historical_aggregate_trades_public_read_only"
        result.update({
            "data_source": data_source,
            "requested_start_time": start_time,
            "requested_end_time": end_time,
            "records_fetched": len(trades),
            "max_trades": max_trades,
            "historical_order_book_available": False,
            "funding_records_fetched": len(funding_events) if market_type == "futures" else 0,
            "execution": "simulated_only",
            "no_live_orders": True,
        })
        return J(result, 200 if result.get("ok") else 422)
    except (TypeError, ValueError) as exc:
        db.rollback()
        return J({"ok": False, "error": "invalid_binance_orderflow_backtest_parameters",
                  "detail": str(exc)[:200]}, 400)
    except Exception as exc:
        db.rollback()
        return J({"ok": False, "error": "binance_historical_orderflow_unavailable",
                  "detail": str(exc)[:200]}, 503)
    finally:
        db.close()


async def specialized_backtest(request: Request):
    """Authenticated offline replay for order-flow and triangular-arbitrage history."""
    db = db_sess()
    try:
        user, err = require_user(request, db)
        if err:
            return err
        p = await body(request)
        strategy_key = str(p.get("strategy_key", "")).strip().lower()
        from app.engines.specialized_backtest import SpecializedBacktestEngine
        engine = SpecializedBacktestEngine(
            initial_cash=p.get("initial_cash", 150.0),
            fee_rate=p.get("fee_rate", 0.001),
            slippage_rate=p.get("slippage_rate", 0.0005),
            max_notional=p.get("max_notional", 80.0),
        )
        if strategy_key == "liquidity_orderflow":
            events = p.get("events")
            signals = p.get("signals")
            if (not isinstance(events, list) or not isinstance(signals, list)
                    or not 2 <= len(events) <= 5000 or len(signals) > 5000):
                return J({"ok": False, "error": "orderflow_history_and_signals_must_be_bounded_lists"}, 400)
            if p.get("data_source") != "user_supplied_historical":
                return J({"ok": False, "error": "verified_historical_orderflow_dataset_required"}, 400)
            result = engine.run_orderflow_replay(events, signals)
            result["data_source"] = "user_supplied_historical_orderflow"
        elif strategy_key == "arbitrage":
            snapshots = p.get("snapshots")
            route = p.get("route")
            if not isinstance(snapshots, list) or not 1 <= len(snapshots) <= 5000:
                return J({"ok": False, "error": "arbitrage_snapshots_must_be_a_bounded_list"}, 400)
            result = engine.run_triangular_arbitrage_replay(
                snapshots, route=route,
                min_profit_pct=p.get("min_profit_pct", 0.05),
            )
            result["data_source"] = "user_supplied_synchronized_public_quote_history"
        else:
            return J({"ok": False, "error": "specialized_strategy_must_be_orderflow_or_arbitrage"}, 400)
        result["initial_cash"] = engine.initial_cash
        result["fee_rate"] = engine.fee_rate
        result["slippage_rate"] = engine.slippage_rate
        result["max_notional"] = engine.max_notional
        result["execution"] = "simulated_only"
        result["no_live_orders"] = True
        return J(result, 200 if result.get("ok") else 422)
    except (TypeError, ValueError) as exc:
        db.rollback()
        return J({"ok": False, "error": "invalid_specialized_backtest_parameters", "detail": str(exc)[:200]}, 400)
    except Exception as exc:
        db.rollback()
        return J({"ok": False, "error": "specialized_backtest_failed", "detail": str(exc)[:200]}, 503)
    finally:
        db.close()


async def news_analysis(request: Request):
    db = db_sess()
    try:
        user, err = require_user(request, db)
        if err: return err
        keyword = request.query_params.get("keyword")
        limit = int(request.query_params.get("limit", "50"))
        from app.engines.news import NewsAnalysisEngine
        result = NewsAnalysisEngine().fetch_and_analyze(keyword=keyword, limit=limit)
        return J(result, 200 if result.get("ok") else 503)
    except (TypeError, ValueError) as exc:
        return J({"ok": False, "error": "invalid_news_query", "detail": str(exc)}, 400)
    finally:
        db.close()


async def fundamental_analysis(request: Request):
    db = db_sess()
    try:
        user, err = require_user(request, db)
        if err: return err
        coin_id = str(request.query_params.get("coin_id", "")).strip().lower()
        if not coin_id:
            return J({"ok": False, "error": "coin_id_required"}, 400)
        from app.engines.fundamental import FundamentalAnalysisEngine
        result = FundamentalAnalysisEngine().fetch_and_analyze(coin_id)
        return J(result, 200 if result.get("ok") else 503)
    finally:
        db.close()


async def token_unlocks(request: Request):
    db = db_sess()
    try:
        user, err = require_user(request, db)
        if err: return err
        from datetime import date, timedelta
        from app.data.tokenomist_public import TokenomistPublicData
        token_id = request.query_params.get("token_id", "").strip().lower()
        if not token_id:
            return J({"ok": False, "error": "token_id_required"}, 400)
        try:
            start_date = date.fromisoformat(request.query_params.get("from", date.today().isoformat()))
            end_date = date.fromisoformat(request.query_params.get("to", (date.today() + timedelta(days=90)).isoformat()))
            result = TokenomistPublicData().get_unlock_events(
                token_id, start=start_date, end=end_date,
            )
        except (TypeError, ValueError) as exc:
            return J({"ok": False, "error": str(exc)}, 400)
        return J(result, 200 if result.get("ok") else 503)
    except Exception as exc:
        return J({"ok": False, "error": "token_unlock_data_unavailable", "detail": str(exc)[:200]}, 503)
    finally:
        db.close()


async def economic_calendar(request: Request):
    db = db_sess()
    try:
        user, err = require_user(request, db)
        if err: return err
        from datetime import date, timedelta
        from app.data.economic_calendar_public import EconomicCalendarPublicData
        try:
            start_date = date.fromisoformat(request.query_params.get("from", date.today().isoformat()))
            end_date = date.fromisoformat(request.query_params.get("to", (date.today() + timedelta(days=30)).isoformat()))
            importance = request.query_params.get("importance", "high")
        except (TypeError, ValueError):
            return J({"ok": False, "error": "invalid_economic_calendar_date_parameters"}, 400)
        result = EconomicCalendarPublicData().fetch(
            start_date=start_date, end_date=end_date, importance=importance,
        )
        return J(result)
    except ValueError as exc:
        return J({"ok": False, "error": str(exc)}, 400)
    except Exception as exc:
        return J({"ok": False, "error": "economic_calendar_unavailable", "detail": str(exc)[:200]}, 503)
    finally:
        db.close()


async def protocol_fundamental_analysis(request: Request):
    db = db_sess()
    try:
        user, err = require_user(request, db)
        if err: return err
        slug = str(request.query_params.get("slug", "")).strip().lower()
        if not slug:
            return J({"ok": False, "error": "protocol_slug_required"}, 400)
        from app.engines.fundamental import FundamentalAnalysisEngine
        result = FundamentalAnalysisEngine().fetch_protocol_and_analyze(slug)
        return J(result, 200 if result.get("ok") else 503)
    finally:
        db.close()


async def market_microstructure(request: Request):
    db = db_sess()
    try:
        user, err = require_user(request, db)
        if err: return err
        symbol = str(request.query_params.get("symbol", "BTC/USDT")).strip().upper().replace("-", "/")
        market_type = str(request.query_params.get("market_type", "spot")).strip().lower()
        if market_type not in {"spot", "futures"}:
            return J({"ok": False, "error": "unsupported_public_market_type"}, 400)
        if not symbol.endswith("/USDT") or len(symbol) > 32:
            return J({"ok": False, "error": "usdt_symbol_required"}, 400)
        from app.engines.liquidity import LiquidityAnalysisEngine
        from app.engines.order_flow import OrderFlowEngine
        liquidity = LiquidityAnalysisEngine().fetch_and_analyze(symbol, limit=100, market_type=market_type)
        order_flow = OrderFlowEngine().fetch_and_analyze(symbol, limit=500, market_type=market_type)
        ok = bool(liquidity.get("ok") or order_flow.get("ok"))
        source = "binance_futures_public_read_only" if market_type == "futures" else "binance_public_read_only"
        return J({
            "ok": ok,
            "symbol": symbol,
            "market_type": market_type,
            "source": source,
            "liquidity": liquidity,
            "order_flow": order_flow,
            "execution": "none",
            "note": "Public depth and recent aggregate trades only; no authenticated account access and no exchange orders.",
        }, 200 if ok else 503)
    finally:
        db.close()


async def arbitrage_analysis(request: Request):
    db = db_sess()
    try:
        user, err = require_user(request, db)
        if err: return err
        p = await body(request)
        route_assets = p.get("route")
        if not isinstance(route_assets, list) or len(route_assets) != 4:
            return J({"ok": False, "error": "triangle_route_must_contain_four_assets"}, 400)
        numeric = ("starting_amount", "fee_rate", "slippage_rate", "min_profit_pct")
        if any(isinstance(p.get(key), bool) for key in numeric if key in p):
            return J({"ok": False, "error": "boolean_arbitrage_configuration_not_allowed"}, 400)
        from app.engines.arbitrage import ArbitrageAnalysisEngine
        result = ArbitrageAnalysisEngine(core_service.market_data).fetch_and_analyze(
            route_assets,
            starting_amount=p.get("starting_amount", 1000.0),
            fee_rate=p.get("fee_rate", 0.001),
            slippage_rate=p.get("slippage_rate", 0.0005),
            min_profit_pct=p.get("min_profit_pct", 0.05),
        )
        return J({
            **result,
            "execution": "none",
            "data_access": "binance_public_read_only",
            "note": "Triangular Spot arbitrage is a theoretical top-of-book estimate. No orders are placed; liquidity reservation, exchange filters, latency and partial fills are not guaranteed.",
        }, 200 if result.get("ok") else 422)
    except (TypeError, ValueError) as exc:
        return J({"ok": False, "error": "invalid_arbitrage_parameters", "detail": str(exc)[:200]}, 400)
    finally:
        db.close()


async def ml_train(request: Request):
    db = db_sess()
    try:
        user, err = require_user(request, db)
        if err: return err
        p = await body(request)
        symbol = str(p.get("symbol", "BTC/USDT")).strip().upper().replace("-", "/")
        if not symbol.endswith("/USDT") or len(symbol) > 32:
            return J({"ok": False, "error": "usdt_spot_symbol_required"}, 400)
        timeframe_value = str(p.get("timeframe", "1h")).lower()
        try:
            timeframe, batch = _ml_timeframe_batch(symbol, timeframe_value)
        except (ValueError, KeyError) as exc:
            return J({"ok": False, "error": str(exc)}, 409)
        from app.engines.hybrid.ml_engine import ml_engine
        model_key = f"{symbol}|{timeframe.value}"
        result = ml_engine.train(
            batch.df.rows,
            lookahead=int(p.get("lookahead", 1)),
            train_ratio=float(p.get("train_ratio", 0.8)),
            epochs=int(p.get("epochs", 400)),
            model_key=model_key,
        )
        if not result.get("ok"):
            return J(result, 422)
        record = LearningRecord(
            category="ml_model",
            subject=model_key,
            outcome="trained",
            lesson="Chronological train/test split using Binance Public read-only historical candles.",
            evidence={"model": result["model"], "metrics": result["metrics"], "data_source": "binance_public_read_only"},
            verified=False,
        )
        db.add(record)
        db.commit()
        db.refresh(record)
        return J({
            "ok": True, "record_id": record.id, "symbol": symbol,
            "timeframe": timeframe.value, "model_key": model_key,
            "metrics": result["metrics"], "model_version": result["model"]["model_version"],
            "training_samples": result["model"]["training_samples"],
            "test_samples": result["model"]["test_samples"],
            "data_source": "binance_public_read_only",
            "execution": "none",
            "note": result["note"] + " Model snapshot is stored in the local learning database.",
        })
    except (TypeError, ValueError) as exc:
        db.rollback()
        return J({"ok": False, "error": "invalid_ml_training_parameters", "detail": str(exc)}, 400)
    except Exception as exc:
        db.rollback()
        return J({"ok": False, "error": "ml_training_failed", "detail": str(exc)[:240]}, 503)
    finally:
        db.close()


async def ml_predict(request: Request):
    db = db_sess()
    try:
        user, err = require_user(request, db)
        if err: return err
        p = await body(request)
        symbol = str(p.get("symbol", "BTC/USDT")).strip().upper().replace("-", "/")
        if not symbol.endswith("/USDT") or len(symbol) > 32:
            return J({"ok": False, "error": "usdt_spot_symbol_required"}, 400)
        timeframe_value = str(p.get("timeframe", "1h")).lower()
        try:
            timeframe, batch = _ml_timeframe_batch(symbol, timeframe_value)
        except (ValueError, KeyError) as exc:
            return J({"ok": False, "error": str(exc)}, 409)
        from app.engines.hybrid.ml_engine import ml_engine
        model_key = f"{symbol}|{timeframe.value}"
        if ml_engine.export_model(model_key) is None:
            record = db.execute(
                select(LearningRecord).where(
                    LearningRecord.category == "ml_model",
                    LearningRecord.subject == model_key,
                ).order_by(desc(LearningRecord.id)).limit(1)
            ).scalars().first()
            model = (record.evidence or {}).get("model") if record else None
            if not model:
                return J({"ok": False, "error": "trained_model_not_found", "model_key": model_key}, 404)
            loaded = ml_engine.load_model(model)
            if not loaded.get("ok"):
                return J(loaded, 409)
        prediction = ml_engine.predict(batch.df.rows, model_key=model_key)
        return J({
            **prediction, "symbol": symbol, "timeframe": timeframe.value,
            "data_source": "binance_public_read_only",
        })
    except Exception as exc:
        db.rollback()
        return J({"ok": False, "error": "ml_prediction_failed", "detail": str(exc)[:240]}, 503)
    finally:
        db.close()


async def ml_drift(request: Request):
    db = db_sess()
    try:
        user, err = require_user(request, db)
        if err: return err
        p = await body(request)
        symbol = str(p.get("symbol", "BTC/USDT")).strip().upper().replace("-", "/")
        if not symbol.endswith("/USDT") or len(symbol) > 32:
            return J({"ok": False, "error": "usdt_spot_symbol_required"}, 400)
        timeframe_value = str(p.get("timeframe", "1h")).lower()
        try:
            timeframe, batch = _ml_drift_timeframe_batch(symbol, timeframe_value)
        except (ValueError, KeyError) as exc:
            return J({"ok": False, "error": str(exc)}, 409)
        from app.engines.hybrid.ml_engine import ml_engine
        model_key = f"{symbol}|{timeframe.value}"
        if ml_engine.export_model(model_key) is None:
            record = db.execute(
                select(LearningRecord).where(
                    LearningRecord.category == "ml_model",
                    LearningRecord.subject == model_key,
                ).order_by(desc(LearningRecord.id)).limit(1)
            ).scalars().first()
            model = (record.evidence or {}).get("model") if record else None
            if not model:
                return J({"ok": False, "error": "trained_model_not_found", "model_key": model_key}, 404)
            loaded = ml_engine.load_model(model)
            if not loaded.get("ok"):
                return J(loaded, 409)
        result = ml_engine.evaluate_drift(
            batch.df.rows,
            model_key=model_key,
            brier_increase_threshold=p.get("brier_increase_threshold", 0.05),
            accuracy_drop_threshold=p.get("accuracy_drop_threshold", 0.10),
        )
        if not result.get("ok"):
            return J(result, 409)
        record = LearningRecord(
            category="ml_drift_evaluation",
            subject=model_key,
            outcome=result["status"],
            lesson="Post-training out-of-sample model drift and calibration evaluation; no model changes.",
            evidence={**result, "data_source": "binance_public_read_only", "timeframe": timeframe.value},
            verified=False,
        )
        db.add(record)
        db.commit()
        db.refresh(record)
        return J({
            **result, "record_id": record.id, "symbol": symbol,
            "timeframe": timeframe.value, "data_source": "binance_public_read_only",
        })
    except Exception as exc:
        db.rollback()
        return J({"ok": False, "error": "ml_drift_evaluation_failed", "detail": str(exc)[:240]}, 503)
    finally:
        db.close()


async def ml_models(request: Request):
    db = db_sess()
    try:
        user, err = require_user(request, db)
        if err: return err
        rows = db.execute(
            select(LearningRecord).where(LearningRecord.category == "ml_model")
            .order_by(desc(LearningRecord.id)).limit(100)
        ).scalars().all()
        latest = {}
        for row in rows:
            latest.setdefault(row.subject, {
                "record_id": row.id,
                "model_key": row.subject,
                "outcome": row.outcome,
                "created_at": row.created_at,
                "metrics": (row.evidence or {}).get("metrics", {}),
            })
        return J({"items": list(latest.values()), "count": len(latest), "persistence": "local_learning_records"})
    finally:
        db.close()


async def strategies(request: Request):
    return J({"strategies": core_service.strategies(), "note": "Registry is extensible; statuses reflect current implementation/integration state."})


async def analyze(request: Request):
    db = db_sess()
    try:
        user, err = require_user(request, db)
        if err: return err
        p = await body(request)
        return J(core_service.analyze(db, symbol=p["symbol"], market_type=p.get("market_type", "spot"), environment_mode=p.get("environment_mode", "demo"),
                                      balance=float(p.get("balance", 1000)), max_leverage=float(p.get("max_leverage", 3)), risk_per_trade=float(p.get("risk_per_trade", 0.01)),
                                      data_source=p.get("data_source", "synthetic")))
    except Exception as e:
        return J({"detail": str(e)}, 400)
    finally:
        db.close()

async def list_decisions(request: Request):
    db = db_sess()
    try:
        user, err = require_user(request, db)
        if err:
            return err
        limit = min(max(int(request.query_params.get("limit", 50)), 1), 200)
        rows = db.execute(select(DecisionRecord).order_by(desc(DecisionRecord.id)).limit(limit)).scalars().all()
        return J({"items": [{"id": r.id, "created_at": r.created_at.isoformat(), "symbol": r.symbol, "action": r.action, "strategy": r.strategy,
                             "confidence": r.confidence, "environment_mode": r.environment_mode, "market_type": r.market_type,
                             "data_source": r.data_source, "reason": r.rationale} for r in rows]})
    finally:
        db.close()


async def list_audit(request: Request):
    db = db_sess()
    try:
        user, err = require_user(request, db)
        if err:
            return err
        limit = min(max(int(request.query_params.get("limit", 100)), 1), 500)
        rows = db.execute(select(AuditLog).order_by(desc(AuditLog.id)).limit(limit)).scalars().all()
        return J({"items": [{"id": r.id, "created_at": r.created_at.isoformat(), "action": r.action, "severity": r.severity,
                             "message": r.message, "environment_mode": r.environment_mode, "market_type": r.market_type, "details": r.details} for r in rows]})
    finally:
        db.close()


async def knowledge_list(request: Request):
    db = db_sess()
    try:
        user, err = require_user(request, db)
        if err:
            return err
        rows = db.execute(select(KnowledgeSource).order_by(desc(KnowledgeSource.id)).limit(200)).scalars().all()
        return J({"items": [{
            "id": r.id, "title": r.title, "source_type": r.source_type, "url": r.url,
            "reliability_score": r.reliability_score, "verified": r.verified,
            "notes": r.notes, "source_verification": (r.metadata_json or {}).get("source_verification"),
        } for r in rows]})
    finally:
        db.close()


async def knowledge_add(request: Request):
    db = db_sess()
    try:
        user, err = require_user(request, db)
        if err: return err
        p = await body(request)
        from app.knowledge.validation import validate_knowledge_source
        validated = validate_knowledge_source(p)
        if not validated.get("ok"):
            return J(validated, 400)
        r = KnowledgeSource(
            title=validated["title"], source_type=validated["source_type"],
            url=validated["url"], reliability_score=validated["reliability_score"],
            verified=False, notes=validated["notes"], metadata_json=validated["metadata"],
        )
        db.add(r); db.commit(); db.refresh(r)
        return J({"id": r.id, "title": r.title, "verified": False, "verification_status": "unverified"})
    finally:
        db.close()


async def knowledge_verify(request: Request):
    db = db_sess()
    try:
        user, err = require_user(request, db)
        if err: return err
        p = await body(request)
        raw_id = p.get("source_id")
        if (isinstance(raw_id, bool)
                or not (
                    isinstance(raw_id, int)
                    or (isinstance(raw_id, str) and raw_id.strip().isdigit())
                )):
            return J({"ok": False, "error": "valid_knowledge_source_id_required"}, 400)
        source_id = int(raw_id)
        source = db.get(KnowledgeSource, source_id)
        if source is None:
            return J({"ok": False, "error": "knowledge_source_not_found"}, 404)
        if not source.url:
            return J({"ok": False, "error": "knowledge_source_url_required"}, 400)
        claim = p.get("claim")
        if claim is None:
            claim = source.notes
        if not isinstance(claim, str) or len(claim) > 2000:
            return J({"ok": False, "error": "claim_must_be_text_under_2000_chars"}, 400)
        from app.knowledge.verification import KnowledgeSourceVerifier
        result = KnowledgeSourceVerifier().verify(source.url, claim=claim or None)
        metadata = dict(source.metadata_json or {})
        metadata["source_verification"] = result
        source.metadata_json = metadata
        # A reachable source and text overlap do not certify the factual claim.
        source.verified = False
        db.add(source)
        db.commit()
        return J({
            **result,
            "source_id": source.id,
            "knowledge_record_verified": False,
        })
    except Exception as exc:
        db.rollback()
        return J({"ok": False, "error": "knowledge_source_verification_failed", "detail": str(exc)[:200]}, 503)
    finally:
        db.close()


async def knowledge_concepts(request: Request):
    from app.knowledge.base import categories, list_concepts
    return J({"categories": categories(), "items": list_concepts(category=request.query_params.get("category"), query=request.query_params.get("q"))})


async def learning_list(request: Request):
    db = db_sess()
    try:
        user, err = require_user(request, db)
        if err:
            return err
        rows = db.execute(select(LearningRecord).order_by(desc(LearningRecord.id)).limit(200)).scalars().all()
        return J({"items": [{"id": r.id, "category": r.category, "subject": r.subject, "outcome": r.outcome, "lesson": r.lesson, "verified": r.verified, "environment_mode": r.environment_mode, "market_type": r.market_type} for r in rows]})
    finally:
        db.close()


async def strategy_trade_outcomes(request: Request):
    """Authenticated strategy-level metrics from confirmed Binance Demo fills only."""
    db = db_sess()
    try:
        user, err = require_user(request, db)
        if err:
            return err
        key = str(request.path_params.get("strategy_key", "")).strip()
        if not key:
            return J({"ok": False, "error": "strategy_key_required"}, 400)
        from app.learning.outcomes import summarize_strategy_outcomes
        result = summarize_strategy_outcomes(db, key)
        return J(result, 200 if result.get("ok") else 400)
    except Exception as exc:
        return J({"ok": False, "error": "strategy_outcome_analytics_failed", "detail": str(exc)[:160]}, 503)
    finally:
        db.close()


async def learning_add(request: Request):
    db = db_sess()
    try:
        user, err = require_user(request, db)
        if err: return err
        p = await body(request)
        r = LearningRecord(category=p.get("category",""), subject=p.get("subject",""), lesson=p.get("lesson",""), outcome=p.get("outcome"), environment_mode=p.get("environment_mode"), market_type=p.get("market_type"), verified=False)
        db.add(r); db.commit(); db.refresh(r)
        return J({"id": r.id, "subject": r.subject})
    finally:
        db.close()


async def strategy_versions(request: Request):
    db = db_sess()
    try:
        user, err = require_user(request, db)
        if err: return err
        key = str(request.query_params.get("strategy_key", "")).strip()
        if not key: return J({"ok": False, "error": "strategy_key_required"}, 400)
        from app.learning.persistent import PersistentStrategyLearning
        service = PersistentStrategyLearning()
        return J({"items": service.list_versions(db, key), "active": service.get_active_version(db, key)})
    finally:
        db.close()


async def strategy_version_create(request: Request):
    db = db_sess()
    try:
        user, err = require_user(request, db)
        if err: return err
        p = await body(request)
        from app.learning.persistent import PersistentStrategyLearning
        result = PersistentStrategyLearning().create_version(
            db, p.get("strategy_key", ""), p.get("params", {}), p.get("metrics", {}),
            make_active=p.get("make_active") is True,
            explicit_approval=p.get("explicit_approval") is True,
        )
        return J(result, 200 if result.get("ok") else 400)
    finally:
        db.close()


async def strategy_version_rollback(request: Request):
    db = db_sess()
    try:
        user, err = require_user(request, db)
        if err: return err
        p = await body(request)
        from app.learning.persistent import PersistentStrategyLearning
        result = PersistentStrategyLearning().rollback(
            db, str(p.get("strategy_key", "")), int(p.get("version", 0)),
            explicit_approval=p.get("explicit_approval") is True,
        )
        return J(result, 200 if result.get("ok") else 400)
    except (TypeError, ValueError) as exc:
        db.rollback()
        return J({"ok": False, "error": "invalid_version_rollback", "detail": str(exc)}, 400)
    finally:
        db.close()


async def strategy_proposal_create(request: Request):
    db = db_sess()
    try:
        user, err = require_user(request, db)
        if err: return err
        p = await body(request)
        from app.learning.persistent import PersistentStrategyLearning
        from app.engines.backtest import normalize_strategy_params
        strategy_key = str(p.get("strategy_key", "")).strip()
        current = normalize_strategy_params(strategy_key, p.get("current_params", {}))
        proposed = normalize_strategy_params(strategy_key, p.get("proposed_params", {}))
        if not current.get("ok") or not proposed.get("ok"):
            return J({"ok": False, "error": "invalid_strategy_parameter_set",
                      "current_error": current.get("error"), "proposed_error": proposed.get("error")}, 400)
        if current["params"] == proposed["params"]:
            return J({"ok": False, "error": "proposed_parameters_must_differ"}, 400)
        result = PersistentStrategyLearning().create_proposal(
            db, strategy_key, current["params"], proposed["params"],
            str(p.get("rationale", "")), p.get("metrics_before", {}),
        )
        return J(result, 200 if result.get("ok") else 400)
    finally:
        db.close()


async def strategy_proposal_test(request: Request):
    db = db_sess()
    try:
        user, err = require_user(request, db)
        if err: return err
        p = await body(request)
        from app.learning.persistent import PersistentStrategyLearning
        result = PersistentStrategyLearning().test_proposal(
            db, int(p.get("proposal_id", 0)), p.get("metrics_after_paper", {}),
        )
        return J(result, 200 if result.get("ok") else 400)
    except (TypeError, ValueError) as exc:
        db.rollback()
        return J({"ok": False, "error": "invalid_proposal_test", "detail": str(exc)}, 400)
    finally:
        db.close()


async def strategy_proposal_walk_forward(request: Request):
    """Reject client-authored fold metrics; only server-run historical replay can validate proposals."""
    db = db_sess()
    try:
        user, err = require_user(request, db)
        if err: return err
        return J({
            "ok": False,
            "error": "historical_walk_forward_required",
            "endpoint": "/api/learning/strategy-proposals/historical-walk-forward",
            "manual_fold_metrics_accepted": False,
        }, 410)
    finally:
        db.close()


async def strategy_proposal_historical_walk_forward(request: Request):
    """Compare stored baseline/candidate parameters on the same real Spot public-candle history."""
    db = db_sess()
    try:
        user, err = require_user(request, db)
        if err: return err
        p = await body(request)
        proposal_id = p.get("proposal_id")
        if isinstance(proposal_id, bool) or not isinstance(proposal_id, int) or proposal_id <= 0:
            return J({"ok": False, "error": "valid_proposal_id_required"}, 400)
        proposal = db.get(LearningRecord, proposal_id)
        if proposal is None or proposal.category != "strategy_proposal":
            return J({"ok": False, "error": "proposal_not_found"}, 404)
        evidence = proposal.evidence if isinstance(proposal.evidence, dict) else {}
        current_params = evidence.get("current_params")
        proposed_params = evidence.get("proposed_params")
        if not isinstance(current_params, dict) or not isinstance(proposed_params, dict):
            return J({"ok": False, "error": "proposal_parameters_missing"}, 400)
        if current_params == proposed_params:
            return J({"ok": False, "error": "proposed_parameters_must_differ"}, 400)

        symbol = str(p.get("symbol", "BTC/USDT")).strip().upper().replace("-", "/")
        if not symbol.endswith("/USDT") or len(symbol) > 32:
            return J({"ok": False, "error": "usdt_spot_symbol_required"}, 400)
        timeframe_value = str(p.get("timeframe", "1h")).lower()
        try:
            timeframe = Timeframe(timeframe_value)
        except ValueError:
            return J({"ok": False, "error": "unsupported_timeframe"}, 400)
        limit = p.get("limit", 500)
        train_window = p.get("train_window", 150)
        test_window = p.get("test_window", 50)
        step = p.get("step", test_window)
        for name, value, minimum, maximum in (
            ("limit", limit, 120, 500),
            ("train_window", train_window, 50, 400),
            ("test_window", test_window, 30, 200),
            ("step", step, 30, 200),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
                return J({"ok": False, "error": f"invalid_{name}"}, 400)
        if step < test_window:
            return J({"ok": False, "error": "walk_forward_step_must_not_overlap_test_windows"}, 400)
        if train_window + test_window + 2 * step > limit:
            return J({"ok": False, "error": "historical_limit_and_step_must_cover_at_least_three_oos_folds"}, 400)

        batch = core_service.market_data.get_public_candles(
            symbol, timeframe, limit=limit, market_type="spot",
        )
        if getattr(batch.source, "value", None) != "live_public" or len(batch.df) < train_window + test_window * 3:
            return J({"ok": False, "error": "verified_public_history_with_three_oos_folds_required"}, 409)
        rows = batch.df.rows
        stamp = rows[-1].get("close_time") or rows[-1].get("timestamp")
        if isinstance(stamp, bool) or not isinstance(stamp, (int, float)):
            return J({"ok": False, "error": "historical_candle_timestamp_required"}, 409)
        duration = {
            Timeframe.D1: 86400, Timeframe.H4: 14400, Timeframe.H1: 3600,
            Timeframe.M15: 900, Timeframe.M5: 300,
        }[timeframe]
        age = __import__("time").time() - float(stamp) / 1000.0
        if age < -60 or age > max(300, duration * 2):
            return J({"ok": False, "error": "stale_historical_candles"}, 409)

        from app.engines.backtest import BacktestEngine
        engine = BacktestEngine(initial_cash=150.0, fee_rate=0.001, slippage_rate=0.0005)
        strategy_key = str(proposal.subject or "")
        baseline = engine.run_strategy_walk_forward(
            rows, strategy_key=strategy_key, timeframe=timeframe,
            train_window=train_window, test_window=test_window, step=step,
            strategy_params=current_params,
        )
        candidate = engine.run_strategy_walk_forward(
            rows, strategy_key=strategy_key, timeframe=timeframe,
            train_window=train_window, test_window=test_window, step=step,
            strategy_params=proposed_params,
        )
        if baseline.get("ok") is not True or candidate.get("ok") is not True:
            return J({
                "ok": False, "error": "historical_parameter_replay_failed",
                "baseline_error": baseline.get("error"), "candidate_error": candidate.get("error"),
            }, 422)
        if (baseline.get("no_lookahead") is not True or candidate.get("no_lookahead") is not True
                or len(baseline.get("folds", [])) != len(candidate.get("folds", []))
                or len(baseline.get("folds", [])) < 3):
            return J({"ok": False, "error": "historical_walk_forward_evidence_incomplete"}, 422)

        def fold_metrics(result):
            return [{
                "expectancy": fold["result"]["expectancy"],
                "max_drawdown": fold["result"]["max_drawdown"],
                "sample_count": fold["testing_candles"],
            } for fold in result["folds"]]

        from app.learning.persistent import PersistentStrategyLearning
        validation = PersistentStrategyLearning().validate_walk_forward(
            db, proposal_id, fold_metrics(baseline), fold_metrics(candidate),
            min_folds=3, min_samples_per_fold=30,
        )
        if not validation.get("ok"):
            return J(validation, 400)
        proposal = db.get(LearningRecord, proposal_id)
        evidence = dict(proposal.evidence or {})
        evidence["metrics_before"] = {
            "expectancy": baseline.get("expectancy", 0.0),
            "max_drawdown": baseline.get("max_fold_drawdown", 0.0),
        }
        walk = dict(evidence.get("walk_forward") or {})
        walk.update({
            "source": "binance_spot_public_read_only",
            "symbol": symbol, "timeframe": timeframe.value,
            "candles": len(rows), "train_window": train_window,
            "test_window": test_window, "step": step,
            "sample_count_unit": "closed_test_candles",
            "fee_rate": engine.fee_rate, "slippage_rate": engine.slippage_rate,
            "baseline_total_trades": baseline.get("total_trades", 0),
            "candidate_total_trades": candidate.get("total_trades", 0),
            "baseline_oos_return": baseline.get("compounded_oos_return"),
            "candidate_oos_return": candidate.get("compounded_oos_return"),
            "baseline_expectancy": baseline.get("expectancy"),
            "candidate_expectancy": candidate.get("expectancy"),
            "no_lookahead": True, "research_only": True,
        })
        evidence["walk_forward"] = walk
        proposal.evidence = evidence
        db.add(proposal)
        db.commit()
        return J({
            **validation,
            "historical_validation": walk,
            "baseline": {
                "total_trades": baseline.get("total_trades"),
                "expectancy": baseline.get("expectancy"),
                "compounded_oos_return": baseline.get("compounded_oos_return"),
                "max_fold_drawdown": baseline.get("max_fold_drawdown"),
            },
            "candidate": {
                "total_trades": candidate.get("total_trades"),
                "expectancy": candidate.get("expectancy"),
                "compounded_oos_return": candidate.get("compounded_oos_return"),
                "max_fold_drawdown": candidate.get("max_fold_drawdown"),
            },
            "data_source": "binance_spot_public_read_only",
            "execution": "simulated_only", "no_live_orders": True,
        }, 200 if validation.get("ok") else 400)
    except (TypeError, ValueError) as exc:
        db.rollback()
        return J({"ok": False, "error": "invalid_historical_walk_forward", "detail": str(exc)[:160]}, 400)
    except Exception as exc:
        db.rollback()
        return J({"ok": False, "error": "historical_walk_forward_failed", "detail": str(exc)[:200]}, 503)
    finally:
        db.close()


async def strategy_proposal_apply_paper(request: Request):
    db = db_sess()
    try:
        user, err = require_user(request, db)
        if err: return err
        p = await body(request)
        from app.learning.persistent import PersistentStrategyLearning
        result = PersistentStrategyLearning().apply_proposal_to_paper(
            db, int(p.get("proposal_id", 0)),
            explicit_approval=p.get("explicit_approval") is True,
        )
        return J(result, 200 if result.get("ok") else 400)
    except (TypeError, ValueError) as exc:
        db.rollback()
        return J({"ok": False, "error": "invalid_proposal_apply", "detail": str(exc)}, 400)
    finally:
        db.close()


async def auth_status(request: Request):
    db = db_sess()
    try:
        from app.models.entities import AppUser
        has_user = db.execute(select(AppUser.id).limit(1)).first() is not None
        return J({"auth_required": auth_service.auth_required(), "allowlist_configured": bool(auth_service.get_allowed_emails()) or bool(settings.allowed_emails), "has_user": has_user,
                  "note": "Set initial password locally with backend script. Never send passwords in chat."})
    finally:
        db.close()


async def auth_login(request: Request):
    db = db_sess()
    try:
        p = await body(request)
        client_ip = request.client.host if request.client else None
        email = p.get("email", "")
        retry_after = auth_service.login_rate_limit_remaining(client_ip, email)
        if retry_after:
            return JSONResponse(
                {"detail": "Too many login attempts. Try again later."},
                status_code=429,
                headers={"Retry-After": str(retry_after)},
            )
        user = auth_service.authenticate(db, email, p.get("password", ""))
        if not user:
            auth_service.record_login_attempt(client_ip, email, success=False)
            db.commit()
            return J({"detail": "Invalid credentials, email not allowed, or account locked"}, 401)
        if user.two_factor_enabled and not auth_service.verify_totp(
            user.two_factor_secret or "", p.get("totp_code", "")
        ):
            log_event(db, action="auth.2fa_denied", severity="warning",
                      actor=user.email, message="Login denied because the second factor was missing or invalid.")
            auth_service.record_login_attempt(client_ip, email, success=False)
            user.failed_login_count = min(int(user.failed_login_count or 0) + 1, 10)
            db.add(user)
            db.commit()
            return J({"detail": "A valid authenticator code is required"}, 401)
        auth_service.record_login_attempt(client_ip, email, success=True)
        token = auth_service.create_session(db, user, ttl_hours=settings.session_ttl_hours, ip_address=client_ip, user_agent=request.headers.get("user-agent"))
        db.commit()
        return J({"ok": True, "token": token, "email": user.email, "token_type": "bearer"})
    finally:
        db.close()


async def auth_2fa_setup(request: Request):
    db = db_sess()
    try:
        user, err = require_user(request, db)
        if err or not user:
            return err or J({"detail": "Authentication required"}, 401)
        try:
            result = auth_service.begin_two_factor_setup(user)
        except ValueError as exc:
            return J({"detail": str(exc)}, 409)
        except RuntimeError as exc:
            return J({"detail": str(exc)}, 503)
        db.add(user)
        db.commit()
        return J({"ok": True, **result, "enabled": False})
    finally:
        db.close()


async def auth_2fa_confirm(request: Request):
    db = db_sess()
    try:
        user, err = require_user(request, db)
        if err or not user:
            return err or J({"detail": "Authentication required"}, 401)
        p = await body(request)
        if not auth_service.confirm_two_factor(user, p.get("totp_code", "")):
            db.rollback()
            return J({"detail": "Invalid authenticator code; second factor remains disabled"}, 400)
        db.add(user)
        log_event(db, action="auth.2fa_enabled", actor=user.email,
                  message="Two-factor authentication enabled.")
        db.commit()
        return J({"ok": True, "two_factor_enabled": True})
    finally:
        db.close()


async def auth_2fa_disable(request: Request):
    db = db_sess()
    try:
        user, err = require_user(request, db)
        if err or not user:
            return err or J({"detail": "Authentication required"}, 401)
        p = await body(request)
        if not auth_service.disable_two_factor(user, p.get("totp_code", "")):
            db.rollback()
            return J({"detail": "Invalid authenticator code or second factor is not enabled"}, 400)
        db.add(user)
        log_event(db, action="auth.2fa_disabled", severity="warning", actor=user.email,
                  message="Two-factor authentication disabled after second-factor verification.")
        db.commit()
        return J({"ok": True, "two_factor_enabled": False})
    finally:
        db.close()


async def auth_logout(request: Request):
    db = db_sess()
    try:
        token = _bearer(request)
        ok = auth_service.revoke_session(db, token) if token else False
        db.commit()
        return J({"ok": ok})
    finally:
        db.close()


async def auth_me(request: Request):
    db = db_sess()
    try:
        user, err = require_user(request, db)
        if err and auth_service.auth_required(): return err
        if not user:
            return J({"authenticated": False, "auth_required": auth_service.auth_required()})
        return J({"authenticated": True, "email": user.email, "is_admin": user.is_admin, "two_factor_enabled": user.two_factor_enabled})
    finally:
        db.close()


async def config_changes(request: Request):
    db = db_sess()
    try:
        user, err = require_user(request, db)
        if err: return err
        rows = db.execute(select(ConfigChange).order_by(desc(ConfigChange.id)).limit(100)).scalars().all()
        return J({"items": [{"id": r.id, "created_at": r.created_at.isoformat(), "actor": r.actor, "scope": r.scope, "key": r.key, "approved": r.approved, "reason": r.reason} for r in rows]})
    finally:
        db.close()


async def chat(request: Request):
    db = db_sess()
    try:
        user, err = require_user(request, db)
        if err: return err
        p = await body(request)
        msg = (p.get("message") or "").strip()
        low = msg.lower()
        state = core_service.get_state(db)
        decisions = db.execute(select(DecisionRecord).order_by(desc(DecisionRecord.id)).limit(5)).scalars().all()
        from app.knowledge.base import list_concepts
        if not msg:
            reply = "اكتب سؤالك عن حالة النظام، القرارات، الاستراتيجيات، المخاطر، أو التحليل."
        elif any(k in low for k in ["حالة", "status", "يعمل", "متوقف", "demo", "live", "spot", "futures", "earn"]):
            reply = f"حالة النظام: البيئة={state['environment_mode']}، السوق={state['market_type']}، مستقل={state['autonomous_enabled']}، إيقاف طوارئ={state['emergency_stop']}. Demo→Live تلقائي معطل؛ لا صلاحيات سحب."
        elif any(k in low for k in ["قرار", "decision", "لماذا", "why", "صفقة", "trade"]):
            if decisions:
                d = decisions[0]
                reply = f"آخر قرار: {d.symbol} → {d.action} (استراتيجية {d.strategy}, ثقة {d.confidence}, مصدر {d.data_source}). السبب: {d.rationale}"
            else:
                reply = "لا توجد قرارات مسجلة بعد. شغّل تحليلًا من Market Scanner."
        elif any(k in low for k in ["استرات", "strategy"]):
            reply = "الاستراتيجيات: " + ", ".join([f"{s['name']}[{s['status']}]" for s in core_service.strategies()[:8]])
        elif any(k in low for k in ["مخاطر", "risk", "رافعة", "رسوم", "امتثال", "compliance"]):
            reply = f"حدود الأمان: {state.get('risk_limits',{})}. محرك الامتثال يسبق أي تنفيذ؛ Live يتطلب تمكينًا صريحًا وتحقق شروط؛ السحب/التحويل ممنوعان."
        elif any(k in low for k in ["تشخيص", "diagnosis", "خطأ", "error", "مشكلة", "issue", "إصلاح", "heal", "صحة", "health"]):
            from app.services.self_diagnosis import get_engine
            engine = get_engine()
            report = engine.run_full_check()
            if any(k in low for k in ["أصلح", "fix", "heal", "repair"]):
                healed = []
                for issue in report.get("issues", []):
                    h = engine.attempt_auto_heal(issue)
                    if h.get("healed"):
                        healed.append(h)
                reply = "تشخيص: " + report["overall"] + ". مشكلات: " + str(len(report["issues"])) + ". مُصلحة تلقائياً: " + str(len(healed)) + "."
            else:
                reply = f"تشخيص النظام: حالة={report['overall']}. مكونات: {len(report['components'])}. مشكلات: {len(report['issues'])}. " + engine.get_summary_text()
        elif any(k in low for k in ["bos", "choch", "سيولة", "liquidity", "اختراق", "breakout", "شمعة", "candle", "rsi", "macd"]):
            concepts = list_concepts(query=msg)[:3]
            reply = " | ".join([f"{c['title']}: {c['summary']}" for c in concepts]) if concepts else "جرّب: BOS, CHOCH, liquidity, breakout, RSI, MACD."
        else:
            reply = "مساعد TITAN مرتبط ببيانات النظام. اسأل عن الحالة، القرارات، الاستراتيجيات، المخاطر/الامتثال، أو مفاهيم التحليل. التكامل مع LLM خارجي اختياري ويخضع للأمان."
        return J({"reply": reply, "grounded": True, "used_external_llm": False})
    finally:
        db.close()


async def ai_providers(request: Request):
    db = db_sess()
    try:
        user, err = require_user(request, db)
        if err: return err
        rows = list_configs(db)
        return J({"supported_providers": sorted(SUPPORTED_PROVIDERS),
                  "status": provider_status(db),
                  "items": [{"id": r.id, "name": r.name, "provider": r.provider, "model": r.model,
                             "api_key_ref_set": bool(r.api_key_ref), "enabled": bool(r.enabled),
                             "priority": r.priority, "tasks": r.tasks or []} for r in rows]})
    finally:
        db.close()


async def ai_provider_create(request: Request):
    db = db_sess()
    try:
        user, err = require_user(request, db)
        if err: return err
        p = await body(request)
        import ipaddress
        import re
        from urllib.parse import urlsplit

        provider = p.get("provider")
        if provider not in SUPPORTED_PROVIDERS:
            return J({"detail": "unsupported provider"}, 400)
        name = str(p.get("name") or "").strip()
        model = str(p.get("model") or "").strip()
        key_ref = p.get("api_key_ref")
        api_key = p.get("api_key")
        encrypted_metadata = None
        if not name or len(name) > 128 or not model or len(model) > 128:
            return J({"detail": "provider_name_and_model_required_max_128"}, 400)
        if api_key is not None:
            if (not isinstance(api_key, str) or not 8 <= len(api_key.strip()) <= 512
                    or any(ord(char) < 32 for char in api_key)):
                return J({"detail": "api_key_must_be_8_to_512_printable_characters"}, 400)
            # Never persist an AI key in plaintext or return it to the browser.
            from app.services.credentials import encrypt_secret
            encrypted_metadata = {"api_key_encrypted": encrypt_secret(api_key.strip())}
            key_ref = "encrypted:metadata"
        elif not isinstance(key_ref, str) or not re.fullmatch(r"env:[A-Z][A-Z0-9_]{1,127}", key_ref.strip()):
            return J({"detail": "provide_api_key_or_valid_env_reference"}, 400)
        base_url = str(p.get("base_url") or "").strip() or None
        if provider == "openai_compatible" and not base_url:
            return J({"detail": "base_url_required_for_openai_compatible"}, 400)
        if base_url and provider != "openai_compatible":
            from app.integrations.ai_providers import _default_base_url
            official_base_url = _default_base_url(provider)
            if not official_base_url or base_url.rstrip("/") != official_base_url.rstrip("/"):
                return J({"detail": "custom_base_url_only_for_openai_compatible"}, 400)
        if base_url:
            try:
                parsed = urlsplit(base_url)
                host = (parsed.hostname or "").lower().rstrip(".")
                port = parsed.port
                is_private_ip = False
                try:
                    address = ipaddress.ip_address(host)
                    is_private_ip = not address.is_global
                except ValueError:
                    pass
                # Permit only this exact local OmniRoute endpoint over HTTP.
                # All other private/local HTTP destinations remain blocked.
                is_omniroute_loopback = (
                    provider == "openai_compatible"
                    and parsed.scheme == "http"
                    and host == "127.0.0.1"
                    and port == 20128
                    and parsed.path.rstrip("/") == "/v1"
                )
                is_public_https = (
                    parsed.scheme == "https"
                    and bool(host)
                    and not is_private_ip
                    and host not in {"localhost", "metadata.google.internal"}
                    and not host.endswith((".localhost", ".local", ".internal"))
                )
                if (not host or parsed.username or parsed.password
                        or parsed.query or parsed.fragment
                        or (port is not None and not 1 <= port <= 65535)
                        or len(base_url) > 512
                        or not (is_omniroute_loopback or is_public_https)):
                    return J({"detail": "base_url_must_be_public_https_or_exact_local_omniroute"}, 400)
            except ValueError:
                return J({"detail": "invalid_provider_base_url"}, 400)
        row = AIProviderConfig(
            name=name, provider=provider, model=model, base_url=base_url,
            api_key_ref=key_ref.strip(), enabled=p.get("enabled") is True,
            priority=int(p.get("priority", 100)), tasks=p.get("tasks") or [],
            metadata_json=encrypted_metadata,
        )
        db.add(row); db.commit(); db.refresh(row)
        return J({"id": row.id})
    except (TypeError, ValueError, RuntimeError) as exc:
        db.rollback()
        return J({"detail": "invalid_provider_configuration", "reason": str(exc)[:160]}, 400)
    finally:
        db.close()


async def ai_provider_test(request: Request):
    db = db_sess()
    try:
        user, err = require_user(request, db)
        if err: return err
        cfg = db.get(AIProviderConfig, int(request.path_params["provider_id"]))
        if not cfg: return J({"detail": "not found"}, 404)
        result = invoke_provider(cfg, [{"role": "user", "content": "Reply with: TITAN_OK"}], task="test")
        record_usage(db, cfg, "test", result); db.commit()
        return J(result.to_dict())
    finally:
        db.close()


async def ai_provider_control(request: Request):
    db = db_sess()
    try:
        user, err = require_user(request, db)
        if err: return err
        cfg = db.get(AIProviderConfig, int(request.path_params["provider_id"]))
        if cfg is None:
            return J({"ok": False, "error": "provider_not_found"}, 404)
        p = await body(request)
        enabled = p.get("enabled")
        if type(enabled) is not bool:
            return J({"ok": False, "error": "enabled_must_be_boolean"}, 400)
        previous = bool(cfg.enabled)
        cfg.enabled = enabled
        db.add(cfg)
        log_event(db, action="ai.provider_enabled" if enabled else "ai.provider_disabled",
                  severity="info" if enabled else "warning",
                  message=f"External AI provider {cfg.provider}/{cfg.model} {'enabled' if enabled else 'disabled'}.",
                  details={"provider_id": cfg.id, "name": cfg.name,
                           "previous_enabled": previous, "enabled": enabled})
        db.commit()
        return J({"ok": True, "provider_id": cfg.id, "enabled": bool(cfg.enabled),
                  "status": provider_status(db)})
    finally:
        db.close()


async def ai_research(request: Request):
    return J({"checked_at_note": "Web lookups performed in-session; verify current terms/pricing/access before relying on them. Not live-tested without API keys.",
              "sources": [
                  {"provider": "Anthropic", "urls": ["https://www.anthropic.com/legal/supported-regions", "https://platform.claude.com/docs/en/about-claude/pricing"], "algeria_note": "Retrieved supported-regions page listed Algeria; verify directly."},
                  {"provider": "OpenAI", "urls": ["https://developers.openai.com/api/docs/supported-countries.md", "https://openai.com/api/pricing/"], "algeria_note": "Retrieved supported-countries markdown included Algeria; verify pricing/models/terms directly."},
                  {"provider": "Google Gemini API", "urls": ["https://ai.google.dev/gemini-api/docs/available-regions", "https://ai.google.dev/gemini-api/docs/pricing"], "algeria_note": "Could not conclusively confirm Algeria from retrieved content; verify directly."},
                  {"provider": "Mistral", "urls": ["https://mistral.ai/pricing", "https://docs.mistral.ai/getting-started/models/"], "algeria_note": "Verify directly."},
                  {"provider": "DeepSeek", "urls": ["https://api-docs.deepseek.com/quick_start/pricing"], "algeria_note": "Verify directly."},
                  {"provider": "xAI", "urls": ["https://docs.x.ai/developers/models"], "algeria_note": "Verify directly."},
              ],
              "caveat": "No model is connected and no feature works until configured with valid credentials and tested successfully."})


async def compliance_check(request: Request):
    db = db_sess()
    try:
        user, err = require_user(request, db)
        if err: return err
        p = await body(request)
        g = PreTradeOrderGateway()
        r = g.validate(symbol=p.get("symbol","BTC/USDT"), market=MarketType(p.get("market_type","spot")), side="buy", order_type="limit",
                       quantity=0.001, price=1.0, reference_price=1.0, environment_mode=p.get("environment_mode","demo"), live_enabled=False)
        if not r.ok:
            log_event(db, action="compliance.block", severity="warning", message=r.reason, details=r.normalized,
                      environment_mode=p.get("environment_mode"), market_type=p.get("market_type")); db.commit()
        return J({"gateway": r.to_dict(), "sources": ["https://www.binance.com/en/terms", "https://www.binance.com/en/terms-api", "https://developers.binance.com/"],
                  "note": "Software precheck only. Verify official Binance terms/policies, eligibility, KYC, geo restrictions, symbol filters, and API limits before live operations."})
    finally:
        db.close()



async def demo_cycle(request: Request):
    db = db_sess()
    try:
        user, err = require_user(request, db)
        if err: return err
        p = await body(request)
        market = p.get("market_type", "spot")
        environment = p.get("environment_mode", "demo")
        execute_shadow = p.get("execute_shadow", True)
        if type(execute_shadow) is not bool:
            return J({"ok": False, "error": "execute_shadow_must_be_boolean", "executed": False}, 400)
        if market not in {"spot", "futures"} or environment != "demo":
            return J({"ok": False, "error": "demo_spot_or_futures_only_policy", "executed": False}, 400)
        selected_state = core_service.get_state(db)
        if market != selected_state.get("market_type"):
            return J({"ok": False, "error": "requested_market_does_not_match_selected_market", "executed": False}, 409)
        from app.services.orchestrator import TradingOrchestrator
        result = TradingOrchestrator().run_cycle(
            db, symbol=p.get("symbol", "BTC/USDT"), market=market, environment="demo",
            execute_shadow=execute_shadow,
        )
        db.commit()
        return J(result)
    except Exception as e:
        db.rollback()
        return J({"ok": False, "error": str(e), "executed": False}, 500)
    finally:
        db.close()


async def master_start(request):
    db = db_sess()
    requested_target = ""
    try:
        user, err = require_user(request, db)
        if err:
            return err
        p = await body(request)
        requested_target = str(p.get("account_target") or "").strip().lower()
        live_confirm = p.get("live_confirm") is True
        if requested_target == "live" and live_confirm:
            current = core_service.get_state(db)
            active_states = {"starting", "running", "analyzing", "executing", "waiting_data"}
            if (current.get("account_target") == "live"
                    and current.get("live_confirmed_by_user") is not True
                    and current.get("bot_run_state") in active_states):
                from app.services.demo_worker_supervisor import demo_worker_supervisor
                stopped = demo_worker_supervisor.stop(timeout=30.0)
                if not stopped.get("ok"):
                    return J({"ok": False, "error": "live_read_only_observer_did_not_stop",
                              "worker": stopped}, 409)
                core_service.stop_bot(db, reason="user_confirmed_live_execution")
        prepared = core_service.start_bot(
            db, account_target=p.get("account_target"),
            live_confirm=live_confirm,
        )
        selected_state = prepared.get("state", {})
        selected_market = selected_state.get("market_type", "spot")
        selected_target = selected_state.get("account_target", "demo")
        from app.services.demo_worker_supervisor import demo_worker_supervisor
        started = demo_worker_supervisor.start(
            market=selected_market,
            account_target=selected_target,
            interval_seconds=60,
        )
        if not started.get("ok"):
            from app.models.entities import SystemState
            from datetime import datetime, timezone
            if demo_worker_supervisor.status().get("running"):
                core_service.set_state(db, emergency_stop=True)
            state = db.get(SystemState, 1)
            if state is not None:
                state.bot_run_state = "stopped"
                state.autonomous_enabled = False
                state.live_confirmed_by_user = False
                state.bot_stop_reason = started.get("error", "worker_start_failed")
                state.bot_stopped_at = datetime.now(timezone.utc)
                db.add(state)
                db.commit()
            return J({
                "ok": False, "error": "trading_worker_start_failed",
                "worker": started, "state": core_service.get_state(db),
            }, 503)
        db.expire_all()
        return J({
            "ok": True, "state": core_service.get_state(db),
            "checks": prepared.get("checks", {}), "worker": started,
            "note": f"Supervised {selected_target}/{selected_market} worker is running.",
        })
    except Exception as e:
        db.rollback()
        if requested_target == "live":
            # A failed Live start must not leave its one-time user approval
            # latched in storage. Stop any partially-started worker first.
            try:
                from app.services.demo_worker_supervisor import demo_worker_supervisor
                stopped = demo_worker_supervisor.stop(timeout=30.0)
                if not stopped.get("ok"):
                    core_service.set_state(db, emergency_stop=True)
                from app.models.entities import SystemState
                from datetime import datetime, timezone
                state = db.get(SystemState, 1)
                if state is not None:
                    state.live_confirmed_by_user = False
                    state.bot_run_state = "stopped"
                    state.autonomous_enabled = False
                    state.bot_stop_reason = "live_start_failed"
                    state.bot_stopped_at = datetime.now(timezone.utc)
                    db.add(state)
                    db.commit()
            except Exception:
                db.rollback()
        return J({"ok": False, "detail": str(e)}, 400)
    finally:
        db.close()


async def chat_reports(request):
    db = db_sess()
    try:
        user, err = require_user(request, db)
        if err:
            return err
        try:
            limit = max(1, min(100, int(request.query_params.get("limit", "25"))))
        except (TypeError, ValueError):
            return J({"ok": False, "error": "invalid_report_limit"}, 400)
        decisions = db.execute(select(DecisionRecord).order_by(desc(DecisionRecord.id)).limit(limit)).scalars().all()
        audits = db.execute(select(AuditLog).order_by(desc(AuditLog.id)).limit(limit)).scalars().all()
        trades = db.execute(select(TradeRecord).order_by(desc(TradeRecord.id)).limit(limit)).scalars().all()
        positions = db.execute(select(PositionRecord).order_by(desc(PositionRecord.id)).limit(limit)).scalars().all()
        snapshots = db.execute(select(PortfolioSnapshot).order_by(desc(PortfolioSnapshot.id)).limit(5)).scalars().all()
        return J({
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "decisions": [{"id": r.id, "created_at": r.created_at.isoformat() if r.created_at else None,
                "symbol": r.symbol, "action": r.action, "strategy": r.strategy, "confidence": r.confidence,
                "entry_price": r.entry_price, "stop_loss": r.stop_loss, "take_profit": r.take_profit,
                "leverage": r.leverage, "rationale": r.rationale, "data_source": r.data_source,
                "environment_mode": r.environment_mode, "market_type": r.market_type,
                "ai_evaluation": ((r.indicators or {}).get("ai_evaluation")
                                  if isinstance(r.indicators, dict) else None)} for r in decisions],
            "audit": [{"id": r.id, "created_at": r.created_at.isoformat() if r.created_at else None,
                "action": r.action, "severity": r.severity, "message": r.message,
                "environment_mode": r.environment_mode, "market_type": r.market_type, "details": r.details} for r in audits],
            "trades": [{"id": r.id, "created_at": r.created_at.isoformat() if r.created_at else None,
                "closed_at": r.closed_at.isoformat() if r.closed_at else None, "symbol": r.symbol,
                "side": r.side, "status": r.status, "strategy": r.strategy, "quantity": r.quantity,
                "entry_price": r.entry_price, "exit_price": r.exit_price, "leverage": r.leverage,
                "fees_total": r.fees_total, "pnl_gross": r.pnl_gross, "pnl_net": r.pnl_net, "reason": r.reason,
                "metadata": r.metadata_json} for r in trades],
            "positions": [{"id": r.id, "symbol": r.symbol, "side": r.side, "status": r.status,
                "quantity": r.quantity, "entry_avg_price": r.entry_avg_price, "mark_price": r.mark_price,
                "leverage": r.leverage, "unrealized_pnl": r.unrealized_pnl, "realized_pnl": r.realized_pnl,
                "environment_mode": r.environment_mode, "market_type": r.market_type} for r in positions],
            "portfolio_snapshots": [{"id": r.id, "created_at": r.created_at.isoformat() if r.created_at else None,
                "environment_mode": r.environment_mode, "market_type": r.market_type,
                "cash_balance": r.cash_balance, "equity_estimate": r.equity_estimate,
                "unrealized_pnl": r.unrealized_pnl, "realized_pnl": r.realized_pnl,
                "fees_total": r.fees_total, "exposure_notional": r.exposure_notional,
                "positions_open": r.positions_open, "details": r.details} for r in snapshots],
        })
    finally:
        db.close()


async def master_trading_pause(request):
    db = db_sess()
    try:
        user, err = require_user(request, db)
        if err:
            return err
        p = await body(request)
        paused = p.get("paused")
        if type(paused) is not bool:
            return J({"ok": False, "error": "paused_must_be_boolean"}, 400)
        try:
            return J(core_service.set_trading_paused(db, paused))
        except ValueError as e:
            db.rollback()
            return J({"ok": False, "error": str(e), "state": core_service.get_state(db)}, 409)
    finally:
        db.close()


async def master_stop(request):
    db = db_sess()
    try:
        user, err = require_user(request, db)
        if err: return err
        p = await body(request)
        close_positions = p.get("close_positions", False)
        if type(close_positions) is not bool:
            return J({"ok": False, "error": "close_positions_must_be_boolean",
                      "state": core_service.get_state(db)}, 400)
        result = core_service.stop_bot(
            db, reason=p.get("reason", "user_stop"),
            close_positions=close_positions,
        )
        from app.services.demo_worker_supervisor import demo_worker_supervisor
        stopped = demo_worker_supervisor.stop(timeout=30.0)
        if not stopped.get("ok"):
            # The persisted stop flag blocks new entries, but a timed-out thread
            # may still be inside an exchange request. Latch the independent
            # emergency flag until the worker is confirmed stopped and state is
            # reconciled.
            emergency_state = core_service.set_state(db, emergency_stop=True)
            return J({"ok": False, "error": "demo_worker_stop_failed",
                      "emergency_stop_latched": True,
                      "worker": stopped, "state": emergency_state}, 503)
        return J({**result, "worker": stopped})
    except Exception as e:
        db.rollback()
        return J({"ok": False, "detail": str(e)}, 400)
    finally:
        db.close()


async def master_emergency_reset(request):
    db = db_sess()
    try:
        user, err = require_user(request, db)
        if err: return err
        try:
            return J(core_service.reset_emergency_stop(db))
        except ValueError as e:
            db.rollback()
            return J({"ok": False, "error": str(e), "emergency_stop": True}, 409)
    except Exception as e:
        db.rollback()
        return J({"ok": False, "error": str(e)}, 500)
    finally:
        db.close()


async def master_emergency(request):
    db = db_sess()
    try:
        user, err = require_user(request, db)
        if err: return err
        state = core_service.set_state(db, emergency_stop=True)
        from app.services.demo_worker_supervisor import demo_worker_supervisor
        stopped = demo_worker_supervisor.stop()
        return J({"ok": bool(stopped.get("ok")), "state": state, "worker": stopped},
                 200 if stopped.get("ok") else 503)
    finally:
        db.close()




shadow_service = ShadowTradingService()

async def shadow_order(request):
    db = db_sess()
    try:
        user, err = require_user(request, db)
        if err: return err
        p = await body(request)
        idempotency_key = p.get("idempotency_key")
        if (not isinstance(idempotency_key, str) or not idempotency_key.strip()
                or len(idempotency_key.strip()) > 128):
            return J({"detail": "valid_idempotency_key_required"}, 400)
        selected_state = core_service.get_state(db)
        requested_market = p.get("market_type", "spot")
        requested_environment = p.get("environment_mode", "demo")
        if (requested_market != selected_state.get("market_type")
                or requested_environment != selected_state.get("environment_mode")
                or requested_environment != "demo"):
            return J({"detail": "shadow_order_market_must_match_selected_demo_state"}, 409)
        res = shadow_service.create_shadow_order(db, env=p.get("environment_mode","demo"), market=p.get("market_type","spot"),
            symbol=p.get("symbol","BTC/USDT"), side=p.get("side","buy"), order_type=p.get("order_type","limit"),
            quantity=float(p.get("quantity",0)), price=p.get("price"), reference_price=p.get("reference_price"),
            idempotency_key=idempotency_key.strip(), simulate=p.get("simulate","fill"), fill_ratio=p.get("fill_ratio",1.0),
            fee_rate=p.get("fee_rate",0.001), mark_price=p.get("mark_price"),
            leverage=p.get("leverage",1.0), reduce_only=p.get("reduce_only",False))
        db.commit()
        return J({"ok": res.ok, "message": res.message, "details": res.details,
                  "order": {"id": res.order.id, "status": res.order.status, "side": res.order.side,
                            "filled_quantity": res.order.filled_quantity, "fees_paid": res.order.fees_paid,
                            "leverage": (res.order.metadata_json or {}).get("leverage", 1.0),
                            "reduce_only": (res.order.metadata_json or {}).get("reduce_only", False),
                            "idempotency_key": res.order.idempotency_key} if res.order else None,
                  "position": {"id": res.position.id, "side": res.position.side,
                               "quantity": res.position.quantity, "entry_avg_price": res.position.entry_avg_price,
                               "leverage": (res.position.metadata_json or {}).get("leverage", 1.0),
                               "unrealized_pnl": res.position.unrealized_pnl} if res.position else None})
    except Exception as e:
        db.rollback(); return J({"detail": str(e)}, 400)
    finally:
        db.close()

async def portfolio(request):
    db = db_sess()
    try:
        user, err = require_user(request, db)
        if err:
            return err
        env = request.query_params.get("environment_mode", "demo")
        market = request.query_params.get("market_type", "spot")
        if market not in {"spot", "futures"}:
            return J({"ok": False, "error": "unsupported_shadow_portfolio_market"}, 400)
        result = shadow_service.get_portfolio(db, env=env, market=market)
        symbols = {str(position.get("symbol")) for position in result.get("positions", []) if position.get("symbol")}
        refresh = {"ok": True, "updated": 0, "source": "none"}
        if symbols:
            try:
                from app.engines.market_data import MarketDataEngine
                prices = MarketDataEngine().get_public_last_prices(symbols, market_type=market)
                updated = shadow_service.update_mark_prices(db, env=env, market=market, prices=prices)
                if updated:
                    shadow_service.record_snapshot(db, env=env, market=market)
                    db.commit()
                    result = shadow_service.get_portfolio(db, env=env, market=market)
                refresh = {
                    "ok": bool(prices), "updated": updated,
                    "source": "binance_futures_mark_price" if market == "futures" else "binance_spot_public_ticker",
                }
            except Exception as exc:
                db.rollback()
                refresh = {"ok": False, "updated": 0, "error": type(exc).__name__}
        result["mark_price_refresh"] = refresh
        return J(result)
    finally:
        db.close()


async def shadow_close(request):
    db = db_sess()
    try:
        user, err = require_user(request, db)
        if err: return err
        p = await body(request)
        res = shadow_service.close_shadow_position(
            db, position_id=p.get("position_id"),
            close_price=p.get("close_price"), fee_rate=p.get("fee_rate", 0.001),
        )
        db.commit(); return J(res)
    except Exception as e:
        db.rollback(); return J({"detail": str(e)}, 400)
    finally:
        db.close()


async def shadow_position_protection(request):
    db = db_sess()
    try:
        user, err = require_user(request, db)
        if err: return err
        p = await body(request)
        result = shadow_service.set_position_protection(
            db, position_id=p.get("position_id"),
            stop_loss=p.get("stop_loss") if p.get("stop_loss") not in (None, "") else None,
            take_profit=p.get("take_profit") if p.get("take_profit") not in (None, "") else None,
            trailing_stop_pct=p.get("trailing_stop_pct") if p.get("trailing_stop_pct") not in (None, "") else None,
        )
        db.commit()
        return J(result, 200 if result.get("ok") else 400)
    except Exception as e:
        db.rollback()
        return J({"ok": False, "detail": str(e)}, 400)
    finally:
        db.close()





async def self_diagnosis(request):
    db = db_sess()
    try:
        user, err = require_user(request, db)
        if err and auth_service.auth_required(): return err
        engine = get_engine()
        report = engine.run_full_check()
        # Attempt auto-heal for recoverable issues
        for issue in report.get("issues", []):
            heal = engine.attempt_auto_heal(issue)
            if heal.get("healed"):
                report.setdefault("healed", []).append(heal)
        # Log to audit
        log_event(db, action="self_diagnosis.run", entity_type="system", details={"overall": report["overall"], "issue_count": len(report["issues"])})
        db.commit()
        return J(report)
    except Exception as e:
        db.rollback()
        return J({"detail": str(e)}, 500)
    finally:
        db.close()


async def download_zip(request):
    # Packaged application files may contain private configuration and user data.
    db = db_sess()
    try:
        user, err = require_user(request, db)
        if err:
            return err
        zip_path = Path('/storage/emulated/0/Android/data/com.androidharness.app/files/workspace/TITAN_SUPER_AI_PREMIUM_COMPLETE.zip')
        if not zip_path.exists():
            return JSONResponse({"error":"not found"}, status_code=404)
        resp = FileResponse(str(zip_path), filename='TITAN_SUPER_AI_PREMIUM_COMPLETE.zip', media_type='application/octet-stream')
        resp.headers['Content-Disposition'] = 'attachment; filename="TITAN_SUPER_AI_PREMIUM_COMPLETE.zip"'
        return resp
    finally:
        db.close()


async def backup_info(request):
    db = db_sess()
    try:
        user, err = require_user(request, db)
        if err:
            return err
        db_path = settings.database_url.replace('sqlite:///', '')
        data_dir = Path(os.getenv("TITAN_DATA_DIR", "")).expanduser() if os.getenv("TITAN_DATA_DIR", "").strip() else Path(__file__).resolve().parents[1] / "data"
        credentials_path = data_dir / "binance_testnet_creds.json"
        return J({
            "db_path": db_path,
            "credentials_path": str(credentials_path),
            "data_directory": str(data_dir),
            "backup_instructions": "انسخ ملف قاعدة البيانات وملف بيانات الاعتماد المشفرة من مسارات db_path وcredentials_path إلى تخزين احتياطي آمن ومشفر.",
            "note": "يجب أن يكون مسار TITAN_DATA_DIR على تخزين دائم في بيئة الإنتاج. Render Free يستخدم تخزيناً مؤقتاً وقد يفقد البيانات عند إعادة التشغيل أو إعادة النشر."
        })
    finally:
        db.close()


async def export_formats(request):
    libs = {}
    for m in ['openpyxl','reportlab','xlsxwriter']:
        try: __import__(m); libs[m]=True
        except Exception: libs[m]=False
    return J({"formats": {"csv": True, "xlsx": libs['openpyxl'] or libs['xlsxwriter'], "pdf": libs['reportlab']},
              "available_libraries": libs,
              "note": "CSV export is implemented. XLSX/PDF require optional libraries in the target environment; install and extend export service if needed."})


# Binance Testnet (Demo external) - NO real keys, NO live
def _get_binance_adapter(market: str = None):
    mkt = (market or os.getenv('BINANCE_TESTNET_MARKET', 'spot')).lower()
    stored = load_testnet_credentials(mkt)
    if mkt not in {'spot', 'futures'}:
        raise ValueError("unsupported_testnet_market")
    # Prefer stored, fallback to env
    if stored.get('configured'):
        return BinanceTestnetAdapter(BinanceConfig(
            enabled=True, environment='testnet', market=mkt,
            api_key_ref=f'stored:testnet:{mkt}:api_key', api_secret_ref=f'stored:testnet:{mkt}:api_secret'
        ))
    market_prefix = f"BINANCE_TESTNET_{mkt.upper()}"
    market_key = os.getenv(f"{market_prefix}_API_KEY")
    market_secret = os.getenv(f"{market_prefix}_API_SECRET")
    if market_key and market_secret:
        key_ref = f"env:{market_prefix}_API_KEY"
        secret_ref = f"env:{market_prefix}_API_SECRET"
    elif os.getenv("BINANCE_TESTNET_MARKET", "spot").strip().lower() == mkt:
        generic_key = os.getenv("BINANCE_TESTNET_API_KEY")
        generic_secret = os.getenv("BINANCE_TESTNET_API_SECRET")
        key_ref = "env:BINANCE_TESTNET_API_KEY" if generic_key and generic_secret else None
        secret_ref = "env:BINANCE_TESTNET_API_SECRET" if generic_key and generic_secret else None
    else:
        key_ref = secret_ref = None
    return BinanceTestnetAdapter(BinanceConfig(
        enabled=bool(key_ref and secret_ref), environment='testnet', market=mkt,
        api_key_ref=key_ref, api_secret_ref=secret_ref,
    ))


def _get_binance_live_adapter(market: str = None, db=None):
    mkt = (market or os.getenv("BINANCE_LIVE_MARKET", "spot")).lower()
    stored = load_binance_credentials("live", mkt)
    if mkt not in {"spot", "futures"}:
        raise ValueError("unsupported_live_market")
    state = core_service.get_state(db) if db is not None else {}
    confirmed = bool(state.get("live_confirmed_by_user") and state.get("account_target") == "live")
    if stored.get("configured"):
        return BinanceLiveAdapter(BinanceConfig(
            enabled=True, environment="live", market=mkt,
            api_key_ref=f"stored:live:{mkt}:api_key", api_secret_ref=f"stored:live:{mkt}:api_secret",
            live_confirmed_by_user=confirmed,
            live_worker_ready=False,
        ))
    prefix = f"BINANCE_LIVE_{mkt.upper()}"
    market_key, market_secret = os.getenv(f"{prefix}_API_KEY"), os.getenv(f"{prefix}_API_SECRET")
    generic_allowed = os.getenv("BINANCE_LIVE_MARKET", "spot").strip().lower() == mkt
    key_ref = f"env:{prefix}_API_KEY" if market_key else (
        "env:BINANCE_LIVE_API_KEY" if generic_allowed and os.getenv("BINANCE_LIVE_API_KEY") else None
    )
    secret_ref = f"env:{prefix}_API_SECRET" if market_secret else (
        "env:BINANCE_LIVE_API_SECRET" if generic_allowed and os.getenv("BINANCE_LIVE_API_SECRET") else None
    )
    return BinanceLiveAdapter(BinanceConfig(
        enabled=bool(key_ref and secret_ref), environment="live", market=mkt,
        api_key_ref=key_ref, api_secret_ref=secret_ref,
        live_confirmed_by_user=confirmed,
        live_worker_ready=False,
    ))


async def connect_page(request):
    # Serve the built mobile-first React terminal at the app root when present.
    # Keep the lightweight Testnet connection utility available at /connect
    # and as a safe fallback for backend-only development environments.
    project_root = Path(__file__).resolve().parents[2]
    frontend_index = project_root / "frontend" / "dist" / "index.html"
    if request.url.path == "/" and frontend_index.is_file():
        return FileResponse(frontend_index)
    try:
        p = Path(__file__).resolve().parent / "static_connect.html"
        return HTMLResponse(p.read_text(encoding="utf-8"))
    except Exception as e:
        return HTMLResponse(f"<h1>Error</h1><pre>{e}</pre>", status_code=500)


async def binance_execution_preflight(request):
    db = db_sess()
    try:
        user, err = require_user(request, db)
        if err:
            return err
        from app.services.binance_execution import binance_execution_service
        result = binance_execution_service.preflight(db, live_worker_ready=False)
        from app.services.demo_worker_supervisor import demo_worker_supervisor
        worker_status = demo_worker_supervisor.status()
        result["worker_available"] = True
        result["worker_running"] = bool(worker_status.get("running"))
        result["worker_status"] = worker_status
        result["orders_submitted"] = False
        result["note"] = (
            "Read-only readiness inspection; no order is submitted. When Testnet preflight is ready, "
            "the separate dashboard Start action starts the supervised worker, which repeats account, "
            "risk, order and reconciliation checks before any submission."
        )
        return J(result, 200 if result.get("ok") else 409)
    except Exception as exc:
        return J({"ok": False, "error": "binance_execution_preflight_failed", "detail": str(exc)[:240]}, 503)
    finally:
        db.close()


async def binance_live_status(request):
    db = db_sess()
    try:
        user, err = require_user(request, db)
        if err:
            return err
        market = request.query_params.get("market")
        adapter = _get_binance_live_adapter(market, db)
        status = adapter.status()
        state = core_service.get_state(db)
        return J({
            **status,
            "live_blocked": not bool(status.get("execution_ready")),
            "server_live_enabled": False,
            "user_live_confirmed": bool(state.get("live_confirmed_by_user")),
            "live_start_required": False,
            "live_execution_hard_disabled": True,
            "selected_account": state.get("account_target"),
            "orders_submitted": False,
            "note": ("Live account is connected for read-only monitoring only. Live order execution is hard-disabled in this release."),
        })
    except Exception as exc:
        return J({"ok": False, "error": str(exc)}, 400)
    finally:
        db.close()


async def binance_testnet_futures_maintenance_brackets(request):
    """Authenticated, read-only Futures Testnet maintenance-tier retrieval."""
    auth_error = _binance_auth_error(request)
    if auth_error:
        return auth_error
    symbol = request.query_params.get("symbol", "")
    if not symbol:
        return J({"ok": False, "error": "symbol_required"}, 400)
    try:
        adapter = _get_binance_adapter("futures")
        result = adapter.get_futures_maintenance_brackets(symbol)
        return J(result, 200)
    except ValueError as exc:
        return J({"ok": False, "error": str(exc)}, 400)
    except RuntimeError as exc:
        message = str(exc)
        status = 503 if "unavailable" in message or "credentials_required" in message else 400
        return J({"ok": False, "error": message, "no_orders_submitted": True}, status)
    except Exception:
        return J({"ok": False, "error": "futures_testnet_maintenance_bracket_fetch_failed",
                  "no_orders_submitted": True}, 503)


async def binance_testnet_status(request):
    auth_error = _binance_auth_error(request)
    if auth_error:
        return auth_error
    try:
        market = request.query_params.get('market')
        return J(_get_binance_adapter(market).status())
    except Exception as e:
        return J({"ok": False, "error": str(e)}, 400)

async def binance_testnet_validate_trade_permission(request):
    auth_error = _binance_auth_error(request)
    if auth_error:
        return auth_error
    try:
        payload = await body(request)
        market = str(payload.get("market") or request.query_params.get("market") or "spot").strip().lower()
        symbol = str(payload.get("symbol") or "BTCUSDT").strip().upper()
        if market not in {"spot", "futures"}:
            return J({"ok": False, "error": "unsupported_testnet_market", "order_submitted": False}, 400)
        result = _get_binance_adapter(market).validate_testnet_trade_permission(symbol)
        return J(result, 200 if result.get("ok") else 409)
    except Exception as exc:
        return J({"ok": False, "error": "testnet_trade_permission_validation_failed",
                  "detail": str(exc)[:240], "order_submitted": False}, 503)

async def binance_testnet_order(request):
    # Direct API-triggered orders bypass the supervised decision, position,
    # loss-limit, and durable execution lifecycle. Keep this legacy route
    # closed; only the supervised TITAN worker may call the execution service.
    auth_error = _binance_auth_error(request)
    if auth_error:
        return auth_error
    return J({
        "ok": False, "sent_to_exchange": False,
        "error": "direct_order_endpoint_disabled",
        "note": "Start the supervised TITAN worker after completing account preflight.",
    }, 403)

async def binance_order_cancel(request):
    auth_error = _binance_auth_error(request)
    if auth_error:
        return auth_error
    db = db_sess()
    try:
        user, err = require_user(request, db)
        if err:
            return err
        payload = await body(request)
        raw_id = payload.get("order_id")
        if isinstance(raw_id, bool):
            return J({"ok": False, "error": "invalid_local_order_id"}, 400)
        try:
            local_order_id = int(raw_id)
        except (TypeError, ValueError, OverflowError):
            return J({"ok": False, "error": "valid_order_id_required"}, 400)
        from app.services.binance_execution import binance_execution_service
        result = binance_execution_service.cancel_order(db, local_order_id=local_order_id)
        return J(result, 200 if result.get("ok") else 409)
    except Exception as exc:
        db.rollback()
        return J({"ok": False, "error": str(exc)[:240]}, 400)
    finally:
        db.close()


async def binance_testnet_account(request):
    auth_error = _binance_auth_error(request)
    if auth_error:
        return auth_error
    try:
        market = request.query_params.get('market')
        return J(_get_binance_adapter(market).get_account())
    except Exception as e:
        return J({"ok": False, "error": str(e)}, 400)



async def binance_testnet_reconcile(request):
    """Synchronize individual durable order states while the Testnet worker is stopped."""
    auth_error = _binance_auth_error(request)
    if auth_error:
        return auth_error
    db = db_sess()
    try:
        market = str(request.query_params.get("market") or "spot").strip().lower()
        if market not in {"spot", "futures"}:
            return J({"ok": False, "error": "unsupported_testnet_market"}, 400)
        selected = core_service.get_state(db)
        if str(selected.get("account_target") or "").lower() != "testnet":
            return J({"ok": False, "error": "testnet_account_not_selected",
                      "orders_submitted": False}, 409)
        if str(selected.get("market_type") or "").lower() != market:
            return J({"ok": False, "error": "testnet_market_selection_mismatch",
                      "selected_market": selected.get("market_type"),
                      "requested_market": market, "orders_submitted": False}, 409)

        from app.services.demo_worker_supervisor import demo_worker_supervisor
        worker = demo_worker_supervisor.status()
        active_states = {"starting", "running", "analyzing", "executing", "waiting_data"}
        if worker.get("running") is True or str(selected.get("bot_run_state") or "stopped").lower() in active_states:
            return J({
                "ok": False, "error": "stop_testnet_worker_before_manual_reconciliation",
                "worker": worker, "bot_run_state": selected.get("bot_run_state"),
                "orders_submitted": False,
            }, 409)

        adapter = _get_binance_adapter(market)
        from app.services.binance_trading_worker import _sync_pending_orders
        try:
            order_sync = _sync_pending_orders(db, adapter, "testnet", market)
        except Exception as sync_error:
            db.rollback()
            latest = core_service.get_state(db)
            if latest.get("emergency_stop") is not True:
                core_service.set_state(db, emergency_stop=True)
                latest = core_service.get_state(db)
            return J({
                "ok": False,
                "error": "testnet_order_status_sync_requires_manual_review",
                "detail": str(sync_error)[:240],
                "order_sync": {"ok": False},
                "manual_review_required": True,
                "emergency_stop_active": bool(latest.get("emergency_stop")),
                "orders_submitted": False,
            }, 409)

        local_rows = db.execute(select(OrderRecord).where(
            OrderRecord.environment_mode == "testnet",
            OrderRecord.market_type == market,
            OrderRecord.shadow.is_(False),
            OrderRecord.status.in_(["pending", "new", "submitted", "partially_filled", "unknown"]),
        )).scalars().all()
        local_open_orders = [{
            "id": row.exchange_order_id or row.id,
            "orderId": row.exchange_order_id,
            "clientOrderId": row.client_order_id,
        } for row in local_rows]
        open_order_result = adapter.reconcile(local_open_orders=local_open_orders)
        if not isinstance(open_order_result, dict) or open_order_result.get("ok") is not True:
            latest = core_service.get_state(db)
            if latest.get("emergency_stop") is not True:
                core_service.set_state(db, emergency_stop=True)
                latest = core_service.get_state(db)
            return J({
                "ok": False,
                "error": "testnet_open_order_reconciliation_mismatch",
                "order_sync": order_sync,
                "open_order_reconciliation": open_order_result,
                "manual_review_required": True,
                "emergency_stop_active": bool(latest.get("emergency_stop")),
                "orders_submitted": False,
            }, 409)

        final_state = core_service.get_state(db)
        return J({
            "ok": True,
            "market": market,
            "order_sync": order_sync,
            "open_order_reconciliation": open_order_result,
            "manual_review_required": False,
            "emergency_stop_active": bool(final_state.get("emergency_stop")),
            "note": (
                "Order states, fills, positions and fees were synchronized read-only. "
                "This action does not clear Emergency Stop or start the worker; use the separate verified reset flow."
            ),
            "orders_submitted": False,
        })
    except Exception as e:
        db.rollback()
        return J({"ok": False, "error": "testnet_reconciliation_failed",
                  "detail": str(e)[:240], "orders_submitted": False}, 409)
    finally:
        db.close()


async def binance_testnet_orders(request):
    auth_error = _binance_auth_error(request)
    if auth_error:
        return auth_error
    try:
        market = request.query_params.get('market')
        symbol = request.query_params.get('symbol')
        return J(_get_binance_adapter(market).get_open_orders(symbol))
    except Exception as e:
        return J({"ok": False, "error": str(e)}, 400)


async def binance_testnet_close(request):
    # Position closes must pass through the same durable idempotency,
    # reconciliation, and emergency-stop path as every other execution.
    auth_error = _binance_auth_error(request)
    if auth_error:
        return auth_error
    return J({
        "ok": False, "sent_to_exchange": False,
        "error": "direct_close_endpoint_disabled",
        "note": "Position management is owned by the supervised TITAN worker.",
    }, 403)

async def binance_testnet_save(request):
    auth_error = _binance_auth_error(request)
    if auth_error:
        return auth_error
    try:
        p = await body(request)
        api_key = (p.get('api_key') or '').strip()
        api_secret = (p.get('api_secret') or '').strip()
        market = (p.get('market') or 'spot').strip().lower()
        if market not in {'spot', 'futures'}:
            return J({"ok": False, "error": "unsupported_testnet_market"}, 400)
        if not api_key or not api_secret:
            return J({"ok": False, "error": "api_key_and_secret_required"}, 400)

        # Verify credentials against the selected Testnet account before persisting.
        candidate = BinanceTestnetAdapter(BinanceConfig(
            enabled=True, environment='testnet', market=market,
            api_key_ref=api_key, api_secret_ref=api_secret,
        ))
        status = candidate.status()
        if not status.get('connected'):
            return J({"ok": False, "saved": False, "error": "testnet_connection_failed",
                      "test_status": status, "note": "Credentials were not stored."}, 400)

        db = db_sess()
        try:
            from app.services.demo_worker_supervisor import demo_worker_supervisor
            current = core_service.get_state(db)
            active_states = {"starting", "running", "analyzing", "executing", "waiting_data"}
            if current.get("bot_run_state") in active_states:
                # Stop the existing worker before replacing credentials so it
                # cannot switch account keys mid-cycle.
                stopped = demo_worker_supervisor.stop(timeout=30.0)
                if not stopped.get("ok"):
                    core_service.set_state(db, emergency_stop=True)
                    return J({"ok": False, "saved": False, "auto_started": False,
                              "worker": stopped, "test_status": status,
                              "error": "previous_worker_did_not_stop",
                              "note": "New credentials were not stored. Emergency stop was latched because the previous worker did not stop cleanly."}, 503)
                core_service.stop_bot(db, reason="switch_account_to_testnet")
            save_testnet_credentials(api_key, api_secret, market)
            core_service.set_state(db, account_target="testnet", market_type=market)
            return J({
                "ok": True, "saved": True, "auto_started": False,
                "test_status": status,
                "note": (
                    "Testnet credentials are encrypted and the selected market is ready for permission validation. "
                    "The worker remains stopped until TRADE scope is verified with the non-executing order/test endpoint "
                    "and you explicitly start Testnet from the dashboard."
                ),
            })
        finally:
            db.close()
    except Exception as e:
        return J({"ok": False, "saved": False, "error": str(e)}, 400)

def _stop_worker_before_credential_deletion(db, target: str, market: str | None, reason: str) -> dict[str, Any]:
    """Stop the owning account worker before removing credentials it may still use."""
    from app.services.demo_worker_supervisor import demo_worker_supervisor

    current = core_service.get_state(db)
    worker = demo_worker_supervisor.status()
    active_states = {"starting", "running", "analyzing", "executing", "waiting_data"}
    state_matches = (
        str(current.get("account_target") or "demo").lower() == target
        and (market is None or str(current.get("market_type") or "spot").lower() == market)
        and str(current.get("bot_run_state") or "stopped").lower() in active_states
    )
    worker_matches = (
        worker.get("running") is True
        and str(worker.get("account_target") or "").lower() == target
        and (market is None or str(worker.get("market") or "").lower() == market)
    )
    if not state_matches and not worker_matches:
        return {"ok": True, "stopped": False, "note": "No matching account worker was active."}

    stopped = demo_worker_supervisor.stop(timeout=30.0)
    if not stopped.get("ok"):
        emergency_state = core_service.set_state(db, emergency_stop=True)
        return {
            "ok": False, "stopped": False, "worker": stopped,
            "state": emergency_state,
            "error": "account_worker_did_not_stop_credentials_retained",
        }
    core_service.stop_bot(db, reason=reason)
    return {"ok": True, "stopped": True, "worker": stopped}


async def binance_testnet_delete(request):
    auth_error = _binance_auth_error(request)
    if auth_error:
        return auth_error
    market = request.query_params.get("market")
    if market is not None and market not in {"spot", "futures"}:
        return J({"ok": False, "error": "market_must_be_spot_or_futures"}, 400)
    db = db_sess()
    try:
        worker_stop = _stop_worker_before_credential_deletion(
            db, "testnet", market, "delete_testnet_credentials",
        )
        if not worker_stop.get("ok"):
            return J({**worker_stop, "deleted": False}, 503)
        result = delete_testnet_credentials(market)
        return J({**result, "worker": worker_stop})
    except Exception as e:
        db.rollback()
        return J({"ok": False, "error": str(e)[:240]}, 400)
    finally:
        db.close()

async def binance_testnet_creds_status(request):
    auth_error = _binance_auth_error(request)
    if auth_error:
        return auth_error
    market = request.query_params.get("market", "spot")
    if market not in {"spot", "futures"}:
        return J({"ok": False, "error": "unsupported_testnet_market"}, 400)
    creds = load_testnet_credentials(market)
    return J({
        "configured": creds.get("configured", False),
        "market": market,
        "environment": "testnet",
        "has_secret": bool(creds.get("configured")),
    })

async def binance_live_save(request):
    auth_error = _binance_auth_error(request)
    if auth_error:
        return auth_error
    try:
        payload = await body(request)
        api_key = str(payload.get("api_key") or "").strip()
        api_secret = str(payload.get("api_secret") or "").strip()
        market = str(payload.get("market") or "spot").strip().lower()
        if market not in {"spot", "futures"}:
            return J({"ok": False, "saved": False, "error": "unsupported_live_market"}, 400)
        if not api_key or not api_secret:
            return J({"ok": False, "saved": False, "error": "api_key_and_secret_required"}, 400)
        candidate = BinanceLiveAdapter(BinanceConfig(
            enabled=True, environment="live", market=market,
            api_key_ref=api_key, api_secret_ref=api_secret,
        ))
        status = candidate.status()
        if not status.get("connected"):
            return J({"ok": False, "saved": False, "error": "live_read_only_connection_failed",
                      "live_status": status, "note": "Credentials were not stored."}, 400)
        db = db_sess()
        try:
            current = core_service.get_state(db)
            active_states = {"starting", "running", "analyzing", "executing", "waiting_data"}
            if current.get("bot_run_state") in active_states:
                from app.services.demo_worker_supervisor import demo_worker_supervisor
                stopped = demo_worker_supervisor.stop(timeout=30.0)
                if not stopped.get("ok"):
                    # Fail closed if an existing execution worker cannot be
                    # confirmed stopped before switching the saved account.
                    core_service.set_state(db, emergency_stop=True)
                    return J({"ok": False, "saved": False, "error": "previous_worker_did_not_stop",
                              "worker": stopped, "live_status": status,
                              "note": "Live credentials were not stored; emergency stop was latched."}, 503)
                core_service.stop_bot(db, reason="switch_account_to_live_read_only")
            save_live_credentials(api_key, api_secret, market)
            # Selecting Live only enables account reading/analysis. It does not
            # confirm Live trading and does not start the execution worker.
            core_service.set_state(db, account_target="live", market_type=market, live_confirm=False)
            from app.models.entities import SystemState
            state_row = db.get(SystemState, 1)
            if state_row is None:
                return J({"ok": False, "saved": True, "error": "system_state_missing",
                          "note": "Live credentials are saved, but the read-only analysis worker could not start."}, 503)
            state_row.bot_run_state = "starting"
            state_row.autonomous_enabled = False
            state_row.bot_stop_reason = "live_read_only_observer_starting"
            db.add(state_row)
            db.commit()
            from app.services.demo_worker_supervisor import demo_worker_supervisor
            observer = demo_worker_supervisor.start(
                market=market, account_target="live", interval_seconds=60,
            )
            if not observer.get("ok"):
                state_row.bot_run_state = "stopped"
                state_row.autonomous_enabled = False
                state_row.live_confirmed_by_user = False
                state_row.bot_stop_reason = observer.get("error", "live_read_only_observer_start_failed")
                state_row.bot_stopped_at = datetime.now(timezone.utc)
                db.add(state_row)
                db.commit()
            return J({"ok": True, "saved": True, "observer_started": bool(observer.get("ok")),
                      "worker": observer, "live_status": status,
                      "state": core_service.get_state(db),
                      "live_trading_started": False,
                      "note": ("Live account connected. TITAN is reading the account and analyzing the market in read-only mode. Live order execution is hard-disabled in this release."
                               if observer.get("ok") else
                               "Live credentials are saved, but the read-only analysis worker did not start. The bot remains stopped; inspect the worker error before retrying.")})
        finally:
            db.close()
    except Exception as exc:
        return J({"ok": False, "saved": False, "error": str(exc)[:240]}, 400)

async def binance_live_creds_status(request):
    auth_error = _binance_auth_error(request)
    if auth_error:
        return auth_error
    market = request.query_params.get("market", "spot")
    if market not in {"spot", "futures"}:
        return J({"ok": False, "error": "unsupported_live_market"}, 400)
    creds = load_binance_credentials("live", market)
    db = db_sess()
    try:
        state = core_service.get_state(db)
        configured = bool(creds.get("configured"))
        server_live_enabled = bool(settings.live_trading_enabled)
        user_live_confirmed = bool(state.get("live_confirmed_by_user"))
        return J({
            "configured": configured, "market": market,
            "environment": "live", "has_secret": configured,
            "server_live_enabled": server_live_enabled,
            "user_live_confirmed": user_live_confirmed,
            "live_start_required": not user_live_confirmed,
            "live_blocked": not (configured and server_live_enabled and user_live_confirmed),
        })
    finally:
        db.close()


async def binance_live_delete(request):
    auth_error = _binance_auth_error(request)
    if auth_error:
        return auth_error
    market = request.query_params.get("market")
    if market not in {"spot", "futures"}:
        return J({"ok": False, "error": "market_must_be_spot_or_futures"}, 400)
    db = db_sess()
    try:
        worker_stop = _stop_worker_before_credential_deletion(
            db, "live", market, "delete_live_credentials",
        )
        if not worker_stop.get("ok"):
            return J({**worker_stop, "deleted": False}, 503)
        result = delete_live_credentials(market)
        return J({**result, "worker": worker_stop})
    except Exception as exc:
        db.rollback()
        return J({"ok": False, "error": str(exc)[:240]}, 400)
    finally:
        db.close()


async def engines_list_detailed(request: Request):
    db = db_sess()
    try:
        user, err = require_user(request, db)
        if err: return err
        return J({"engines": core_service.get_engines_detailed(db)})
    finally:
        db.close()


async def engine_control(request: Request):
    db = db_sess()
    try:
        user, err = require_user(request, db)
        if err: return err
        p = await body(request)
        key = request.path_params.get("engine_key") or p.get("key")
        action = p.get("action") or request.query_params.get("action")
        res = core_service.control_engine(db, key, action, user=user.email if user else "system")
        return J(res)
    except Exception as e:
        return J({"detail": str(e)}, 400)
    finally:
        db.close()


async def export_csv(request):
    import csv, io
    from starlette.responses import Response
    db = db_sess()
    try:
        user, err = require_user(request, db)
        if err and auth_service.auth_required(): return err
        kind = request.query_params.get("kind","orders")
        env = request.query_params.get("environment_mode","demo"); market = request.query_params.get("market_type","spot")
        buf = io.StringIO(); w = csv.writer(buf)
        if kind == "positions":
            rows = db.execute(select(PositionRecord).where(PositionRecord.environment_mode==env, PositionRecord.market_type==market)).scalars().all()
            w.writerow(["id","symbol","side","quantity","entry_avg_price","mark_price","unrealized_pnl","realized_pnl","fees_total","status","shadow"])
            for r in rows: w.writerow([r.id,r.symbol,r.side,r.quantity,r.entry_avg_price,r.mark_price,r.unrealized_pnl,r.realized_pnl,r.fees_total,r.status,r.shadow])
        else:
            rows = db.execute(select(OrderRecord).where(OrderRecord.environment_mode==env, OrderRecord.market_type==market)).scalars().all()
            w.writerow(["id","symbol","side","order_type","quantity","filled_quantity","price","avg_fill_price","status","fees_paid","shadow","idempotency_key"])
            for r in rows: w.writerow([r.id,r.symbol,r.side,r.order_type,r.quantity,r.filled_quantity,r.price,r.avg_fill_price,r.status,r.fees_paid,r.shadow,r.idempotency_key])
        return Response(buf.getvalue(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{kind}_{env}_{market}.csv"'})
    finally:
        db.close()

routes = [
    Route("/", connect_page),
    Route("/connect", connect_page),
    Route("/api/health", health),
    Route("/api/system/status", system_status),
    Route("/api/system/state", update_state, methods=["POST"]),
    Route("/api/scanner/spot", scanner_spot),
    Route("/api/scanner/futures", scanner_futures),
    Route("/api/scanner/smart", smart_scanner),
    Route("/api/scanner/smart-futures", smart_scanner_futures),
    Route("/api/fundamentals", fundamental_analysis),
    Route("/api/fundamentals/protocol", protocol_fundamental_analysis),
    Route("/api/economic-calendar", economic_calendar),
    Route("/api/token-unlocks", token_unlocks),
    Route("/api/news", news_analysis),
    Route("/api/backtest/strategy", backtest_strategy, methods=["POST"]),
    Route("/api/backtest/compare", compare_strategy_backtests, methods=["POST"]),
    Route("/api/backtest/risk-simulation", risk_simulation_backtest, methods=["POST"]),
    Route("/api/backtest/portfolio", portfolio_backtest, methods=["POST"]),
    Route("/api/backtest/futures", futures_backtest, methods=["POST"]),
    Route("/api/backtest/specialized", specialized_backtest, methods=["POST"]),
    Route("/api/backtest/orderflow/binance", binance_orderflow_backtest, methods=["POST"]),
    Route("/api/backtest/arbitrage/binance", binance_triangular_arbitrage_backtest, methods=["POST"]),
    Route("/api/orderbook/capture/triangle", capture_triangle_orderbook, methods=["POST"]),
    Route("/api/orderbook/capture/futures", capture_futures_orderbook, methods=["POST"]),
    Route("/api/backtest/orderflow/futures-orderbook", historical_futures_orderbook_backtest, methods=["POST"]),
    Route("/api/backtest/arbitrage/orderbook", historical_orderbook_arbitrage_backtest, methods=["POST"]),
    Route("/api/market/microstructure", market_microstructure),
    Route("/api/arbitrage/triangle", arbitrage_analysis, methods=["POST"]),
    Route("/api/performance", performance_analytics),
    Route("/api/telegram/status", telegram_status),
    Route("/api/telegram/poll", telegram_poll),
    Route("/api/telegram/send", telegram_send, methods=["POST"]),
    Route("/api/predictions", prediction_evaluations),
    Route("/api/predictions/evaluate", evaluate_predictions, methods=["POST"]),
    Route("/api/ml/train", ml_train, methods=["POST"]),
    Route("/api/ml/predict", ml_predict, methods=["POST"]),
    Route("/api/ml/drift", ml_drift, methods=["POST"]),
    Route("/api/ml/models", ml_models),
    Route("/api/strategies", strategies),
    Route("/api/analysis/decision", analyze, methods=["POST"]),
    Route("/api/decisions", list_decisions),
    Route("/api/audit", list_audit),
    Route("/api/knowledge", knowledge_list),
    Route("/api/knowledge", knowledge_add, methods=["POST"]),
    Route("/api/knowledge/verify", knowledge_verify, methods=["POST"]),
    Route("/api/knowledge/concepts", knowledge_concepts),
    Route("/api/learning", learning_list),
    Route("/api/learning/strategies/{strategy_key:str}/outcomes", strategy_trade_outcomes),
    Route("/api/learning/strategy-versions", strategy_versions),
    Route("/api/learning/strategy-versions", strategy_version_create, methods=["POST"]),
    Route("/api/learning/strategy-versions/rollback", strategy_version_rollback, methods=["POST"]),
    Route("/api/learning/strategy-proposals", strategy_proposal_create, methods=["POST"]),
    Route("/api/learning/strategy-proposals/test", strategy_proposal_test, methods=["POST"]),
    Route("/api/learning/strategy-proposals/walk-forward", strategy_proposal_walk_forward, methods=["POST"]),
    Route("/api/learning/strategy-proposals/historical-walk-forward", strategy_proposal_historical_walk_forward, methods=["POST"]),
    Route("/api/learning/strategy-proposals/apply-paper", strategy_proposal_apply_paper, methods=["POST"]),
    Route("/api/learning", learning_add, methods=["POST"]),
    Route("/api/auth/status", auth_status),
    Route("/api/auth/login", auth_login, methods=["POST"]),
    Route("/api/auth/2fa/setup", auth_2fa_setup, methods=["POST"]),
    Route("/api/auth/2fa/confirm", auth_2fa_confirm, methods=["POST"]),
    Route("/api/auth/2fa/disable", auth_2fa_disable, methods=["POST"]),
    Route("/api/auth/logout", auth_logout, methods=["POST"]),
    Route("/api/auth/me", auth_me),
    Route("/api/config/changes", config_changes),
    Route("/api/chat", chat, methods=["POST"]),
    Route("/api/chat/reports", chat_reports),
    Route("/api/ai/providers", ai_providers),
    Route("/api/ai/providers", ai_provider_create, methods=["POST"]),
    Route("/api/ai/providers/{provider_id:int}/test", ai_provider_test, methods=["POST"]),
    Route("/api/ai/providers/{provider_id:int}/control", ai_provider_control, methods=["POST"]),
    Route("/api/ai/research", ai_research),
    Route("/api/compliance/check-order", compliance_check, methods=["POST"]),
    Route("/api/demo/cycle", demo_cycle, methods=["POST"]),
    Route("/api/master/start", master_start, methods=["POST"]),
    Route("/api/master/trading-pause", master_trading_pause, methods=["POST"]),
    Route("/api/master/stop", master_stop, methods=["POST"]),
    Route("/api/master/emergency-stop", master_emergency, methods=["POST"]),
    Route("/api/master/emergency-reset", master_emergency_reset, methods=["POST"]),
    Route("/api/shadow/order", shadow_order, methods=["POST"]),
    Route("/api/portfolio", portfolio),
    Route("/api/export/csv", export_csv),
    Route("/api/export/formats", export_formats),
    Route("/api/self-diagnosis", self_diagnosis),
    Route("/api/backup/info", backup_info),
    Route("/download", download_zip),
    Route("/api/shadow/close", shadow_close, methods=["POST"]),
    Route("/api/shadow/protection", shadow_position_protection, methods=["POST"]),
    Route("/api/engines", engines_list_detailed),
    Route("/api/engines/detailed", engines_list_detailed),
    Route("/api/engines/{engine_key}/control", engine_control, methods=["POST"]),
    Route("/api/engine/control", engine_control, methods=["POST"]),
    Route("/api/binance/live/status", binance_live_status),
    Route("/api/binance/live/creds", binance_live_creds_status),
    Route("/api/binance/live/save", binance_live_save, methods=["POST"]),
    Route("/api/binance/live/delete", binance_live_delete, methods=["POST"]),
    Route("/api/binance/execution/preflight", binance_execution_preflight),
    Route("/api/binance/testnet/status", binance_testnet_status),
    Route("/api/binance/testnet/validate-trade-permission", binance_testnet_validate_trade_permission, methods=["POST"]),
    Route("/api/binance/testnet/futures/maintenance-brackets", binance_testnet_futures_maintenance_brackets),
    Route("/api/binance/testnet/creds", binance_testnet_creds_status),
    Route("/api/binance/testnet/save", binance_testnet_save, methods=["POST"]),
    Route("/api/binance/testnet/delete", binance_testnet_delete, methods=["POST"]),
    Route("/api/binance/testnet/order", binance_testnet_order, methods=["POST"]),
    Route("/api/binance/orders/cancel", binance_order_cancel, methods=["POST"]),
    Route("/api/binance/testnet/close", binance_testnet_close, methods=["POST"]),
    Route("/api/binance/testnet/account", binance_testnet_account),
    Route("/api/binance/testnet/orders", binance_testnet_orders),
    Route("/api/binance/testnet/reconcile", binance_testnet_reconcile),
]


from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app):
    dbmod.init_db()
    db = dbmod.SessionLocal()
    try:
        core_service.ensure_bootstrap(db)
        # Reconcile persisted runtime flags with this process: worker threads are in-memory only.
        from app.services.restart_recovery import recover_interrupted_worker
        recover_interrupted_worker(db)
        # Restore the latest persisted model for each symbol/timeframe key.
        from app.engines.hybrid.ml_engine import ml_engine
        model_rows = db.execute(
            select(LearningRecord).where(LearningRecord.category == "ml_model")
            .order_by(desc(LearningRecord.id)).limit(200)
        ).scalars().all()
        loaded_keys = set()
        for row in model_rows:
            model_payload = (row.evidence or {}).get("model")
            if not isinstance(model_payload, dict):
                continue
            model_key = str(model_payload.get("model_key") or row.subject)
            if model_key in loaded_keys:
                continue
            if ml_engine.load_model(model_payload).get("ok"):
                loaded_keys.add(model_key)
    finally:
        db.close()
    try:
        yield
    finally:
        from app.services.demo_worker_supervisor import demo_worker_supervisor
        demo_worker_supervisor.stop(timeout=30.0)

def create_star_app() -> Starlette:
    app = Starlette(routes=routes, lifespan=lifespan)
    frontend_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if frontend_dist.is_dir():
        # API routes are registered first; this mount serves Vite assets,
        # PWA manifest, service worker and icons from the same origin.
        app.routes.append(Mount("/", app=StaticFiles(directory=str(frontend_dist), html=True), name="frontend"))
    app.add_middleware(SecurityHeadersMiddleware)
    configured_origins = (settings.allowed_origins or "").strip()
    if settings.app_env.strip().lower() in {"production", "prod"} and configured_origins == "*":
        # Fail closed in production: same-origin works without CORS; deployers
        # must explicitly allowlist any separately hosted frontend origin.
        origins = []
    else:
        origins = [origin.strip() for origin in configured_origins.split(",") if origin.strip()]
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=False, allow_methods=["*"], allow_headers=["*"])
    return app

def _startup():
    # kept for compatibility
    pass


app = create_star_app()