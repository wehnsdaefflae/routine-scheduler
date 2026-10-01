"""Bounded subprocess capture with explicit, parseable loss metadata."""
from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import TextIO

#: The shortest secret VALUE that is redacted from captured output. A shorter one is too
#: likely to be an ordinary word or number the command printed for its own reasons — masking
#: every "true" or "8080" would corrupt output without protecting anything a guess could not.
REDACT_MIN_CHARS = 8


class CapturedOutput(str):
    """String-compatible transport carrying capture provenance outside user content."""

    __slots__ = ("capture_truncated",)
    capture_truncated: bool

    def __new__(cls, text: str, *, truncated: bool = False):
        obj = super().__new__(cls, text)
        obj.capture_truncated = truncated
        return obj


def _secret_pattern(secrets: Mapping[str, str] | None) -> tuple[re.Pattern[str] | None,
                                                               dict[str, str], int]:
    """The one regex over every redactable value (longest first, so a value that contains
    another is replaced whole), the value → name map its replacement reads, and the longest
    value's length — the read-ahead that keeps a value from straddling the capture's cut.
    """
    by_value = {v: k for k, v in (secrets or {}).items() if len(v) >= REDACT_MIN_CHARS}
    if not by_value:
        return None, {}, 0
    values = sorted(by_value, key=len, reverse=True)
    return re.compile("|".join(map(re.escape, values))), by_value, len(values[0])


def read_capped(stream: TextIO, limit: int, *, diagnostic: str = "",
                secrets: Mapping[str, str] | None = None) -> CapturedOutput:
    """Keep ordinary output unchanged; bound the serialized envelope after overflow.

    `secrets` (name → value) are the credentials the command was handed. A command that
    prints one — a util echoing its request headers, a script dumping its env on a crash —
    would otherwise carry the value verbatim into the observation, the transcript, the spill
    file and full-text search, where every later run and every reader can see it. Each value
    of at least REDACT_MIN_CHARS is replaced by `[secret NAME redacted]` HERE, the one seam
    every util, script and shell capture passes, before any of those copies exists. The read
    reaches one longest-value past the cut and the cut never splits a value, so a secret
    crossing the cap cannot survive as a prefix in the preview.
    """
    pattern, by_value, pad = _secret_pattern(secrets)
    stream.seek(0)
    raw = stream.read(limit + 1 + pad)
    complete = len(raw) < limit + 1 + pad
    if pattern is None:
        text = raw
    else:
        cut = len(raw) if complete else limit + 1
        for m in pattern.finditer(raw):
            if m.start() < cut < m.end():
                cut = m.end()            # never leave a value's head on this side of the cut
        text = pattern.sub(lambda m: f"[secret {by_value[m.group(0)]} redacted]", raw[:cut])
    if complete and len(text) <= limit:
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
