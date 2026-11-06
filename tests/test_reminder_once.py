"""A consequence reminder holds ONCE PER REMINDER per run, never once per action string.

After its first hold the caution is in the run's context, so a reworded action it also matches
learns nothing from a second hold — keyed on the string, it was held again (scheduler-builder
20261105 T242: "Changing the command changed its string, so both fired again. Re-emitting
verbatim"; ~39 hold re-emits over four self-audit runs). The resume rebuild of the same ledger
is pinned in tests/test_guard_scope.py.
"""

from __future__ import annotations

from conftest import finish, write_file
from helpers import reminder, server_for, set_capabilities
from rsched import reminders as store
from rsched.engine.runtime import run_routine
from rsched.engine.transcript import read_events

TS = "20260905-090000"


def _run(make_routine, scripted, replies, *, local=()):
    d = make_routine(slug="remonce")
    set_capabilities(d, reminders="local")
    store.save_local(d, list(local), {})
    ep = scripted(replies)
    status, run_dir = run_routine(d, server_for(d), run_ts=TS)
    return d, ep, status, read_events(run_dir / "transcript.jsonl")[0]


def test_a_reminder_holds_once_per_run_however_the_action_is_reworded(make_routine, scripted):
    """The caution is in the run's context after its first hold, so a REWORDED action it also
    matches runs at once — keyed on the string, changing a command re-held it (scheduler-builder
    20261105 T242: "Changing the command changed its string, so both fired again"). One hold,
    one fire, one label owed."""
    d, _ep, status, events = _run(
        make_routine, scripted,
        [write_file("state/probe.txt", content="one"),
         write_file("state/probe-2.txt", content="two"),
         finish()],
        local=[reminder(rid="rem-p", regex=r"^write_file path=state/probe", desc="clobbers")])
    holds = [e for e in events if e["type"] == "observation"
             and e["payload"].get("kind") == "reminder_hold"]
    assert len(holds) == 1 and status == "ok"
    assert (d / "state" / "probe-2.txt").read_text(encoding="utf-8") == "two"
    assert not (d / "state" / "probe.txt").exists()          # the held one never ran
    assert store.load_local(d)[0][0].stats["fires"] == 1


def test_a_second_reminder_still_holds_and_names_only_itself(make_routine, scripted):
    """Per REMINDER, not per run: a caution that has not held yet still holds an action an
    earlier hold's reminder also matches — and the hold names only the new caution, so the fire
    tally and the labels owed count only what the run was actually shown."""
    _d, _ep, _status, events = _run(
        make_routine, scripted,
        [write_file("state/a.txt", content="1"),
         write_file("state/b.txt", content="2"),
         write_file("state/b.txt", content="2"),
         finish()],
        local=[reminder(rid="rem-any", regex=r"^write_file path=state/", desc="any state"),
               reminder(rid="rem-b", regex=r"^write_file path=state/b", desc="b is shared")])
    holds = [e["payload"] for e in events if e["type"] == "observation"
             and e["payload"].get("kind") == "reminder_hold"]
    assert [[r["id"] for r in h["reminders"]] for h in holds] == [["rem-any"], ["rem-b"]]
