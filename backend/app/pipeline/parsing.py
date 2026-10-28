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


_COLON_REPAIR_LIMIT = 4
_VALUE_START = set('{["0123456789-tfn')
_BARE_STRING = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\"")


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
        blob = stripped[start : end + 1]
        try:
            payload = json.loads(blob)
        except json.JSONDecodeError as exc:
            payload = _recover_object(blob, exc)
            if payload is None:
                raise ParseFailure(f"malformed JSON: {exc}", text) from exc
    if not isinstance(payload, dict):
        raise ParseFailure("model JSON must be an object", text)
    return payload


def _recover_object(blob: str, exc: json.JSONDecodeError) -> dict[str, Any] | None:
    """Recover two shapes long answers actually come back in.

    A missing colon is one dropped ``:"`` between a key and its value. An
    abandoned document is a fragment, then a second object that runs to the
    closing brace. A first object that already parses is left alone.
    """
    repaired = _repair_missing_colons(blob, exc)
    if repaired is not None:
        return repaired
    try:
        json.JSONDecoder().raw_decode(blob)
    except json.JSONDecodeError:
        return _object_through_end(blob)
    return None


def _repair_missing_colons(blob: str, exc: json.JSONDecodeError) -> dict[str, Any] | None:
    current = blob
    error = exc
    for _ in range(_COLON_REPAIR_LIMIT):
        if error.msg != "Expecting ':' delimiter":
            return None
        nxt = _with_colon(current, error.pos)
        if nxt is None:
            return None
        try:
            payload = json.loads(nxt)
        except json.JSONDecodeError as again:
            current = nxt
            error = again
            continue
        if isinstance(payload, dict):
            return payload
        return None
    return None


def _with_colon(blob: str, pos: int) -> str | None:
    if pos <= 0 or pos >= len(blob) or blob[pos - 1] != '"':
        return None
    nxt = blob[pos]
    if nxt in _VALUE_START:
        return blob[:pos] + ":" + blob[pos:]
    if _BARE_STRING.match(blob[pos:]):
        return blob[:pos] + ':"' + blob[pos:]
    return None


def _object_through_end(blob: str) -> dict[str, Any] | None:
    decoder = json.JSONDecoder()
    start = 0
    while True:
        index = blob.find("{", start)
        if index < 0:
            return None
        try:
            payload, stop = decoder.raw_decode(blob, index)
        except json.JSONDecodeError:
            start = index + 1
            continue
        if isinstance(payload, dict) and stop == len(blob):
            return payload
        start = index + 1


def parse_analysis(text: str) -> AnalysisDocument:
    payload = extract_json_object(text)
    try:
        return AnalysisDocument.model_validate(payload)
    except ValidationError as exc:
        raise ParseFailure(f"response did not match the analysis schema: {exc}", text) from exc
