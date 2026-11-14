"""The GATE over the limits convention — every declared remainder is ADDRESSED to someone.

The scanner it runs on is `tests/limitscan.py`: parsing the CHANGELOG's declaration vocabulary
and `docs/limits.md`'s entries is one job (a reusable reader, with its own evidence in its
docstring), and asserting over them is another. They were one file until it crossed the house
350-line bar, which may only be answered by splitting along a responsibility — and that boundary
was already written into the file as a comment.

Why the convention exists, in one line: of nine remainders declared between 0.444.0 and 0.445.2,
the one given a report id was fixed the NEXT DAY and six carried no route at all. `limitscan`'s
docstring holds the whole measurement.
"""

from __future__ import annotations

from limitscan import (
    _INLINE,
    _OPENERS,
    DESIGNS_DOC,
    LIMITS_PATH,
    REQUIRED_FIELDS,
    TEACH,
    _changelog,
    _limits,
    clauses,
    disposition,
    entries,
    entry_problems,
    undisposed,
)
from rsched.docs_build import GUIDE_ORDER

# ---- the gate ----------------------------------------------------------------------------

def test_every_declared_limit_carries_a_disposition():
    problems = undisposed(_changelog(), _limits())
    assert not problems, f"{len(problems)} limit clause(s) addressed to nobody:\n  " + \
        "\n  ".join(problems) + "\n\n" + TEACH


def test_every_entry_in_the_store_is_complete_and_still_matches_its_clause():
    problems = entry_problems(_limits(), _changelog())
    assert not problems, ("docs/limits.md entries that do not hold up:\n  " + "\n  ".join(problems))


def test_the_scanner_is_not_vacuous():
    """A prose scanner whose regex stops matching passes while proving nothing."""
    found = clauses(_changelog())
    assert len(found) >= 12, f"only {len(found)} limit clauses found — did the markers break?"
    assert {c.marker for c in found} >= {"Known limit", "Still open:", "is NOT built"}
    # Keyed by (release, line), never by release alone: 0.445.1 declares THREE clauses and a
    # per-release dict silently kept whichever came last, so the three assertions below passed
    # or failed on dict ordering rather than on what the changelog says.
    routed = {(c.release, c.line): disposition(c.text) for c in found if disposition(c.text)}
    said = list(routed.values())
    assert any(d.startswith("routed as R2844") for d in said), routed
    assert any("q-20260912-121159-40" in d for d in said), routed
    assert any(DESIGNS_DOC in d for d in said), routed
    assert entries(_limits()), "docs/limits.md holds no entries, so the store is not the reader"


def test_a_clause_passes_with_a_disposition_and_fails_without_one():
    """Both directions, on text this test owns — the gate above can only prove one of them."""
    head = "## [9.9.9] — 2026-01-01\n\n### Added\n- **A thing** (R1).\n"
    naked = head + "  Known limit: the watch covers one provider of three.\n"
    assert undisposed(naked, ""), "a clause with no disposition must fail"
    for disposed in (
        "  Known limit: it covers one provider. Not fixed, reported as R9001: the other two.\n",
        "  *Still open:* `q-20260101-120000-1` needs the callers moved first.\n",
        "  Part (b) is NOT built, so the entry stays in `docs/designs.md`.\n",
        ("  Known limit: one provider only. Accepted permanently: the other two are proxied "
         "through it, so there is nothing to watch.\n"),
    ):
        assert not undisposed(head + disposed, ""), disposed
    # and the store itself is a disposition, joined on the release that declared the limit
    entry = ('## One provider\n\n- **Not covered:** two providers.\n- **Evidence:** read it.\n'
             '- **Declared:** 9.9.9 (2026-01-01)\n'
             '- **Clause:** "the watch covers one provider of three"\n'
             '- **Would matter when:** a key expires.\n- **Disposition:** undecided.\n')
    assert not undisposed(naked, entry)
    assert not entry_problems(entry, naked)
    assert entry_problems(entry.replace("one provider of three", "something else"), naked)


def test_a_clause_quoted_as_a_code_token_is_prose_about_the_convention():
    """R2596's lesson: a comment quoting its own marker reddened `main` itself. A doc or a release
    entry DESCRIBING this convention must not red the gate it describes."""
    head = "## [9.9.9] — 2026-01-01\n\n### Added\n"
    about = head + "- No `Known limit:` clause ships without one of three dispositions.\n"
    assert not clauses(about), clauses(about)
    assert not undisposed(about, "")
    real = head + "- A thing shipped. Known limit: the second half is unbuilt.\n"
    assert len(clauses(real)) == 1


def test_the_store_is_reachable_and_states_the_contract_the_check_enforces():
    """A docs page nothing serves is half a deliverable, and a vocabulary the store does not
    teach is a check whose fix nobody can find."""
    assert LIMITS_PATH.is_file(), "docs/limits.md is the store this gate routes limits into"
    assert "limits" in GUIDE_ORDER, ("docs/limits.md is served either way, but GUIDE_ORDER is "
                                     "where the Help tab's reading order is decided")
    text = LIMITS_PATH.read_text(encoding="utf-8")
    for marker in (*_OPENERS, *_INLINE):
        assert marker in text, f"the store does not teach the declaration marker {marker!r}"
    assert "Accepted permanently:" in text
    assert DESIGNS_DOC in text and "report" in text
    for field in REQUIRED_FIELDS:
        assert field in text, f"the store's contract does not name its own field {field!r}"
