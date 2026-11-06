"""memory_write EDIT MODE — `anchor`/`replacement` against an existing note (R2432/R2456).

Revising a mature note meant re-emitting all of it. `vodafone-outage-claims` composed a
104-line revision of `vodafone-case-state`, was rejected at the 100-line cap, hand-trimmed it
and composed it again — on a note created by the FIX for the same problem one day earlier
(R2391 split one topic into four, which buys headroom and does not change the mechanism: each
note grows back toward the cap and the same rejection returns).

An in-place edit means a run adding facts to a 95-line note never re-emits the note.
`edit_file` and `write_util` already carry the mode; this is the third caller of the same
`authoring.anchor_edit` helper, so an anchor failure teaches the same route.

The cap itself is gone (P3: a guard whose fix is mechanical applies it): every refusal it made —
five tests here once pinned their numbers, R2610/R2753/R2773 — was a run trimming text to fit a
length nothing else depended on. A note past the advised size now LANDS and is told by how much.
"""

from __future__ import annotations

from helpers import run_context, server_config
from rsched.engine import executor
from rsched.engine.actions import validate_action
from rsched.engine.memops import MEMORY_NOTE_ADVISED_LINES
from rsched.engine.observations import format_observation


def _ctx(make_routine, tmp_path):
    return run_context(make_routine(slug="memedit"), "20260712-070000",
                       server=server_config(libraries_home=tmp_path / "libraries"))


def _write(ctx, name, content, about):
    return executor.dispatch({"kind": "memory_write", "name": name,
                              "content": content, "about": about}, ctx)


def _edit(ctx, name, anchor, replacement, **extra):
    return executor.dispatch({"kind": "memory_write", "name": name, "anchor": anchor,
                              "replacement": replacement, **extra}, ctx)


def test_an_anchor_edit_patches_the_note_in_place(make_routine, tmp_path):
    ctx = _ctx(make_routine, tmp_path)
    note = ctx.routine.dir / ".memory" / "case-state.md"
    _write(ctx, "case-state", "# case\n- claim filed 2026-09-01\n- awaiting reply\n",
           "the open claim's state")

    obs = _edit(ctx, "case-state", "- awaiting reply",
                "- reply received 2026-10-11, refund promised")
    assert "error" not in obs and obs.get("edited") and obs["replacements"] == 1
    assert note.read_text(encoding="utf-8") == (
        "# case\n- claim filed 2026-09-01\n- reply received 2026-10-11, refund promised\n")
    assert obs["lines"] == 3
    assert "replaced 1 occurrence" in format_observation(obs)


def test_an_edit_keeps_the_notes_index_line(make_routine, tmp_path):
    """`about` is not required in edit mode — the note's subject has not changed — and the
    engine-owned INDEX line must survive untouched. A revised `about` still updates it."""
    ctx = _ctx(make_routine, tmp_path)
    index = ctx.routine.dir / ".memory" / "INDEX.md"
    _write(ctx, "case-state", "# case\n- one\n", "the open claim's state")
    _edit(ctx, "case-state", "- one", "- two")
    assert index.read_text(encoding="utf-8") == "- case-state.md: the open claim's state\n"
    _edit(ctx, "case-state", "- two", "- three", about="the claim, now with the refund")
    assert index.read_text(encoding="utf-8") == (
        "- case-state.md: the claim, now with the refund\n")


def test_an_edit_past_the_advised_size_lands_and_says_by_how_much(make_routine, tmp_path):
    """The three refusals for one pointer line ("shortening by one line to fit") are what this
    replaces: the edit is WRITTEN, and the observation carries one line naming the overrun and
    the move for next time — never a refusal to trim against."""
    ctx = _ctx(make_routine, tmp_path)
    body = "\n".join(f"- fact {i}" for i in range(MEMORY_NOTE_ADVISED_LINES))
    _write(ctx, "big", body, "a mature note")
    obs = _edit(ctx, "big", "- fact 0", "- fact 0\n- fact 0b\n- fact 0c")
    assert "error" not in obs
    assert obs["lines"] == MEMORY_NOTE_ADVISED_LINES + 2 and obs["over_advised"] == 2
    assert "- fact 0c" in (ctx.routine.dir / ".memory" / "big.md").read_text(encoding="utf-8")
    seen = format_observation(obs)
    assert "2 line(s) over the advised ~100" in seen and "split part of it" in seen


def test_a_whole_note_past_the_advised_size_is_written_too(make_routine, tmp_path):
    """Schema-side the bound was a validate_action refusal; it is no longer a problem at all."""
    body = "\n".join(f"- fact {i}" for i in range(MEMORY_NOTE_ADVISED_LINES + 5))
    action = {"say": "s", "kind": "memory_write", "name": "big", "content": body, "about": "a"}
    assert validate_action(action) == []
    obs = _write(_ctx(make_routine, tmp_path), "big", body, "a")
    assert obs["over_advised"] == 5 and "5 line(s) over" in format_observation(obs)


def test_a_note_within_the_advised_size_says_nothing_about_size(make_routine, tmp_path):
    ctx = _ctx(make_routine, tmp_path)
    obs = _write(ctx, "small", "- one\n- two", "a short note")
    assert "over_advised" not in obs and "advised" not in format_observation(obs)


def test_a_stale_or_ambiguous_anchor_is_refused_loudly(make_routine, tmp_path):
    ctx = _ctx(make_routine, tmp_path)
    _write(ctx, "case-state", "# case\n- one\n- one\n", "the open claim's state")

    missing = _edit(ctx, "case-state", "- not in the note", "x")
    assert missing.get("error") and "anchor not found" in missing["error"]
    assert "memory_read" in missing["error"], "the refusal must say where to copy it from"

    ambiguous = _edit(ctx, "case-state", "- one", "- two")
    assert ambiguous.get("error") and "occurs 2×" in ambiguous["error"]

    both = _edit(ctx, "case-state", "- one", "- two", all=True)
    assert "error" not in both and both["replacements"] == 2
    assert (ctx.routine.dir / ".memory" / "case-state.md").read_text(
        encoding="utf-8") == "# case\n- two\n- two\n"


def test_editing_a_note_that_does_not_exist_teaches_the_whole_write(make_routine, tmp_path):
    ctx = _ctx(make_routine, tmp_path)
    obs = _edit(ctx, "nothing-here", "a", "b")
    assert obs.get("error") and "no note" in obs["error"]
    assert "'content'" in obs["error"], "it must name the way to create one"
    assert obs.get("topics") == [], "and say what notes do exist"


def test_the_validator_accepts_edit_mode_and_still_demands_a_whole_note(make_routine, tmp_path):
    """`content` + `about` stay REQUIRED for a whole-note write; edit mode needs neither, and
    an edit carrying `content` too is refused rather than silently preferring one."""
    assert validate_action({"say": "s", "kind": "memory_write", "name": "t",
                            "anchor": "a", "replacement": "b"}) == []
    assert validate_action({"say": "s", "kind": "memory_write", "name": "t",
                            "anchor": "a", "replacement": "b",
                            "about": "revised subject"}) == []
    # an anchor with no replacement is a DELETION of that text — legal, like edit_file's
    assert validate_action({"say": "s", "kind": "memory_write", "name": "t",
                            "anchor": "a"}) == []
    problems = validate_action({"say": "s", "kind": "memory_write", "name": "t",
                                "content": "x", "about": "a", "anchor": "q"})
    assert problems and any("anchor" in p for p in problems)
    # the whole-note form is unchanged: content and about are still required
    assert any("content" in p for p in
               validate_action({"say": "s", "kind": "memory_write", "name": "t",
                                "about": "a"}))
