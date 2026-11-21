"""A written-out count in contract prose is a claim about a tuple's `len()`, and nothing checked it."""

import re
from pathlib import Path

from rsched.engine.actionschema import KINDS
from rsched.engine.transcript import EVENT_TYPES

ROOT = Path(__file__).resolve().parents[1]
CLAUDE = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
ANATOMY = (ROOT / "docs" / "prompt-anatomy.md").read_text(encoding="utf-8")

#: The vocabulary block: its inline list, then the count in parentheses that summarises it.
_EVENTS_BLOCK = re.compile(r"\*\*Transcript events\*\*.*?\((\d+)\s+—", re.DOTALL)
#: "not the full 30-kind schema" — the projection paragraph's claim about `KINDS`.
_KIND_COUNT = re.compile(r"full\s+(\d+)-kind schema")


def test_claude_md_names_every_transcript_event_type() -> None:
    """The missing name is the defect; the count is only how a reader notices."""
    missing = [t for t in EVENT_TYPES if t not in CLAUDE]
    assert not missing, (
        "CLAUDE.md's Transcript-events vocabulary omits event types the engine writes: "
        f"{missing}. Extend the doc with the tuple (CLAUDE.md: 'extend the tuple and both "
        "readers together, never one alone')."
    )


def test_claude_md_event_count_matches_the_tuple() -> None:
    match = _EVENTS_BLOCK.search(CLAUDE)
    assert match, "CLAUDE.md's **Transcript events** block no longer states a count in parentheses"
    assert int(match.group(1)) == len(EVENT_TYPES), (
        f"CLAUDE.md says ({match.group(1)} …) transcript event types; "
        f"transcript.EVENT_TYPES holds {len(EVENT_TYPES)}"
    )


def test_prompt_anatomy_kind_count_matches_the_schema() -> None:
    match = _KIND_COUNT.search(ANATOMY)
    assert match, "docs/prompt-anatomy.md no longer states the schema's kind count"
    assert int(match.group(1)) == len(KINDS), (
        f"docs/prompt-anatomy.md says the schema has {match.group(1)} kinds; "
        f"actionschema.KINDS holds {len(KINDS)}"
    )
