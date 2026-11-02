"""The Offline guarantee: every model call a run makes stays on this machine."""

from __future__ import annotations

from research_engine.embeddings import is_local_endpoint
from research_engine.runconfig import ROLES, RunConfig

#: The prefix every refusal starts with, so a host and a test can recognise it.
OFFLINE_UNAVAILABLE = "offline_unavailable"


def violations(cfg: RunConfig, *, custom_base_url: str = "") -> list[str]:
    """One line per role whose model would leave this machine; empty when all stay local."""
    if cfg.llm_mode == "fake":
        return []
    from research_engine.llm_factory import map_local_host

    out: list[str] = []
    for role in ROLES:
        route = cfg.model_for(role)
        provider, _, _ = route.partition(":")
        if provider == "ollama":
            endpoint = map_local_host(cfg.ollama_base_url)
            if not is_local_endpoint(endpoint):
                out.append(f"{role} uses Ollama at {endpoint}, which is not on this machine")
        elif provider == "custom":
            endpoint = map_local_host(custom_base_url) if custom_base_url else ""
            if not endpoint:
                out.append(f"{role} uses a custom endpoint with no address saved")
            elif not is_local_endpoint(endpoint):
                out.append(f"{role} uses a custom endpoint at {endpoint}, not on this machine")
        else:
            out.append(f"{role} uses {route}, a cloud provider")
    return out


def refusal(cfg: RunConfig, *, custom_base_url: str = "") -> str | None:
    """The failure message for an Offline run that cannot stay offline, or None."""
    if not cfg.offline:
        return None
    found = violations(cfg, custom_base_url=custom_base_url)
    if not found:
        return None
    return (
        f"{OFFLINE_UNAVAILABLE}: Offline research needs every model to run on this computer. "
        + "; ".join(found)
        + ". Choose local models in Settings → Models, or turn Offline off."
    )
