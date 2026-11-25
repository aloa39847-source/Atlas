from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models.entities import AuditLog


def log_event(
    db: Session,
    *,
    action: str,
    message: str = "",
    actor: str = "system",
    entity_type: str | None = None,
    entity_id: str | None = None,
    environment_mode: str | None = None,
    market_type: str | None = None,
    severity: str = "info",
    details: dict[str, Any] | None = None,
) -> AuditLog:
    row = AuditLog(
        actor=actor,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        environment_mode=environment_mode,
        market_type=market_type,
        severity=severity,
        message=message,
        details=details or {},
    )
    db.add(row)
    db.flush()
    return row
