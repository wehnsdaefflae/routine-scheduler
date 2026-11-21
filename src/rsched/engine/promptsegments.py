"""The prompt's per-turn SEGMENT breakdown (D270): what this turn's window is spent on.

`window.note_prompt_size` measures the prompt's TOTAL against what the provider counted. This
measures its MAKE-UP: the composer's own `(name, text)` sections — harness, schema, example,
workflow, instruction, capabilities, digest, messages, reports — plus the two segments the
message list grows turn by turn (`history`, what the model itself said, and `observations`,
what the engine handed back). Recorded beside the ratio, by the same writer, on the turn whose
prompt the provider has just counted.

DIAGNOSTIC ONLY, a hard constraint: the composed prompt is a CACHING CONTRACT, and a
measurement that rewrote one byte of what is sent would invalidate the provider's cache. Every
function here READS the composer's section tuples and the message list as sent, and writes
nothing but its own dict; `tests/test_prompt_segments.py` holds both halves to byte identity.

The unit is the engine's own estimate (`compaction.estimate_input_tokens`), the currency every
window gate in `window.py` compares against — a packing guess, not a tokenizer count.
"""

from __future__ import annotations

import json

from . import promptcapture
from .compaction import estimate_input_tokens

#: The message roles the prompt GROWS by, named as the breakdown reports them: what the model
#: said, and what it was handed back. The system message is measured section by section
#: instead (`of_sections`), so it is never counted here a second time. PUBLIC because
#: `readmodels/promptsegtext.py` serves the TEXT of exactly these segments and must agree.
GROWN_SEGMENTS = {"assistant": "history", "user": "observations"}


def of_sections(sections: list[tuple[str, str]]) -> dict[str, int]:
    """The always-on prompt by SECTION, in prompt order — the composer's own segmentation.

    Measuring `composer.system_sections`' own tuples is the one segmentation that cannot
    disagree with what is actually sent.
    """
    return {name: estimate_input_tokens([{"content": text}]) for name, text in sections}


def note_sections(ctx, sections: list[tuple[str, str]]) -> tuple[dict[str, int], dict[str, int]]:
    """Boot's one call over the composed sections: MEASURE them twice, and record their TEXT.

    Returns `(chars, tokens)` — the run record's unit and the window's — and writes the section
    texts once for this leg (`engine/promptcapture`). All three readings come from the SAME
    tuple list, so the stored text and the counts can never describe different prompts. Nothing
    here reaches the prompt path.
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

    The section sizes are boot's (the always-on prompt is re-sent verbatim); the grown segments
    are re-measured from `loop.messages` as just sent. Returns a fresh dict.
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

    Best-effort: a diagnostic may never fail a turn, and a loop may have no run context.
    """
    ctx = getattr(loop, "ctx", None)
    if ctx is None:
        return
    sections = getattr(ctx, "prompt_segments", None) or {}
    ctx.prompt_segments = measure(loop, {k: v for k, v in sections.items()
                                         if k not in GROWN_SEGMENTS.values()})
