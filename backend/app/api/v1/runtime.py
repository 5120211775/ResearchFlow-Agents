"""Read-only, credential-free runtime identity for the UI."""

from fastapi import APIRouter, Depends

from app.config import settings
from app.dependencies import get_current_user
from app.models.user import User
from app.services.model_routing import resolve

router = APIRouter()


@router.get("/runtime", tags=["Configuration"])
async def runtime_identity(user: User = Depends(get_current_user)):
    return {
        "llm_mode": settings.llm_mode,
        "effective_routing": resolve(session_routing=None, user_routing=user.model_routing),
        "embeddings_provider": settings.embeddings_provider,
        "embeddings_model": settings.embeddings_model,
        "max_input_tokens": settings.max_input_tokens,
        "max_parallel_tasks": settings.max_parallel_tasks,
        "max_critic_loops": settings.max_critic_loops,
        "max_wallclock_seconds": settings.max_wallclock_seconds,
    }
