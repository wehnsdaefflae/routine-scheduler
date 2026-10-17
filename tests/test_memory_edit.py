"""memory_write EDIT MODE — `anchor`/`replacement` against an existing note (R2432/R2456).

Revising a mature note meant re-emitting all of it. `vodafone-outage-claims` composed a
104-line revision of `vodafone-case-state`, was rejected at the 100-line cap, hand-trimmed it
and composed it again — on a note created by the FIX for the same problem one day earlier
(R2391 split one topic into four, which buys headroom and does not change the mechanism: each
note grows back toward the cap and the same rejection returns).

Two halves of one fix, which is why they are one change: an in-place edit means a run adding
facts to a 95-line note never re-emits the note, so the cap cannot reject work that was never
recomposed — and the cap is evaluated against the RESULT, so an edit that keeps a note under it
is never composed twice. `edit_file` and `write_util` already carry the mode; this is the third
caller of the same `authoring.anchor_edit` helper, so an anchor failure teaches the same route.
"""

from __future__ import annotations

from helpers import run_context, server_config
from rsched.engine import executor
from rsched.engine.actions import MEMORY_NOTE_MAX_LINES, validate_action
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


def test_the_cap_is_measured_against_the_result_not_the_submission(make_routine, tmp_path):
    """The half that makes edit mode worth more than it looks: a note at the cap can still be
    revised, because what is measured is the note after the patch — not the text submitted."""
    ctx = _ctx(make_routine, tmp_path)
    body = "\n".join(f"- fact {i}" for i in range(MEMORY_NOTE_MAX_LINES))
    _write(ctx, "big", body, "a mature note")
    obs = _edit(ctx, "big", "- fact 0", "- fact 0 (confirmed 2026-10-11)")
    assert "error" not in obs and obs["lines"] == MEMORY_NOTE_MAX_LINES


def test_an_edit_that_would_exceed_the_cap_is_refused_with_the_number(make_routine, tmp_path):
    ctx = _ctx(make_routine, tmp_path)
    body = "\n".join(f"- fact {i}" for i in range(MEMORY_NOTE_MAX_LINES))
    _write(ctx, "big", body, "a mature note")
    obs = _edit(ctx, "big", "- fact 0", "- fact 0\n- fact 0b\n- fact 0c")
    assert obs.get("error")
    assert str(MEMORY_NOTE_MAX_LINES + 2) in obs["error"] and "capped" in obs["error"]
    # and the note on disk is UNCHANGED, like every other refused write
    assert (ctx.routine.dir / ".memory" / "big.md").read_text(encoding="utf-8").startswith(
        "- fact 0\n- fact 1\n")


def test_the_refusal_carries_the_budget_and_not_only_the_overrun(make_routine, tmp_path):
    """R2610: the message stated the RESULT size, from which no caller can compute what WOULD
    fit. One run met five consecutive rejections, three on the same note — which sat at 92 of
    100 lines, so no net addition could ever have succeeded, while "make the replacement
    shorter" invited exactly the trim-and-retry loop it got. The numbers that determine the
    next attempt are the note's current size and the anchor's, and when the budget is zero the
    way out is replacing MORE of the note, which the message now names.
    """
    ctx = _ctx(make_routine, tmp_path)
    body = "\n".join(f"- fact {i}" for i in range(MEMORY_NOTE_MAX_LINES - 8))
    _write(ctx, "nearly-full", body, "a mature note")
    obs = _edit(ctx, "nearly-full", "- fact 0",
                "\n".join(f"- new {i}" for i in range(12)))
    error = obs["error"]
    assert f"currently has {MEMORY_NOTE_MAX_LINES - 8} lines" in error, error
    assert "anchor covers 1" in error, error
    assert "fits in 9 line(s)" in error, error        # 100 - (92 - 1)
    assert "LARGER anchor" in error, error


def test_a_note_at_the_cap_is_told_its_budget_is_zero(make_routine, tmp_path):
    """The case that made the old wording actively misleading: at the cap, the budget for a
    one-line anchor is exactly that one line, so "shorter" is unreachable advice unless the
    anchor grows. The number says so instead of implying a retry."""
    ctx = _ctx(make_routine, tmp_path)
    body = "\n".join(f"- fact {i}" for i in range(MEMORY_NOTE_MAX_LINES))
    _write(ctx, "at-cap", body, "a mature note")
    obs = _edit(ctx, "at-cap", "- fact 0", "- fact 0\n- extra")
    assert "fits in 1 line(s)" in obs["error"], obs["error"]


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
