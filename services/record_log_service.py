from sqlalchemy.orm import Session
from models.record_log import RecordLog


def add_log(
    db: Session,
    entity_type: str,
    entity_id: int,
    action: str,
    actor_id: int | None = None,
    old_value: str | None = None,
    new_value: str | None = None,
) -> None:
    """Add one audit row. The caller owns the commit."""
    db.add(RecordLog(
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        actor_id=actor_id,
        old_value=str(old_value) if old_value is not None else None,
        new_value=str(new_value) if new_value is not None else None,
    ))
