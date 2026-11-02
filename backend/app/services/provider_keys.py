"""Load a user's encrypted provider connection with a server-key fallback."""

import uuid
import structlog
from sqlalchemy import select
from app.models.user import User
from app.services import crypto

logger = structlog.get_logger()


async def _user_provider_keys(db, user_id: str) -> dict[str, str]:
    """This user's BYOK provider key, if they've set one and it still decrypts.

    Returns {provider: key} or {} to fall back to the server's key. `crypto.
    user_provider_keys` treats "no key stored" and "stored but undecryptable" alike —
    both degrade to `{}` — but only the latter is worth a log line: a rotated signing
    secret is an operator's problem, and a user who never set a key is not.
    """
    user = (
        await db.execute(select(User).where(User.id == uuid.UUID(user_id)))
    ).scalar_one_or_none()
    if user is None:
        return {}
    keys = crypto.user_provider_keys(user)
    if not keys and user.api_key_encrypted and user.api_key_provider:
        logger.warning("byok_key_undecryptable", user_id=user_id, provider=user.api_key_provider)
    return keys
