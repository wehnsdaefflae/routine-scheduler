"""What `GET /api/items` WEIGHS — the archive section carries the line it renders (F560).

The finding measured 232,543 bytes for 51 open rows on the live instance, and every reader of
the endpoint (this routine's own queue script, self-audit's folds) spilled the answer to a file
and folded it with a helper before it was readable. Measured again 2026-10-19: of the
`changelog` key's 95,641 bytes, 85,931 were release summaries — 8-20 lines of markdown each,
of which `static/md.js::summaryLine` renders the FIRST LINE and drops the rest.

So this is not a cap on what the page may show. It is the server stopping shipping what the
page throws away. An item's own history keeps its full prose, server-side, on its card
(`addressed`), which is where a reader of one item looks.
"""

from __future__ import annotations

import json

from rsched.web.api_items import CHANGELOG_SUMMARY_CHARS, _light_changelog

LONG_SUMMARY = (
    "F999 — the headline a reader scans.\n"
    "\n"
    "Then eighteen more lines of release prose, which the changelog section never renders "
    "because `summaryLine` takes the first non-blank line and stops.\n"
    + "filler that nobody on this page ever sees. " * 40
)


def test_the_changelog_carries_the_first_line_the_page_actually_renders():
    rows = [{"ts": "2026-10-19T00:00:00Z", "commit": "abc123", "run_id": "r1",
             "items": ["F999"], "summary": LONG_SUMMARY}]
    light = _light_changelog(rows)
    assert light[0]["summary"].startswith("F999 — the headline a reader scans."), light[0]
    assert "filler that nobody" not in light[0]["summary"], (
        "the whole release summary is still riding along; the page renders one line of it")


def test_a_clipped_row_says_its_prose_continues():
    """A truncation a reader cannot see is worse than the weight: it reads as the whole entry."""
    rows = [{"summary": LONG_SUMMARY}]
    assert "…" in _light_changelog(rows)[0]["summary"], (
        "nothing marks the clip, so a reader takes the first line for the entire change")


def test_a_short_summary_is_passed_through_untouched():
    """Most rows are one line. Those must be byte-identical — a marker on a complete entry
    would be a lie about the archive, in the direction nobody checks.
    """
    rows = [{"ts": "t", "commit": "c", "summary": "one line and that is all"}]
    assert _light_changelog(rows)[0]["summary"] == "one line and that is all"


def test_every_other_key_of_a_changelog_row_survives():
    """The row is CLIPPED, not rebuilt: the page needs `ts` and `commit`, and a future reader
    needs `items` and `run_id` to join a change to what it addressed.
    """
    row = {"ts": "t", "commit": "c", "run_id": "r", "items": ["D1"], "repo": "x",
           "version": "0.1.0", "done": ["D1"], "summary": "s"}
    light = _light_changelog([row])[0]
    assert {k: v for k, v in light.items() if k != "summary"} == \
           {k: v for k, v in row.items() if k != "summary"}


def test_the_clip_is_bounded_even_for_a_single_enormous_line():
    """A run that writes its whole summary as ONE line must not defeat the measure."""
    rows = [{"summary": "x" * 5000}]
    clipped = _light_changelog(rows)[0]["summary"]
    assert len(clipped) <= CHANGELOG_SUMMARY_CHARS + 1, len(clipped)


def test_sixty_rows_of_real_shaped_prose_fit_in_a_fraction_of_the_old_payload():
    """The number the finding is about. 60 release-shaped rows weighed ~95 KB; the measure has
    to be a fraction of that, or the readers keep spilling the answer to a file.
    """
    rows = [{"ts": "t", "commit": "c" * 40, "run_id": "r" * 15, "items": ["D1", "F2"],
             "summary": LONG_SUMMARY} for _ in range(60)]
    before = len(json.dumps(rows))
    after = len(json.dumps(_light_changelog(rows)))
    assert after < before / 3, (
        f"{before} -> {after} bytes is not a fraction: every reader of /api/items still has to "
        f"spill the answer to a file before it can be read")
