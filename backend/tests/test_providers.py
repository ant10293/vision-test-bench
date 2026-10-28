"""Provider capability routing refuses unsupported modes instead of substituting them."""

import pytest
from app.providers.anthropic_provider import AnthropicProvider
from app.providers.catalog import lookup
from app.providers.openai_provider import OpenAIProvider
from app.providers.types import GenerationRequest, UnsupportedMode, estimate_cost


def test_openai_catalog_has_no_native_video():
    caps = lookup("openai", "gpt-4.1")
    assert caps.image is True
    assert caps.native_video is False
    assert caps.structured_output is True


def test_video_capable_models_are_not_sent_frames():
    from app.providers.model_list import use_video_file

    assert use_video_file("gemini", "gemini-2.5-pro") is True
    assert use_video_file("meta", "muse-spark-1.3") is True
    assert use_video_file("anthropic", "claude-sonnet-5-5") is False
    assert use_video_file("openai", "gpt-4.1") is False


def test_overlong_video_is_not_replaced_with_frames():
    from types import SimpleNamespace

    from app.pipeline.infer import _require_video_length

    run = SimpleNamespace(provider_id="gemini", model_id="gemini-2.5-flash")
    video = SimpleNamespace(duration_sec=4000)
    with pytest.raises(UnsupportedMode, match="not replaced with frames"):
        _require_video_length(run, video)


def test_openai_refuses_native_video():
    provider = OpenAIProvider()
    request = GenerationRequest(model_id="gpt-4.1", system_prompt="s", user_prompt="u", modality="native_video")
    with pytest.raises(UnsupportedMode):
        import asyncio

        asyncio.run(provider.generate("sk-test", request))


def test_anthropic_sends_reasoning_effort(monkeypatch):
    captured: dict = {}

    class _Usage:
        input_tokens = 3
        output_tokens = 2

    class _Block:
        type = "tool_use"
        name = "record_analysis"
        input = {"ok": True}

    class _Response:
        content = [_Block()]
        usage = _Usage()
        id = "msg_1"

        def model_dump(self):
            return {"id": "msg_1", "model": "claude-sonnet-5-5", "usage": {}, "stop_reason": "tool_use"}

    class _Messages:
        async def create(self, **kwargs):
            captured.update(kwargs)
            return _Response()

    class _Client:
        messages = _Messages()

    monkeypatch.setattr(AnthropicProvider, "_client", lambda self, secret, timeout: _Client())
    request = GenerationRequest(
        model_id="claude-sonnet-5-5",
        system_prompt="s",
        user_prompt="u",
        reasoning_effort="xhigh",
    )
    import asyncio

    asyncio.run(AnthropicProvider().generate("sk-test", request))
    assert captured["output_config"] == {"effort": "xhigh"}


def test_anthropic_refuses_effort_the_model_does_not_list():
    request = GenerationRequest(
        model_id="claude-sonnet-4-6",
        system_prompt="s",
        user_prompt="u",
        reasoning_effort="xhigh",
    )
    with pytest.raises(UnsupportedMode, match="not available"):
        import asyncio

        asyncio.run(AnthropicProvider().generate("sk-test", request))


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


def test_estimate_cost_uses_catalog_list_rates():
    caps = lookup("openai", "gpt-4.1")
    cost, known = estimate_cost(caps, 1000, 500)
    assert known is True
    assert cost == 0.006


def test_unpriced_model_does_not_invent_a_zero_invoice():
    caps = lookup("meta", "muse-spark-1.3")
    cost, known = estimate_cost(caps, 1000, 500)
    assert known is False
    assert cost == 0.0
