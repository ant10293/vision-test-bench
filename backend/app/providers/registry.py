"""Provider registry. Adding a provider means registering an adapter."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.providers.anthropic_provider import AnthropicProvider
from app.providers.compatible import CompatibleProvider, compatible_base_url
from app.providers.gemini_provider import GeminiProvider
from app.providers.local import DeterministicProvider, FixtureProvider
from app.providers.meta_provider import MetaProvider
from app.providers.openai_provider import OpenAIProvider
from app.providers.types import ProviderError

PROVIDERS = {
    "openai": OpenAIProvider,
    "anthropic": AnthropicProvider,
    "gemini": GeminiProvider,
    "meta": MetaProvider,
    "deterministic": DeterministicProvider,
    "fixture": FixtureProvider,
}


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
