"""Provider registry. Adding a provider means registering an adapter."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.providers.anthropic_provider import AnthropicProvider
from app.providers.compatible import CompatibleProvider, compatible_base_url
from app.providers.cursor_provider import CursorProvider
from app.providers.gemini_provider import GeminiProvider
from app.providers.local import DeterministicProvider, FixtureProvider
from app.providers.meta_provider import MetaProvider
from app.providers.openai_provider import OpenAIProvider
from app.providers.types import ProviderError

PROVIDERS = {
    "openai": OpenAIProvider,
    "anthropic": AnthropicProvider,
    "cursor": CursorProvider,
    "gemini": GeminiProvider,
    "meta": MetaProvider,
    "deterministic": DeterministicProvider,
    "fixture": FixtureProvider,
}

_LOCAL = {"deterministic", "fixture", "manual_json"}


def get_provider(provider_id: str, session: Session | None = None):
    if provider_id == "openai_compatible":
        base = compatible_base_url(session) if session is not None else ""
        return CompatibleProvider(base)
    cls = PROVIDERS.get(provider_id)
    if cls is None:
        raise ProviderError(f"unknown provider {provider_id}")
    return cls()


def provider_ids() -> list[str]:
    return [*PROVIDERS.keys(), "openai_compatible", "manual_json"]


def run_transport(provider_id: str, config: dict | None) -> str:
    saved = config or {}
    if provider_id == "anthropic":
        transport = str(saved.get("anthropic_transport") or "api")
        return transport if transport in {"api", "claude_code"} else "api"
    if provider_id == "cursor":
        transport = str(saved.get("cursor_transport") or "login")
        return transport if transport in {"login", "api_key"} else "login"
    return "api"


def needs_secret(provider_id: str, transport: str = "api") -> bool:
    if provider_id in _LOCAL:
        return False
    if provider_id == "anthropic" and transport == "claude_code":
        return False
    if provider_id == "cursor" and transport == "login":
        return False
    return True
