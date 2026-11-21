"""The always-on prompt's SECTION TEXTS, written once at boot — so the whole bar can be read.

`history` and `observations` are already on disk in the transcript; the always-on sections are
recorded here so the console can show them. Re-composing at read time is no alternative: building
the digest drains a shared store and writes transcript events, so a GET would mutate state.

The bounds are the contract:

- **ONCE at boot, never per turn.** The always-on prompt is re-sent verbatim every turn, so one
  capture per leg describes every turn; per-turn would multiply the size by the turn count.
- **In the run dir**, beside the transcript, under the run's own retention.
- **Not one byte of what is sent.** This module READS the composer's `(name, text)` tuples and
  returns nothing the prompt path consumes — the composed prompt is a caching contract
  (`tests/test_prompt_segments.py` holds it to byte identity).
- **Nothing in the engine reads it back.** A resumed leg RE-COMPOSES its system prompt; reading
  a stored copy in would let a stale file change what is sent.

**Best-effort:** a capture that cannot be written must never stop a run starting, so failures
are swallowed to a log line and the view says "not recorded". **A resumed leg overwrites it**,
so the file describes the prompt the CURRENT leg sends rather than a confident wrong text.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger("rsched.engine.promptcapture")

#: Where the capture lands, beside `transcript.jsonl` in the run dir.
FILE = "prompt-sections.json"

#: What one section's text is cut to — far above every real section, a bound only against a
#: pathological instruction or digest.
SECTION_MAX = 128 * 1024


def path(run_dir: Path) -> Path:
    """The capture's path for a run."""
    return Path(run_dir) / FILE


def write(run_dir: Path, sections: list[tuple[str, str]]) -> None:
    """Record the always-on sections' TEXT for this leg. Never raises.

    `sections` is the composer's own `(name, text)` list, in prompt order — the same value
    `promptsegments.of_sections` measures, taken here rather than re-derived so the text and the
    token count can never describe different prompts. Written atomically, so a reader never sees
    half a capture.
    """
    try:
        rows = [{"name": str(name), "chars": len(text), "truncated": len(text) > SECTION_MAX,
                 "text": text[:SECTION_MAX]} for name, text in sections]
        payload = {"sections": rows}
        target = path(run_dir)
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(target)
    except (OSError, TypeError, ValueError):
        # A diagnostic may never be the reason a run fails to start; the view says "not recorded".
        log.warning("prompt-capture: the section texts could not be recorded", exc_info=True)


def read(run_dir: Path) -> dict[str, dict]:
    """`{name: {chars, truncated, text}}` for a run, or `{}` when nothing was captured.

    The reader's job on `{}` is to say so, never to imply the sections were empty.
    """
    try:
        raw = json.loads(path(run_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    rows = raw.get("sections") if isinstance(raw, dict) else None
    if not isinstance(rows, list):
        return {}
    out: dict[str, dict] = {}
    for row in rows:
        if isinstance(row, dict) and (name := str(row.get("name") or "")):
            out[name] = {"chars": int(row.get("chars") or 0),
                         "truncated": bool(row.get("truncated")),
                         "text": str(row.get("text") or "")}
    return out
