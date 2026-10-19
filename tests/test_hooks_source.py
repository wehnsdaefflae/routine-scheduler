"""The hook stream as the SOURCE rather than the mirror — D152 campaign step 4.

Steps 2 and 3 left `reports.jsonl` and the reminders store as their own writers, with
`.control/hooks.jsonl` a best-effort mirror beside them. That is the duplication this step
retires, and these are the tests that say what "retired" means — because the file itself stays
(it has readers outside this engine: the operator's backlog counts, self-audit's folds):

1. the hook is written FIRST and the ledger row is a pure projection of it, computed in one
   place (`hooks/reportproj.py`);
2. the stream carries what the mirror dropped — the whole disposal (`answers`, `closes`,
   `settles`, `supersedes`) and the lifecycle events — so the ledger can be REBUILT from it,
   which is the testable form of "one source";
3. a hook write that fails REFUSES the filing instead of leaving the two stores disagreeing:
   the mirror was best-effort precisely because it was not the source, and the moment it
   becomes the source that trade reverses;
4. the reminder half records a DISPOSITION change, which step 2's mirror never saw at all.
"""

from __future__ import annotations

from conftest import finish, util, write_file
from helpers import server_for, set_capabilities
from rsched import reminders as remstore
from rsched import reports
from rsched.engine.runtime import run_routine
from rsched.hooks import reportproj
from rsched.hooks import store as hookstore

TS = "20260919-090000"


def _home(tmp_path):
    home = tmp_path / "routines"
    (home / ".control").mkdir(parents=True)
    return home


def _file(home, **kw):
    kw.setdefault("routine", "builder")
    kw.setdefault("run_id", "builder:1")
    filed = reports.file_report(home, **kw)
    assert filed is not None
    return filed[1]


# --- one writer, one projection -------------------------------------------------------------


def test_the_ledger_row_is_the_projection_of_the_hook(tmp_path):
    """The claim of this step in one assertion: the row on disk is what `reportproj` computes
    from the hook, key for key. Two writers can disagree; a writer and a projection cannot.
    """
    home = _home(tmp_path)
    rid = _file(home, title="the gate reds on main", detail="see run X", target="self-audit")

    hook = next(h for h in hookstore.read_hooks(home) if h.id == rid)
    rows = [r for r in reports.read_reports(reports.reports_path(home)) if r["id"] == rid]

    assert rows == [reportproj.ledger_row(hook)]


def test_the_stream_carries_the_whole_disposal(tmp_path):
    """What step 2's mirror dropped. A stream that does not record what a report DISPOSED OF
    cannot reproduce the ledger, and a store that cannot reproduce what it replaced is a mirror
    whatever it is called.
    """
    home = _home(tmp_path)
    first = _file(home, title="a defect", detail="d", target="self-audit")
    reply = _file(home, routine="self-audit", run_id="self-audit:1", title="fixed", detail="d",
                  target="builder",
                  disposal=reports.Disposal(answers=first, closes=True))

    hook = next(h for h in hookstore.read_hooks(home) if h.id == reply)

    assert hook.payload["answers"] == first
    assert hook.payload["closes"] is True
    assert hook.addressee == "routine:builder"


def test_a_fold_is_two_rows_about_two_ids_on_one_stream(tmp_path):
    """`supersedes` folds a row into a carrier: the carrier's hook names what it took over, and
    the folded id gets its OWN later row — the append-only way to say "this is now that
    thread".
    """
    home = _home(tmp_path)
    old = _file(home, title="routed once", detail="d")

    carrier = _file(home, title="taking it over", detail="d", target="self-audit",
                    supersedes=(old,))

    rows = [h for h in hookstore.read_hooks(home) if h.source == "report"]
    by_id = {}
    for h in rows:
        by_id.setdefault(h.id, []).append(h)
    assert by_id[carrier][0].payload["supersedes"] == [old]
    fold = by_id[old][-1]
    assert fold.payload == {"event": "superseded", "by": carrier, "to": "self-audit"}
    assert fold.disposition == "settled"


def test_a_delivery_and_a_retraction_are_facts_on_the_stream(tmp_path):
    """The lifecycle, which step 2 did not record at all: a report is filed, picked up, and
    sometimes withdrawn. `delivered` leaves the thread OPEN — the target owes an answer — while
    a retraction settles it.
    """
    home = _home(tmp_path)
    target_dir = home / "self-audit"
    (target_dir / "inbox").mkdir(parents=True)
    rid = _file(home, title="pick this up", detail="d", target="self-audit",
                target_dir=target_dir)

    reports.stamp_delivered(home, [{"report": rid}], run_id="self-audit:2")
    events = [h for h in hookstore.read_hooks(home) if h.id == rid and h.payload.get("event")]

    assert [h.payload["event"] for h in events] == ["delivered"]
    assert events[0].payload["run_id"] == "self-audit:2"
    assert events[0].disposition == "open"

    second = _file(home, title="withdraw me", detail="d", target="self-audit",
                   target_dir=target_dir)
    reports.retract_report(home, second)
    retracted = [h for h in hookstore.read_hooks(home)
                 if h.id == second and h.payload.get("event") == "retracted"]

    assert len(retracted) == 1
    assert retracted[0].disposition == "settled"


def test_the_ledger_rebuilds_from_the_hook_stream_alone(tmp_path):
    """The whole campaign step, measurable: fold the hook stream and fold the file, and the two
    must agree row for row. While that holds, the file is a derived view and the duplication is
    gone even though the bytes stay — which is what the design's "wrap first, replace last"
    asked for.
    """
    home = _home(tmp_path)
    target_dir = home / "self-audit"
    (target_dir / "inbox").mkdir(parents=True)
    one = _file(home, title="one", detail="d", target="self-audit", target_dir=target_dir)
    two = _file(home, title="two", detail="d")
    _file(home, routine="self-audit", run_id="self-audit:1", title="answering", detail="d",
          target="builder", disposal=reports.Disposal(answers=one, closes=True))
    _file(home, title="folding", detail="d", target="self-audit", supersedes=(two,))
    reports.stamp_delivered(home, [{"report": one}], run_id="self-audit:2")

    rebuilt = reportproj.rebuild(home)
    ledger = reports.read_reports(reports.reports_path(home))

    assert rebuilt == ledger


def test_a_hook_write_failure_refuses_the_filing(tmp_path, monkeypatch):
    """The trade that reverses when a mirror becomes the source. Step 2's mirror was
    best-effort because the ledger was the truth; now the hook IS the truth, so a filing whose
    hook cannot be written must not leave a ledger row behind it — one store silently ahead of
    the other is the exact failure the unification exists to end.
    """
    def boom(*_a, **_k):
        raise OSError("the control directory is read-only")

    home = _home(tmp_path)
    monkeypatch.setattr(hookstore, "write", boom)

    assert reports.file_report(home, routine="builder", run_id="builder:1", title="t",
                               detail="d", target="self-audit") is None
    assert reports.read_reports(reports.reports_path(home)) == []


# --- the reminder half ----------------------------------------------------------------------


def test_a_disposition_is_recorded_on_the_stream(make_routine, scripted):
    """Step 2 mirrored a reminder's WRITE and never its disposition, so the one store could not
    answer what happened to a hook — the question a store that owns the mechanism has to
    answer. The target's say over a foreign hook is now a later row about the same id.
    """
    d = make_routine(slug="receiver")
    set_capabilities(d, reminders="local")
    foreign = remstore.Reminder(id="rem-fo", regex="^util:danger", description="setter's",
                                scope="local", kind="action", owner="setter",
                                created_run="setter:1", stats=remstore.blank_stats())
    remstore.save_local(d, [foreign], {})
    scripted([util("danger"),
              {**write_file("state/x.txt", content="x"),
               "remind_feedback": {"id": "rem-fo", "label": "could_not",
                                   "disposition": "mute"}},
              finish()])

    status, _ = run_routine(d, server_for(d), run_ts=TS)
    rows = [h for h in hookstore.read_hooks(d.parent)
            if h.source == "reminder" and h.id == "rem-fo"]

    assert status == "ok"
    assert rows and rows[-1].disposition == "mute"
    assert rows[-1].addressee == "routine:receiver"


def test_the_reminder_store_is_derivable_from_the_stream(make_routine, scripted):
    """The reminder half of "make one the source and DERIVE the other". The definitions a run
    would hold are computed from the hook stream and compared against the store it actually
    read — `drift` empty is the claim, and a non-empty answer would NAME the hook that was
    written without going through the write path.

    The tallies are deliberately NOT derived: `fires` and the four labels are per-routine
    evidence with their own writer (`reminders.record`), and mirroring a counter would give one
    number two writers — the defect this campaign removes rather than spreads.
    """
    d = make_routine(slug="remr")
    set_capabilities(d, reminders="local")
    scripted([{"say": "Noting one.", "kind": "read_file", "path": "LEDGER.md",
               "remind": {"op": "add", "regex": "^util:fs-ops mv ",
                          "description": "moving a served file breaks the live page"}},
              {"say": "And a result watcher.", "kind": "read_file", "path": "LEDGER.md",
               "remind": {"op": "add", "kind": "result", "regex": "exit 137",
                          "description": "the OOM killer took it; halve the batch"}},
              finish()])

    status, _ = run_routine(d, server_for(d), run_ts=TS)
    from rsched.hooks import reminderproj

    derived = reminderproj.definitions(d.parent, routine="remr")

    assert status == "ok"
    assert sorted(v["regex"] for v in derived.values()) == ["^util:fs-ops mv ", "exit 137"]
    assert {v["kind"] for v in derived.values()} == {"action", "result"}
    assert all(v["created_run"] for v in derived.values())
    assert reminderproj.drift(d.parent, d) == {}


def test_a_removed_hook_leaves_the_derived_view(make_routine, scripted):
    """`remove` is the one subtractive disposition: the hook is GONE, not present-and-silent.
    A `mute` stays a definition — kept for its owner to see — so only one of the three takes a
    row out of the derivation, and the derived view has to tell them apart or it disagrees with
    the store on the next run.
    """
    d = make_routine(slug="receiver2")
    set_capabilities(d, reminders="local")
    foreign = remstore.Reminder(id="rem-go", regex="^util:danger", description="setter's",
                                scope="local", kind="action", owner="setter",
                                created_run="setter:1", stats=remstore.blank_stats())
    remstore.save_local(d, [foreign], {})
    scripted([util("danger"),
              {**write_file("state/x.txt", content="x"),
               "remind_feedback": {"id": "rem-go", "label": "could_not",
                                   "disposition": "remove"}},
              finish()])

    status, _ = run_routine(d, server_for(d), run_ts=TS)
    from rsched.hooks import reminderproj

    assert status == "ok"
    assert "rem-go" not in reminderproj.definitions(d.parent, routine="receiver2")
    assert [r.id for r in remstore.load_local(d)[0]] == []
