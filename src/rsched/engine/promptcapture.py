"""The always-on prompt's SECTION TEXTS, written once at boot — so the whole bar can be read.

The operator asked *"can we click on it to see the actual prompt?"*, and the honest answer was
half of it: `history` and `observations` are the message list and are on disk in the transcript,
but the always-on sections — most of the bar — had no recorded text anywhere. Re-composing one at
read time is not an alternative: the path that builds the digest drains a shared store, touches
the job ledger and writes `answer` transcript events, so a GET endpoint that re-composed would
mutate state on every tap of the bar.

So he authorized the write, in these words: **"Also store the always-on section texts ONCE at
boot in the run dir, so the whole bar becomes clickable and truthful — a new persistence of
prompt text (~60-250 kB per run), touching no byte of what is sent and no cache test"**.

**His wording IS the contract, and every bound in it is load-bearing:**

- **ONCE at boot, never per turn.** The always-on prompt is composed once and re-sent verbatim
  every turn, so one capture describes every turn of the leg — and the figure he accepted
  (~60-250 kB) is a per-RUN one. A per-turn capture would multiply it by the turn count and is
  not what he agreed to.
- **In the run dir**, beside the transcript, under the same retention as the run itself.
- **Not one byte of what is sent.** This module READS the `(name, text)` tuples the composer
  already built and the caller already joined; it returns nothing the prompt path consumes.
  `engine/promptsegments.py`'s header states why that matters and
  `tests/test_prompt_segments.py` holds it to byte identity on both halves of the request: the
  composed prompt is a caching contract, and a cold window of this size has cost over $100 here.
- **Nothing in the engine reads it back.** It is a diagnostic surface for the console. A resumed
  leg RE-COMPOSES its system prompt (`boot` does, every leg) and must keep doing so — reading a
  stored copy into the prompt would make a stale file able to change what is sent, which is the
  one thing this may not touch.

**Best-effort, like every diagnostic on the boot path.** A capture that cannot be written must
never be why a run fails to start, so every failure here is swallowed to a log line. The run
then has no section texts and the view says so for those segments, which is exactly the state
every run before this one is in.

**A resumed leg overwrites it,** deliberately: the sections are re-composed on every leg from
that moment's digest, capabilities and inbox, so the file describes the prompt the CURRENT leg
is sending. The alternative — keeping the first leg's copy — would label a text as what is being
sent when it is not, and an honest "not recorded" was always better than a confident wrong text.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger("rsched.engine.promptcapture")

#: Where the capture lands, beside `transcript.jsonl` in the run dir.
FILE = "prompt-sections.json"

#: What one section's text is cut to. 128 KiB is far above every real section (the harness and
#: the schema, the two largest, are ~30 KB each), so nothing real is cut — it is a bound against
#: a pathological instruction or digest making the capture itself the biggest file in the run.
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
        # A diagnostic may never be the reason a run fails to start. The view falls back to
        # "text not recorded", which is where every run before this one already is.
        log.warning("prompt-capture: the section texts could not be recorded", exc_info=True)


def read(run_dir: Path) -> dict[str, dict]:
    """`{name: {chars, truncated, text}}` for a run, or `{}` when nothing was captured.

    `{}` is the ordinary answer for every run recorded before this shipped, and the reader's job
    is to say so rather than to imply the sections were empty.
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
