"""F778: what a RESUMED leg is TOLD about the children that died with its earlier leg.

F732 made the dead children VISIBLE (`subruns` lists their directories, `wait`/`kill` refuse
them with the reason). It did not change what the resumed leg is told at boot, and that note
said the opposite of the truth: *"these child tasks were RUNNING when the run was interrupted
and did NOT survive the restart (results lost): … Re-issue any you still need."*

Measured cost, 2026-10-25: `scheduler-builder:20261025-140002` died at turn ~285 with two live
children holding ~1,400 s of real build work each. Their work was on disk the whole time under
`sub/<n>/`; the resumed parent was told it was lost and had to find it by hand.

What is pinned here:
  * the note never says the results are lost, and never tells the leg to re-issue blindly;
  * it names each dead child's DIRECTORY, what landed in its `artifacts/`, and how far it got;
  * it names the child's surviving `instruction.md` — the original brief a re-brief needs —
    and only when that file really exists (a promise the disk cannot support is worse than
    no offer: a failed salvage must not read as a successful one);
  * it states that RE-ADOPTION is impossible, with the reason (a child is a thread in the
    parent's process — docs/child-runs.md § Process model), so a leg does not go looking;
  * no orphans means no note at all.
"""

from __future__ import annotations

import json

from rsched.engine import subsalvage


def _dead_child(run_dir, n, *, label="worker", mode="parallel", workflow="general-task",
                turns=0, brief=True, artifacts=()):
    """A child directory exactly as an earlier leg left it (childrun.materialize_to_disk +
    the child's own transcript), with `brief=False` for one killed before its brief landed."""
    d = run_dir / "sub" / str(n)
    d.mkdir(parents=True)
    if brief:
        (d / "instruction.md").write_text(f"the original brief for {label}\n", encoding="utf-8")
    rows = [{"type": "run_started", "label": label, "workflow": workflow}]
    rows += [{"type": "action", "turn": t, "payload": {}} for t in range(1, turns + 1)]
    (d / "transcript.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    if artifacts:
        (d / "artifacts").mkdir()
        for name in artifacts:
            (d / "artifacts" / name).write_text("handed back\n", encoding="utf-8")
    return d


def _orphan(n, *, label="worker", mode="parallel", workflow="general-task"):
    """One row as `history.orphaned_children` hands it to boot."""
    return {"n": n, "label": label, "mode": mode, "workflow": workflow}


def test_the_note_no_longer_claims_the_results_are_lost(tmp_path):
    """The defect itself: the sentence the resumed parent read, over work that was on disk."""
    _dead_child(tmp_path, 1, turns=42, artifacts=("F700.md",))
    note = subsalvage.resume_note(tmp_path, [_orphan(1)])
    assert "results lost" not in note
    assert "Re-issue any you still need" not in note
    assert "NOT gone" in note


def test_the_note_names_the_directory_and_the_hand_back(tmp_path):
    """Salvage means the leg can GO AND LOOK without searching the runs tree."""
    d = _dead_child(tmp_path, 2, label="builder", turns=37, artifacts=("F700.md", "notes.md"))
    note = subsalvage.resume_note(tmp_path, [_orphan(2, label="builder")])
    assert str(d) in note
    assert "F700.md" in note and "notes.md" in note
    assert "'builder'" in note
    assert "turn 37" in note          # how much work died with it, from its own transcript


def test_a_child_that_handed_nothing_back_says_so_rather_than_implying_files(tmp_path):
    _dead_child(tmp_path, 1, turns=3)
    note = subsalvage.resume_note(tmp_path, [_orphan(1)])
    assert "nothing in artifacts/" in note


def test_the_note_offers_the_original_brief_for_a_re_brief(tmp_path):
    """Re-brief is only possible because `instruction.md` is written at spawn time and
    outlives the leg — so the note points at the actual file."""
    d = _dead_child(tmp_path, 1, turns=9)
    note = subsalvage.resume_note(tmp_path, [_orphan(1)])
    assert str(d / "instruction.md") in note
    assert "ORIGINAL BRIEF" in note
    assert "RE-BRIEF" in note


def test_no_brief_on_disk_is_never_promised(tmp_path):
    """A failed salvage must not read as a successful one. A child killed before its brief
    landed has nothing to carry forward, and the note must not offer it."""
    _dead_child(tmp_path, 1, brief=False, turns=1)
    note = subsalvage.resume_note(tmp_path, [_orphan(1)])
    assert "ORIGINAL BRIEF" not in note
    assert "instruction.md" not in note
    assert str(tmp_path / "sub" / "1") in note     # the directory is still named


def test_the_note_rules_re_adoption_out_with_the_reason(tmp_path):
    """A leg told only "it died" goes looking for a way to pick the child back up. There is
    none: the child is an EngineLoop on a daemon thread inside this process."""
    _dead_child(tmp_path, 1, turns=5)
    note = subsalvage.resume_note(tmp_path, [_orphan(1)])
    assert "RE-ADOPT" in note
    assert "THREAD" in note
    assert "wait" in note and "kill" in note


def test_every_dead_child_gets_its_own_line(tmp_path):
    """The 2026-10-25 incident had TWO live children; a note naming one of them is half a
    salvage."""
    _dead_child(tmp_path, 1, label="f700", turns=120, artifacts=("F700.md",))
    _dead_child(tmp_path, 2, label="f732", mode="sequential", turns=88)
    note = subsalvage.resume_note(tmp_path, [_orphan(1, label="f700"),
                                             _orphan(2, label="f732", mode="sequential")])
    assert "'f700'" in note and "'f732'" in note
    assert "turn 120" in note and "turn 88" in note
    assert "sequential" in note


def test_no_orphans_is_no_note(tmp_path):
    """A leg whose children all finished must not read a salvage paragraph about nothing."""
    assert subsalvage.resume_note(tmp_path, []) == ""


def test_a_missing_directory_does_not_crash_the_boot(tmp_path):
    """The orphan list comes from the TRANSCRIPT, so a row can name a directory that is not
    there (a pruned run dir). Boot must still compose its note — a diagnostic that can stop a
    resume is worse than the gap it reports."""
    note = subsalvage.resume_note(tmp_path, [_orphan(7)])
    assert "#7" in note
    assert "turn 0" in note           # unknown, stated rather than invented
    assert "ORIGINAL BRIEF" not in note
