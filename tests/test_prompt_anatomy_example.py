"""§5 of docs/prompt-anatomy.md calls itself a FULL VERBATIM EXAMPLE — this makes it one (F672).

The section quotes a whole composed system prompt, and the Help tab renders it as what the
orchestrator actually sees. Nothing compared it to the composer. By 2026-10-15 it had drifted
in three ways at once, none of them visible to the checks that existed:

1. **Nine of fifteen kind bullets** were an older wording than `kindsurface.KIND_PROSE`
   (`llm`, `spawn`, `subtask`, `detach`, `list_models`, `subruns`, `kill`, `ask_user`,
   `report`). One divergence reached a run's behaviour rather than its reading: the `subtask`
   bullet offered `workflow: "generate"` "to DRAFT one when none fits" — `generate` is real for
   `create_routine` only, and `subruns.py`/`child.py` have zero hits for it, so a run following
   the doc would have its child refused.
2. **The ACTION SCHEMA dump disagreed with the bullets in the same section**: the bullets
   advertised `goal`, `list_models` and `delete`/`move`/`mkdir`, while the dump's `kind` enum
   omitted all five. The example contradicted itself about what its own routine could emit.
3. **Nine schema fields were missing and eight descriptions stale** — `options` still said
   `maxItems: 5` where the schema says 26 (ask_user's cap is a validator constant, not the
   field's).

`test_prompt_anatomy.py` catches a field the schema no longer DEFINES, and pins individual
engine strings as needles. Neither shape could see any of the above: a bullet quoting last
month's wording defines nothing and matches no needle. So this file checks the other direction
— the one that makes "verbatim" structural:

- every bullet the composer would emit for the example's own kind set appears in the fence;
- no bullet appears for a kind the example's schema does not carry;
- the schema dump is byte-identical to `schema_for_kinds` for that same kind set.

The example's kind set is taken from the dump's own `kind` enum, so the doc stays free to
choose which routine it portrays; what it cannot do is portray it inaccurately.
"""

import json
import re
from pathlib import Path

from rsched.engine.kindsurface import KIND_PROSE, schema_for_kinds

DOC = Path(__file__).resolve().parents[1] / "docs" / "prompt-anatomy.md"
TEXT = DOC.read_text(encoding="utf-8")

#: The two placeholders the composer substitutes per routine (`engine/harness.py`): the
#: util-approval sentence (one of three, by the routine's confirm dial) and the ask_user
#: timeout in minutes. A bullet carrying one is checked segment-wise around it — the engine's
#: own words are pinned, the routine-specific value is not.
_PLACEHOLDERS = ("{util_confirm}", "{ask_timeout_min}", "{ask_options_max}")

#: `script` is the one kind with no bullet by design — its gloss sits in CAPABILITIES, beside
#: the routine's own scripts (see KIND_PROSE's own comment).
_NO_BULLET = {"script"}


def _fence() -> str:
    """The quoted system prompt of §5.1 — the first fenced block after its header."""
    section = TEXT[TEXT.index("### 5.1 System prompt"):TEXT.index("### 5.2")]
    parts = section.split("```")
    assert len(parts) >= 3, "§5.1 no longer contains a fenced block"
    return parts[1]


def _schema_dump() -> tuple[str, dict]:
    """The `# ACTION SCHEMA` JSON inside the §5.1 fence, as written and as parsed."""
    fence = _fence()
    head = "# ACTION SCHEMA (your every reply matches this)\n"
    start = fence.index(head) + len(head)
    end = fence.index("\n# EXAMPLE of a valid reply", start)
    raw = fence[start:end]
    return raw, json.loads(raw)


def _contains_around_placeholders(haystack: str, prose: str) -> bool:
    """`prose` appears in `haystack`, allowing any value where a placeholder sits."""
    pattern = prose
    for ph in _PLACEHOLDERS:
        pattern = pattern.replace(ph, "\x00")
    segments = pattern.split("\x00")
    pos = 0
    for seg in segments:
        if not seg:
            continue
        found = haystack.find(seg, pos)
        if found < 0:
            return False
        pos = found + len(seg)
    return True


def test_the_quoted_bullets_are_the_composer_s_own_prose():
    """Every bullet the example's kind set earns is in the fence, word for word.

    This is what the `generate` slug slipped through: the doc's `subtask` bullet was a
    paraphrase nobody compared to anything, and a run can act on a paraphrase.
    """
    fence = _fence()
    kinds = set(_schema_dump()[1]["properties"]["kind"]["enum"])
    checked = 0
    for covers, prose in KIND_PROSE:
        if not kinds & set(covers):
            continue
        checked += 1
        assert _contains_around_placeholders(fence, prose), (
            f"docs/prompt-anatomy.md §5.1 quotes the {covers[0]!r} bullet in wording the "
            f"composer no longer emits. Re-render the block from "
            f"kindsurface.kind_bullets() rather than editing it by hand:\n\n{prose}")
    assert checked >= 10, f"only {checked} bullets were checked — has §5.1's kind set shrunk?"


def test_no_bullet_quotes_a_kind_the_example_cannot_emit():
    """§5 is a PROJECTION, so an absent bullet is legitimate — a PRESENT one for an absent
    kind is not. The doc carried `goal` and `list_models` bullets while its own schema enum
    omitted both: the section disagreed with itself about the routine it portrays."""
    fence = _fence()
    kinds = set(_schema_dump()[1]["properties"]["kind"]["enum"])
    leads = {m.group(1) for m in re.finditer(r"^- ([a-z_]+)[ :/]", fence, re.MULTILINE)}
    bulleted = {k for covers, _ in KIND_PROSE for k in covers}
    stray = {lead for lead in leads if lead in bulleted and lead not in kinds}
    assert not stray, (
        f"§5.1 quotes bullets for kinds its own ACTION SCHEMA enum omits: {sorted(stray)}. "
        "Both halves must be rendered for ONE kind set.")


def test_the_quoted_schema_is_the_projection_byte_for_byte():
    """The dump is `schema_for_kinds` for the example's kinds, serialised the way the composer
    serialises it (`indent=1`, ASCII-escaped). Nine fields and eight descriptions had gone
    stale behind a hand-maintained copy."""
    raw, shown = _schema_dump()
    kinds = shown["properties"]["kind"]["enum"]
    # The fence carries no reminder standing instruction, so the example routine's reminders
    # capability is off — the projection drops `remind`/`remind_feedback` with it.
    assert "Label every hold with" not in _fence(), (
        "§5.1 now quotes the reminder standing instruction — render the dump with "
        "schema_for_kinds(..., reminders=True) and update this test's expectation")
    expected = json.dumps(schema_for_kinds(kinds, reminders=False), indent=1,
                          ensure_ascii=True)
    assert raw.strip() == expected.strip(), (
        "docs/prompt-anatomy.md §5.1's ACTION SCHEMA block is not the projection for its own "
        "`kind` enum. Re-render it: json.dumps(schema_for_kinds(<enum>, reminders=False), "
        "indent=1, ensure_ascii=True)")


def test_every_kind_in_the_example_has_its_bullet_or_is_the_known_exception():
    """A kind the example's schema carries is a kind the run can emit, so the harness owes it
    a bullet. `script` is the documented exception (its gloss lives in CAPABILITIES)."""
    kinds = set(_schema_dump()[1]["properties"]["kind"]["enum"])
    bulleted = {k for covers, _ in KIND_PROSE for k in covers}
    assert not (kinds - bulleted - _NO_BULLET), (
        f"kinds in §5.1's schema with no bullet in KIND_PROSE: "
        f"{sorted(kinds - bulleted - _NO_BULLET)}")
