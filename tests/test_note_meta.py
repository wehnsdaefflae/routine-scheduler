"""D268: a note's `stale_after` and `verified` as DATA — declared, rendered, merged, judged.

The failure these guard against is the silent one. A sub-field absent from `KIND_FIELDS` is
stripped by `normalize_action` with no error at all, so the first test here is that the fields
survive the contract rather than that they render nicely; and a note WITHOUT them must keep
behaving exactly as it does today, which is every note on the instance.
"""

from __future__ import annotations

import datetime as dt

from helpers import run_context, server_config
from rsched.engine import executor, notemeta
from rsched.engine.actions import normalize_action, validate_action
from rsched.engine.actionschema import ACTION_SCHEMA
from rsched.engine.composer import state_digest
from rsched.engine.observations import format_observation


def _ctx(make_routine, tmp_path, slug="notemeta"):
    return run_context(make_routine(slug=slug), "20261108-070000",
                       server=server_config(libraries_home=tmp_path / "libraries"))


def _write(ctx, name, content, about=None, **extra):
    action = {"kind": "memory_write", "name": name, "content": content, **extra}
    if about is not None:
        action["about"] = about
    return executor.dispatch(action, ctx)


def _index(ctx):
    return (ctx.routine.dir / ".memory" / "INDEX.md").read_text(encoding="utf-8")


def test_the_fields_are_declared_on_the_kind_and_survive_the_contract():
    """Trap 1: an undeclared sub-field is DROPPED by `normalize_action` and nothing says so,
    so the handler would never see it however well the rest worked.
    """
    action = {"say": "s", "kind": "memory_write", "name": "n", "content": "c", "about": "a",
              "stale_after": "2026-12-01", "verified": {"by": "human:operator",
                                                        "at": "2026-10-02"}}
    kept = normalize_action(dict(action))
    assert kept["stale_after"] == "2026-12-01"
    assert kept["verified"] == {"by": "human:operator", "at": "2026-10-02"}
    assert validate_action(kept) == []
    for field in notemeta.FIELDS:
        assert field in ACTION_SCHEMA["properties"], field


def test_the_index_line_carries_both_markers(make_routine, tmp_path):
    ctx = _ctx(make_routine, tmp_path)
    obs = _write(ctx, "portal", "# quirks", "scraping gotchas", stale_after="2026-12-01",
                 verified={"by": "human:operator", "at": "2026-10-02"})
    assert _index(ctx) == ("- portal.md: scraping gotchas · stale after 2026-12-01"
                           " · operator-verified 2026-10-02\n")
    assert obs["meta"]["stale_after"] == "2026-12-01"
    # the run has to SEE what was recorded — what notemeta could not render was left out
    rendered = format_observation(obs)
    assert "stale after 2026-12-01" in rendered
    assert "operator-verified 2026-10-02" in rendered


def test_a_note_without_the_fields_is_untouched(make_routine, tmp_path):
    """Back-compat, which is every note on the instance: no fields, no markers, no `meta` key."""
    ctx = _ctx(make_routine, tmp_path)
    obs = _write(ctx, "plain", "# plain", "what it holds")
    assert _index(ctx) == "- plain.md: what it holds\n"
    assert "meta" not in obs
    assert notemeta.index_suffix({}) == "" and notemeta.index_suffix(None) == ""


def test_a_revision_keeps_a_date_it_does_not_restate(make_routine, tmp_path):
    """The markers MERGE. Replace-only semantics would make every ordinary revision drop
    whatever expiry an earlier run had recorded.
    """
    ctx = _ctx(make_routine, tmp_path)
    _write(ctx, "roster", "v1", "who is on call", stale_after="2026-12-01")
    _write(ctx, "roster", "v2", "who is on call, revised")
    assert _index(ctx) == ("- roster.md: who is on call, revised · stale after 2026-12-01\n")
    _write(ctx, "roster", "v3", verified={"by": "run", "at": "2026-11-08"})
    assert _index(ctx) == ("- roster.md: who is on call, revised · stale after 2026-12-01"
                           " · run-verified 2026-11-08\n")


def test_an_unrenderable_value_is_left_out_and_the_note_still_lands(make_routine, tmp_path):
    """Optional metadata on a write whose real payload is the note: refusing the turn over a
    malformed date would cost the note. The observation names what WAS recorded.
    """
    ctx = _ctx(make_routine, tmp_path)
    obs = _write(ctx, "odd", "# body", "a subject", stale_after="soon",
                 verified={"by": "root", "at": "2026-10-02"})
    assert obs["created"] and "meta" not in obs
    assert _index(ctx) == "- odd.md: a subject\n"
    assert notemeta.from_action({"verified": {"by": "human:operator", "at": "nope"}}) == {}


def test_a_stale_note_gets_its_own_digest_heading(make_routine, tmp_path):
    ctx = _ctx(make_routine, tmp_path)
    _write(ctx, "fresh", "a", "still true", stale_after="2099-01-01")
    _write(ctx, "expired", "b", "priced in July", stale_after="2026-07-30")
    digest = state_digest(ctx.routine.dir, [], [])
    assert ".memory/ notes PAST THEIR stale_after" in digest
    assert "- expired.md: priced in July · stale since 2026-07-30" in digest
    assert "- fresh.md: still true · stale after 2099-01-01" in digest
    index_block = digest.split(notemeta.STALE_HEADING)[0]
    assert "expired.md" not in index_block        # never among the notes it says to consult


def test_partition_is_inclusive_of_the_date_itself():
    """`stale_after` is the last day the note is trusted: it goes stale the day AFTER."""
    lines = ["- n.md: x · stale after 2026-07-30"]
    assert notemeta.partition_stale(lines, on=dt.date(2026, 7, 30)) == (lines, [])
    assert notemeta.partition_stale(lines, on=dt.date(2026, 7, 31)) == ([], lines)
    assert notemeta.partition_stale(["- p.md: no date"], on=dt.date(2026, 7, 31)) == (
        ["- p.md: no date"], [])


def test_markers_round_trip_off_the_index_line():
    line = ("- n.md: what it holds · stale after 2026-12-01"
            " · operator-verified 2026-10-02")
    assert notemeta.about_of(line) == "what it holds"
    assert notemeta.meta_of(line) == {"stale_after": "2026-12-01",
                                      "verified": {"by": "human:operator", "at": "2026-10-02"}}
    assert notemeta.index_suffix(notemeta.meta_of(line)) == line.split("holds", 1)[1]
