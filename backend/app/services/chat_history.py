"""The model's view of a conversation: the most recent turns, in the order they happened."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat_message import ChatMessage

#: How many turns of conversation the model is shown: enough for continuity, bounded so a
#: long thread cannot grow the prompt — and the bill — without limit. Report chat and
#: project threads have always used the same ceiling; they now read it from one place.
HISTORY_LIMIT = 20


async def recent_turns(db: AsyncSession, where, *, limit: int = HISTORY_LIMIT) -> list[ChatMessage]:
    """The newest `limit` messages matching `where`, returned oldest first.

    `where` rather than an id because the column is the only difference between the two
    conversations this serves: report chat scopes by `session_id`, project threads by
    `thread_id`, and `chat_messages` carries exactly one of them per row.
    """
    newest_first = (
        (
            await db.execute(
                select(ChatMessage)
                .where(where)
                .order_by(ChatMessage.created_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return list(reversed(newest_first))
