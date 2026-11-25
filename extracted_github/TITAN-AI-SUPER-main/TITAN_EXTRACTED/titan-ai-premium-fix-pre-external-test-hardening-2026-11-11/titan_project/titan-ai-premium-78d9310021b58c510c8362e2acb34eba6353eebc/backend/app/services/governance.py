from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models.entities import ConfigChange
from .audit import log_event


def record_config_change(
    db: Session,
    *,
    actor: str,
    scope: str,
    key: str,
    old_value: Any = None,
    new_value: Any = None,
    reason: str = "",
    approved: bool = False,
    metadata: dict[str, Any] | None = None,
) -> ConfigChange:
    row = ConfigChange(
        actor=actor, scope=scope, key=key,
        old_value=old_value if isinstance(old_value, (dict, list, type(None))) else {"value": old_value},
        new_value=new_value if isinstance(new_value, (dict, list, type(None))) else {"value": new_value},
        reason=reason, approved=approved, metadata_json=metadata or {},
    )
    db.add(row)
    log_event(db, action="config.change", actor=actor, message=f"Config change: {scope}.{key}", details={"scope": scope, "key": key, "approved": approved})
    db.flush()
    return row
