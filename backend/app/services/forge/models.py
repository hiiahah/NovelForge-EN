"""Model roles and exact model validation.

Roles: analysis, planning, drafting, validator, repair. Each role resolves to an
``LLMConfig`` id; when a role is not configured it falls back to the drafting
config so a project with a single model still works, but the resolution is
recorded in every pipeline run so the report shows which model did what.

AuthND Lab configuration is validated *exactly*: the model string is normalized
with the provider's own normalizer and must resolve to an allow-listed
``publisher/model_id``. Substring matches such as ``foo/kimi`` or
``kimi-k3-mini`` are rejected.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from sqlmodel import Session

from app.db.models import LLMConfig

ROLES = ("analysis", "planning", "drafting", "validator", "repair", "evaluator")

# Exact AuthND models the Lab analysis is validated for.
AUTHND_LAB_ALLOWED_MODELS: Tuple[str, ...] = (
    "moonshotai/kimi-k3",
    "moonshotai/kimi-k2-instruct",
)


def normalize_authnd_model(model_name: str) -> str:
    from app.services.ai.providers import authnd_auth

    publisher, model_id, _ = authnd_auth._normalize_model(model_name)
    return f"{publisher}/{model_id}".lower()


# Direct API providers accepted for Lab analysis; they need an API key (checked
# by the chat-model factory), no model allow list.
API_KEY_PROVIDERS: Tuple[str, ...] = ("anthropic", "openai", "openai_compatible", "google")


def validate_lab_llm_config(cfg: LLMConfig) -> Tuple[bool, str]:
    """(ok, reason). Genspark and direct API providers are accepted as-is; AuthND must match the allow list exactly."""
    provider = (cfg.provider or "").strip().lower()
    if provider == "genspark":
        return True, "genspark"
    if provider in API_KEY_PROVIDERS:
        if not (cfg.api_key or "").strip():
            return False, f"LLM configuration '{cfg.display_name or cfg.model_name}' ({provider}) has no API key"
        if not (cfg.model_name or "").strip():
            return False, f"LLM configuration '{cfg.display_name or ''}' ({provider}) has no model name"
        return True, f"{provider}/{cfg.model_name.strip()}"
    if provider not in {"authnd", "nvidia_authnd"}:
        return False, f"Lab analysis requires an AuthND, Genspark or API-key provider ({', '.join(API_KEY_PROVIDERS)}) configuration (got provider '{cfg.provider}')"
    try:
        normalized = normalize_authnd_model(cfg.model_name or "")
    except ValueError as exc:
        return False, f"Invalid AuthND model '{cfg.model_name}': {exc}"
    if normalized not in AUTHND_LAB_ALLOWED_MODELS:
        return False, f"Lab analysis requires one of {list(AUTHND_LAB_ALLOWED_MODELS)} (got '{cfg.model_name}' -> '{normalized}')"
    return True, normalized


@dataclass
class RoleResolution:
    role: str
    llm_config_id: int
    model_name: str
    provider: str
    fallback: bool

    def as_dict(self) -> Dict[str, object]:
        return dict(self.__dict__)


def resolve_roles(session: Session, roles: Dict[str, Optional[int]], *, default_llm_config_id: int) -> Dict[str, RoleResolution]:
    """Resolve each role to a concrete LLM config; unknown ids raise ValueError."""
    out: Dict[str, RoleResolution] = {}
    default = session.get(LLMConfig, int(default_llm_config_id))
    if default is None:
        raise ValueError(f"LLM configuration {default_llm_config_id} not found")
    for role in ROLES:
        cid = roles.get(role)
        if cid:
            cfg = session.get(LLMConfig, int(cid))
            if cfg is None:
                raise ValueError(f"LLM configuration {cid} for role '{role}' not found")
            out[role] = RoleResolution(role=role, llm_config_id=cfg.id, model_name=cfg.model_name, provider=cfg.provider, fallback=False)
        else:
            out[role] = RoleResolution(role=role, llm_config_id=default.id, model_name=default.model_name, provider=default.provider, fallback=True)
    return out


__all__ = ["API_KEY_PROVIDERS", "AUTHND_LAB_ALLOWED_MODELS", "ROLES", "RoleResolution", "normalize_authnd_model", "resolve_roles", "validate_lab_llm_config"]
