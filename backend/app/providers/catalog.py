"""Static capability catalog.

Prices are approximate public list rates stored so a run can record an estimate.
They are not invoices. Unknown models stay unpriced.
"""

from __future__ import annotations

from app.providers.types import ModelCapabilities

_MUSE_NOTE = (
    "Muse Spark accepts text, image, and video on the Meta Responses API. "
    "Reasoning effort 'none' is rejected. Audio input on 1.3 is degraded; this lab does not send audio."
)
_SAM_NOTE = (
    "SAM 3.1 returns boxes and masks. It is not an action-recognition model and is never "
    "substituted for a scene-understanding model."
)
_OPENAI_NOTE = (
    "Public Responses API accepts images, not video files. Native video stays disabled "
    "unless a future capability record explicitly enables it for that model id."
)
_ANTHROPIC_NOTE = (
    "Claude vision is an image API. Native video is disabled because current Messages API "
    "documentation does not accept video files. Long sequences are split into ordered windows."
)

MUSE_EFFORTS = ("minimal", "low", "medium", "high", "xhigh", "max")


def _caps(**kwargs) -> ModelCapabilities:
    return ModelCapabilities(**kwargs)


CATALOG: dict[tuple[str, str], ModelCapabilities] = {
    ("openai", "gpt-4.1"): _caps(
        model_id="gpt-4.1",
        provider_id="openai",
        image=True,
        structured_output=True,
        max_images=16,
        context_tokens=1_047_576,
        supports_temperature=True,
        input_price_per_mtok=2.0,
        output_price_per_mtok=8.0,
        notes=_OPENAI_NOTE,
    ),
    ("openai", "gpt-4.1-mini"): _caps(
        model_id="gpt-4.1-mini",
        provider_id="openai",
        image=True,
        structured_output=True,
        max_images=16,
        context_tokens=1_047_576,
        input_price_per_mtok=0.4,
        output_price_per_mtok=1.6,
        notes=_OPENAI_NOTE,
    ),
    ("openai", "gpt-4o"): _caps(
        model_id="gpt-4o",
        provider_id="openai",
        image=True,
        structured_output=True,
        max_images=16,
        context_tokens=128_000,
        input_price_per_mtok=2.5,
        output_price_per_mtok=10.0,
        notes=_OPENAI_NOTE,
    ),
    ("anthropic", "claude-sonnet-4-5"): _caps(
        model_id="claude-sonnet-4-5",
        provider_id="anthropic",
        image=True,
        structured_output=True,
        max_images=20,
        context_tokens=200_000,
        input_price_per_mtok=3.0,
        output_price_per_mtok=15.0,
        notes=_ANTHROPIC_NOTE,
    ),
    ("anthropic", "claude-opus-4-1"): _caps(
        model_id="claude-opus-4-1",
        provider_id="anthropic",
        image=True,
        structured_output=True,
        max_images=20,
        context_tokens=200_000,
        input_price_per_mtok=15.0,
        output_price_per_mtok=75.0,
        notes=_ANTHROPIC_NOTE,
    ),
    ("anthropic", "claude-haiku-4-5"): _caps(
        model_id="claude-haiku-4-5",
        provider_id="anthropic",
        image=True,
        structured_output=True,
        max_images=20,
        context_tokens=200_000,
        input_price_per_mtok=1.0,
        output_price_per_mtok=5.0,
        notes=_ANTHROPIC_NOTE,
    ),
    ("gemini", "gemini-2.5-flash"): _caps(
        model_id="gemini-2.5-flash",
        provider_id="gemini",
        image=True,
        native_video=True,
        structured_output=True,
        max_images=16,
        max_video_seconds=3600,
        context_tokens=1_048_576,
        input_price_per_mtok=0.3,
        output_price_per_mtok=2.5,
        notes="Native video uses the Gemini Files API and is polled until ACTIVE. Frame mode is a separate modality.",
    ),
    ("gemini", "gemini-2.5-pro"): _caps(
        model_id="gemini-2.5-pro",
        provider_id="gemini",
        image=True,
        native_video=True,
        structured_output=True,
        max_images=16,
        max_video_seconds=3600,
        context_tokens=1_048_576,
        input_price_per_mtok=1.25,
        output_price_per_mtok=10.0,
        notes="Native video uses the Gemini Files API and is polled until ACTIVE. Frame mode is a separate modality.",
    ),
    ("meta", "muse-spark-1.3"): _caps(
        model_id="muse-spark-1.3",
        provider_id="meta",
        image=True,
        native_video=True,
        structured_output=True,
        max_images=16,
        context_tokens=1_048_576,
        reasoning_efforts=MUSE_EFFORTS,
        supports_temperature=False,
        input_price_per_mtok=None,
        output_price_per_mtok=None,
        notes=_MUSE_NOTE,
    ),
    ("meta", "muse-spark-1.2"): _caps(
        model_id="muse-spark-1.2",
        provider_id="meta",
        image=True,
        native_video=True,
        structured_output=True,
        max_images=16,
        context_tokens=1_048_576,
        reasoning_efforts=("minimal", "low", "medium", "high", "xhigh"),
        supports_temperature=False,
        notes=_MUSE_NOTE + " 1.2 does not advertise the max effort level.",
    ),
    ("meta", "muse-spark-1.1"): _caps(
        model_id="muse-spark-1.1",
        provider_id="meta",
        image=True,
        native_video=True,
        structured_output=True,
        max_images=16,
        context_tokens=1_048_576,
        reasoning_efforts=("low", "medium", "high"),
        supports_temperature=False,
        notes=_MUSE_NOTE,
    ),
    ("meta", "sam-3.1"): _caps(
        model_id="sam-3.1",
        provider_id="meta",
        image=True,
        native_video=True,
        structured_output=False,
        max_images=1,
        max_video_seconds=600,
        supports_temperature=False,
        notes=_SAM_NOTE,
    ),
    ("deterministic", "motion-baseline"): _caps(
        model_id="motion-baseline",
        provider_id="deterministic",
        image=True,
        structured_output=True,
        max_images=10_000,
        supports_temperature=False,
        notes="Local optical-flow and background baseline. Not a vision-language model.",
    ),
    ("fixture", "authored-fixture"): _caps(
        model_id="authored-fixture",
        provider_id="fixture",
        image=True,
        structured_output=True,
        supports_temperature=False,
        notes="Returns a checksum-locked authored analysis. Refuses unknown videos. Not model output.",
    ),
    ("manual_json", "imported-json"): _caps(
        model_id="imported-json",
        provider_id="manual_json",
        text=True,
        structured_output=True,
        supports_temperature=False,
        notes="User-supplied JSON. Not a model call.",
    ),
}


def lookup(provider_id: str, model_id: str) -> ModelCapabilities:
    known = CATALOG.get((provider_id, model_id))
    if known:
        return known
    return _default_manual(provider_id, model_id)


def _default_manual(provider_id: str, model_id: str) -> ModelCapabilities:
    native = provider_id in {"gemini", "meta"} and not model_id.startswith("sam")
    temperature = provider_id not in {"meta"}
    return ModelCapabilities(
        model_id=model_id,
        provider_id=provider_id,
        image=provider_id not in {"manual_json"},
        native_video=native,
        structured_output=provider_id != "meta" or not model_id.startswith("sam"),
        max_images=16 if provider_id != "anthropic" else 20,
        supports_temperature=temperature,
        notes=(
            "Manually added model id. Capabilities are a conservative default, not a live "
            "provider capability record. Native video is not enabled for OpenAI or Anthropic defaults."
        ),
    )


def models_for(provider_id: str) -> list[ModelCapabilities]:
    return [caps for (provider, _model), caps in CATALOG.items() if provider == provider_id]
