"""Bounded subprocess capture with explicit, parseable loss metadata."""
from __future__ import annotations

import json
from typing import TextIO


class CapturedOutput(str):
    """String-compatible transport carrying capture provenance outside user content."""

    __slots__ = ("capture_truncated",)
    capture_truncated: bool

    def __new__(cls, text: str, *, truncated: bool = False):
        obj = super().__new__(cls, text)
        obj.capture_truncated = truncated
        return obj


def read_capped(stream: TextIO, limit: int, *, diagnostic: str = "") -> CapturedOutput:
    """Keep ordinary output unchanged; bound the serialized envelope after overflow."""
    stream.seek(0)
    text = stream.read(limit + 1)
    if len(text) <= limit:
        return CapturedOutput(f"{text}\n[{diagnostic}]" if diagnostic and text
                              else diagnostic or text)

    def encode(count: int) -> str:
        return json.dumps({"capture_truncated": True, "complete": False,
                           "capture_limit_chars": limit, "diagnostic": diagnostic,
                           "preview": text[:count]}, ensure_ascii=True, separators=(",", ":"))

    if len(encode(0)) > limit:
        raise ValueError("capture limit too small for truncation metadata")
    low, high = 0, limit
    while low < high:
        mid = (low + high + 1) // 2
        if len(encode(mid)) <= limit:
            low = mid
        else:
            high = mid - 1
    return CapturedOutput(encode(low), truncated=True)
