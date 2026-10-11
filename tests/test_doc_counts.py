"""A written-out count in contract prose is a claim about a tuple's `len()`, and nothing checked it.

`engine/transcript.py:7` already states the principle for its own vocabulary — *"`EVENT_TYPES` is
the number, and `len()` is how to ask"* — but the two documents that quote those numbers to a
reader were pinned on neither the names nor the count. Both had drifted silently:

* `CLAUDE.md`'s Transcript-events block listed 19 types and said `(19 …)` while `EVENT_TYPES`
  held 20 — `oversight_failed` (`transcript.py:42`, written by the escalation ladder) was in the
  code, in the web renderer and in the CLI, and absent from the vocabulary every contributor
  reads. CLAUDE.md's own instruction on that line is "extend the tuple and both readers
  together, never one alone"; the doc was the reader nobody extended.
* `docs/prompt-anatomy.md` called the action schema "the full 30-kind schema" while `KINDS`
  held 33.

A count is the cheapest kind of documentation to falsify and the most expensive to disbelieve: a
reader who counts 20 names under a `(19 …)` cannot tell which half is stale. These tests fail
until the prose follows the source, in both directions — the names AND the number.
"""

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
