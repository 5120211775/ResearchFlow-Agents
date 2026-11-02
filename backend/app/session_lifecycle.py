"""The session status write a user's stop has to win against — one home for both hosts."""

from __future__ import annotations

import uuid

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.session import Session, SessionStatus


async def mark_running(db: AsyncSession, session_id: uuid.UUID) -> bool:
    """Move the session to RUNNING unless the user has stopped it. True if it moved.

    The condition is part of the UPDATE rather than a read before it, so a stop that
    commits between the driver loading the row and this write still wins. The loaded
    object is deliberately left alone — a caller that needs the status afterwards
    re-reads the row.
    """
    result = await db.execute(
        update(Session)
        .where(Session.id == session_id, Session.cancelled_at.is_(None))
        .values(status=SessionStatus.RUNNING)
        .execution_options(synchronize_session=False)
    )
    return result.rowcount == 1
