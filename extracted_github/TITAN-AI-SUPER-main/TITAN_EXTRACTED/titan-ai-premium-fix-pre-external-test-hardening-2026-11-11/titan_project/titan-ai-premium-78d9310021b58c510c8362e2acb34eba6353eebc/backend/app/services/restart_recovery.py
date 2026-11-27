from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.entities import SystemState


ACTIVE_STATES = {"starting", "running", "analyzing", "executing", "waiting_data"}


def recover_interrupted_worker(db: Session) -> dict:
    """Fail closed after process restart; never pretend an in-memory worker survived."""
    state = db.get(SystemState, 1)
    if state is None or str(state.bot_run_state or "").lower() not in ACTIVE_STATES:
        return {"recovered": False, "reason": "no_interrupted_worker"}

    target = str(getattr(state, "account_target", "demo") or "demo").lower()
    previous_state = str(state.bot_run_state)
    state.bot_run_state = "stopped"
    state.autonomous_enabled = False
    state.live_confirmed_by_user = False
    state.bot_stopped_at = datetime.now(timezone.utc)
    state.bot_stop_reason = "process_restart_recovery"
    # Live execution is hard-disabled in this release. Keep the emergency stop
    # latched on any interrupted Live-target observer state.
    if target == "live":
        state.emergency_stop = True
    summary = dict(state.status_summary or {})
    summary["restart_recovery"] = {
        "detected": True,
        "previous_state": previous_state,
        "account_target": target,
        "positions_preserved_for_reconciliation": True,
        "automatic_resume": False,
    }
    state.status_summary = summary
    db.add(state)
    db.commit()
    return {
        "recovered": True,
        "previous_state": previous_state,
        "account_target": target,
        "automatic_resume": False,
    }
