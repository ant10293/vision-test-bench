"""JSON extraction from untrusted model text."""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import ValidationError

from app.schemas.analysis import AnalysisDocument


class ParseFailure(Exception):
    def __init__(self, message: str, raw_text: str = ""):
        super().__init__(message)
        self.raw_text = raw_text


def extract_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?", "", stripped).strip()
        stripped = re.sub(r"```$", "", stripped).strip()
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start < 0 or end <= start:
            raise ParseFailure("model response did not contain a JSON object", text) from None
        try:
            payload = json.loads(stripped[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ParseFailure(f"malformed JSON: {exc}", text) from exc
    if not isinstance(payload, dict):
        raise ParseFailure("model JSON must be an object", text)
    return payload


def parse_analysis(text: str) -> AnalysisDocument:
    payload = extract_json_object(text)
    try:
        return AnalysisDocument.model_validate(payload)
    except ValidationError as exc:
        raise ParseFailure(f"response did not match the analysis schema: {exc}", text) from exc
