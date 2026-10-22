"""SAM 3.1 segmentation adapter.

Parses the documented special-token grammar for boxes. Mask rasters are decoded
with meta_sam_parser when that package is installed; otherwise the raw payload is kept.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

from openai import AsyncOpenAI

from app.providers.common import data_url, translate
from app.providers.meta_provider import META_BASE
from app.providers.types import ProviderError

BOX_RE = re.compile(
    r"(?P<id>\d+)<\|box;(?P<body>[^|]+)\|><\|mask;(?P<mask>[^|]+)\|>"
)
FRAME_RE = re.compile(r"^<(?P<frame>\d+)f>(?P<body>.*)$")


@dataclass
class SamImageResult:
    text: str
    records: list["SamRecord"]
    latency_ms: int
    request_id: str
    input_tokens: int
    output_tokens: int


@dataclass
class SamRecord:
    frame_index: int
    track_id: str
    x1: int
    y1: int
    x2: int
    y2: int
    width: int
    height: int
    confidence: float | None
    mask_payload: str
    mask_encoding: str


def parse_sam_output(text: str) -> list[SamRecord]:
    records: list[SamRecord] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        frame_match = FRAME_RE.match(line)
        if not frame_match:
            continue
        frame_index = int(frame_match.group("frame"))
        for match in BOX_RE.finditer(frame_match.group("body")):
            fields = _fields(match.group("body"))
            mask_fields = _fields(match.group("mask"))
            data = mask_fields.get("data", "")
            encoding = "one_bit" if ",!" in data or data.endswith("!") else "lossless"
            if ",!" in data:
                encoding = "one_bit"
            elif ",~" in data:
                encoding = "lossless"
            records.append(
                SamRecord(
                    frame_index=frame_index,
                    track_id=str(match.group("id")),
                    x1=int(fields["x1"]),
                    y1=int(fields["y1"]),
                    x2=int(fields["x2"]),
                    y2=int(fields["y2"]),
                    width=int(fields["w"]),
                    height=int(fields["h"]),
                    confidence=float(fields["c"]) if "c" in fields else None,
                    mask_payload=match.group("mask"),
                    mask_encoding=encoding,
                )
            )
    return records


def _fields(body: str) -> dict[str, str]:
    found = {}
    for part in body.split(";"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        found[key] = value
    return found


def decode_mask_raster(record: SamRecord):
    """Return a row-major 0/1 raster when the official parser is installed."""
    try:
        from meta_sam_parser import decode_mask_to_raster
    except ImportError:
        return None
    try:
        mask = {
            "x": 0,
            "y": 0,
            "data": record.mask_payload.split("data=", 1)[-1],
            "width": record.width,
            "height": record.height,
        }
        return decode_mask_to_raster(mask)
    except Exception:
        return None


class SamAdapter:
    model_id = "sam-3.1"

    def __init__(self, secret: str, timeout_s: float = 180):
        self.secret = secret
        self.timeout_s = timeout_s

    def _client(self) -> AsyncOpenAI:
        return AsyncOpenAI(api_key=self.secret, base_url=META_BASE, timeout=self.timeout_s)

    async def segment_image(self, image_path, concept: str) -> SamImageResult:
        if not concept.strip():
            raise ProviderError("SAM concept prompts must be a non-empty noun phrase.")
        started = time.perf_counter()
        try:
            response = await self._client().responses.create(
                model=self.model_id,
                input=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": concept},
                            {"type": "input_image", "image_url": data_url(image_path)},
                        ],
                    }
                ],
                metadata={"mask_encoding": "one_bit", "include_confidence": "true"},
            )
        except Exception as exc:
            raise translate(exc) from exc
        usage = getattr(response, "usage", None)
        text = getattr(response, "output_text", "") or ""
        return SamImageResult(
            text=text,
            records=parse_sam_output(text),
            latency_ms=int((time.perf_counter() - started) * 1000),
            request_id=str(getattr(response, "id", "") or ""),
            input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
        )
