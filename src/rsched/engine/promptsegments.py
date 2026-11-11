"""The prompt's per-turn SEGMENT breakdown (D270): what this turn's window is spent on.

`window.note_prompt_size` measures the prompt's TOTAL against what the provider counted. This
measures its MAKE-UP: the composer's own `(name, text)` sections — harness, schema, example,
workflow, instruction, capabilities, digest, messages, reports — plus the two segments the
message list grows turn by turn (`history`, what the model itself said, and `observations`,
what the engine handed back). Recorded beside the ratio, by the same writer, on the turn whose
prompt the provider has just counted.

DIAGNOSTIC ONLY, which is a hard constraint and not a preference. The composed prompt is a
CACHING CONTRACT — appended to, never mutated; `window.py`'s header names the four sanctioned
exceptions and this is not one of them. A measurement that rewrote one byte of what is sent
would invalidate the provider's prompt cache for the rest of the run, and a cold window of this
size has cost over $100 here. So every function in this module READS: it takes the section
tuples the composer already built and the message list exactly as it was sent, and writes
nothing but its own dict. `tests/test_prompt_segments.py` holds that to byte identity on both
halves of the request (the joined system prompt and the message list, media included).

The unit is the engine's own estimate (`compaction.estimate_input_tokens` — 3.5 UTF-8
bytes/token plus per-message framing), because that is the currency every window gate in
`window.py` compares against: a segment and the window it is measured against then read in the
same units. It is a packing guess and not a tokenizer count, and `note_prompt_size`'s run-local
ratio is the measurement of how far off it runs on this run's content.
"""

from __future__ import annotations

import json

from . import promptcapture
from .compaction import estimate_input_tokens

#: The message roles the prompt GROWS by, named as the breakdown reports them: what the model
#: said, and what it was handed back. The system message is measured section by section
#: instead (`of_sections`), so it is never counted here a second time.
#:
#: PUBLIC because a second reader needs the same mapping and a second copy of it would be a
#: second thing to keep true: `readmodels/promptsegtext.py` serves the TEXT of exactly these two
#: segments (they are the message list, which is on disk) and refuses it for every always-on
#: section (whose text is never stored). If this mapping and that reader disagreed, the view
#: would offer a click the server answers with a refusal, or hide one it could honour.
GROWN_SEGMENTS = {"assistant": "history", "user": "observations"}


def of_sections(sections: list[tuple[str, str]]) -> dict[str, int]:
    """The always-on prompt by SECTION, in prompt order — the composer's own segmentation.

    `composer.system_sections` already names and orders every section it joins, so measuring
    its tuples is the one segmentation that cannot disagree with what is actually sent. A
    second, hand-written segmentation of the same prompt could, and would be believed.
    """
    return {name: estimate_input_tokens([{"content": text}]) for name, text in sections}


def note_sections(ctx, sections: list[tuple[str, str]]) -> tuple[dict[str, int], dict[str, int]]:
    """Boot's one call over the composed sections: MEASURE them twice, and record their TEXT.

    Returns `(chars, tokens)` — the run record's unit and the window's — and writes the section
    texts once for this leg (`engine/promptcapture`, R3341, on the operator's authorization:
    *"store the always-on section texts ONCE at boot in the run dir"*). All three readings come
    from the SAME tuple list, so the text a reader opens and the token count the bar draws can
    never describe different prompts.

    It lives here rather than in `boot` because this module already owns "what the always-on
    prompt is made of" — and because `boot.py` sits exactly at its size-ratchet ceiling, so one
    call there is the whole budget. Nothing in the return value or the file reaches the prompt
    path: `of_sections` reads, `promptcapture` writes a diagnostic, and the caller joins the
    tuples it already had.
    """
    promptcapture.write(ctx.run_dir, sections)
    return {name: len(text) for name, text in sections}, of_sections(sections)


def schema_tokens(loop) -> int:
    """The action schema's own cost. It travels BESIDE the message list — the provider counts
    it in the prompt, `estimate_input_tokens(loop.messages)` never sees it.
    """
    schema = getattr(loop, "action_schema", None)
    if not schema or getattr(loop, "_schema_off", False):
        return 0
    return estimate_input_tokens([{"content": json.dumps(schema, ensure_ascii=False)}])


def measure(loop, sections: dict[str, int] | None = None) -> dict[str, int]:
    """This turn's breakdown: the always-on sections, then the two segments that grow.

    The section sizes are the ones boot measured — the always-on prompt is re-sent verbatim
    every turn, so nothing re-reads it — while the grown segments are re-measured from
    `loop.messages` as the completion just sent it. Returns a fresh dict; the caller decides
    where it is recorded, and nothing here touches the prompt.
    """
    grown: dict[str, list[dict]] = {name: [] for name in GROWN_SEGMENTS.values()}
    for msg in loop.messages:
        segment = GROWN_SEGMENTS.get(msg.get("role", ""))
        if segment is not None:
            grown[segment].append(msg)
    sizes = dict(sections or {})
    sizes.update({name: estimate_input_tokens(msgs) for name, msgs in grown.items()})
    return sizes


def note(loop) -> None:
    """Record this turn's breakdown on the run context, leaving the prompt untouched.

    Best-effort by construction: a diagnostic may never be the reason a turn fails, and the
    unit fixtures that drive `window.note_prompt_size` build a loop with no run context at
    all. The sections it reports are the ones boot recorded (`ctx.prompt_chars`' own source),
    re-stated in the window's own unit so the segments and the window agree.
    """
    ctx = getattr(loop, "ctx", None)
    if ctx is None:
        return
    sections = getattr(ctx, "prompt_segments", None) or {}
    ctx.prompt_segments = measure(loop, {k: v for k, v in sections.items()
                                         if k not in GROWN_SEGMENTS.values()})
