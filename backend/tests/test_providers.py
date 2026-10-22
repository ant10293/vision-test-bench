"""Provider capability routing refuses unsupported modes instead of substituting them."""

import pytest
from app.providers.anthropic_provider import AnthropicProvider
from app.providers.catalog import lookup
from app.providers.openai_provider import OpenAIProvider
from app.providers.types import GenerationRequest, UnsupportedMode


def test_openai_catalog_has_no_native_video():
    caps = lookup("openai", "gpt-4.1")
    assert caps.image is True
    assert caps.native_video is False
    assert caps.structured_output is True


def test_openai_refuses_native_video():
    provider = OpenAIProvider()
    request = GenerationRequest(model_id="gpt-4.1", system_prompt="s", user_prompt="u", modality="native_video")
    with pytest.raises(UnsupportedMode):
        import asyncio

        asyncio.run(provider.generate("sk-test", request))


def test_anthropic_refuses_native_video():
    provider = AnthropicProvider()
    request = GenerationRequest(
        model_id="claude-sonnet-4-5",
        system_prompt="s",
        user_prompt="u",
        modality="native_video",
    )
    with pytest.raises(UnsupportedMode):
        import asyncio

        asyncio.run(provider.generate("sk-test", request))


def test_muse_rejects_temperature_capability():
    caps = lookup("meta", "muse-spark-1.3")
    assert caps.native_video is True
    assert caps.supports_temperature is False
    assert "none" not in caps.reasoning_efforts
    assert "high" in caps.reasoning_efforts


def test_sam_is_not_an_analysis_model():
    caps = lookup("meta", "sam-3.1")
    assert caps.structured_output is False
    assert "action" not in caps.notes.lower() or "not an action" in caps.notes.lower()
