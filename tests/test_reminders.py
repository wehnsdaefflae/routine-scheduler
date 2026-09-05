"""The consequence-reminder layer: the store, the capability, and the pre-execution hold."""

import json

import pytest
import yaml

from conftest import finish, util, write_file
from rsched import reminders as store
from rsched.config import ServerConfig
from rsched.engine.actions import validate_action
from rsched.engine.runtime import run_routine
from rsched.engine.transcript import read_events
from rsched.grants import capabilities_for, floor_capabilities, read_library_requires
from rsched.policyload import load_policy
from rsched.reminders import Reminder

TS = "20260905-090000"


def _rem(rid="rem-1", regex="^util:danger", desc="it deletes the target", scope="local",
         **stats):
    return Reminder(id=rid, regex=regex, description=desc, scope=scope,
                    created_run="r:1", stats={**store.blank_stats(), **stats})


# --- the store --------------------------------------------------------------------------

@pytest.mark.parametrize(("pattern", "fragment"), [
    ("", "non-empty"),
    ("x" * (store.MAX_REGEX_CHARS + 1), "at most"),
    ("^util:(", "not a valid regular expression"),
    (".*", "matches the EMPTY string"),          # would hold every action ever taken
    ("(a?)*", "matches the EMPTY string"),
])
def test_regex_problems_are_caught_at_the_write_gate(pattern, fragment):
    problem = store.regex_problem(pattern)
    assert problem and fragment in problem


def test_a_usable_pattern_passes_and_matches_the_canonical_string():
    assert store.regex_problem("^util:fs-ops mv ") is None
    assert _rem(regex="^util:fs-ops mv ").matches("util:fs-ops mv a b")
    assert not _rem(regex="^util:fs-ops mv ").matches("util:fs-ops cp a b")


@pytest.mark.parametrize("rid", ["../../../etc/passwd", "rem-../x", "notrem-1", "",
                                 "rem-a/b", "rem-" + "x" * 200])
def test_an_id_that_would_escape_the_store_is_refused(tmp_path, rid):
    """An id is a path segment, and a global record carries its OWN id — a git sync or a
    hand-edit into the shared library is untrusted input."""
    assert store.is_reminder_id(rid) is False
    with pytest.raises(ValueError, match="not a reminder id"):
        store.global_path(tmp_path, rid)
    with pytest.raises(ValueError, match="not a reminder id"):
        store.global_rel(rid)
    assert store.delete_global(tmp_path, rid) is False       # never raises, never unlinks
    assert store.is_reminder_id(store.new_id("20260905-090000", set())) is True


def test_a_global_record_with_an_unusable_id_is_skipped(tmp_path):
    lib = tmp_path / "reminders"
    lib.mkdir()
    (lib / "evil.json").write_text(
        '{"id": "../../../../x", "regex": "^util:", "description": "d"}', encoding="utf-8")
    store.write_global(lib, _rem(rid="rem-ok", regex="^util:ok", scope="global"))
    assert [r.id for r in store.load_global(lib)] == ["rem-ok"]


def test_a_pattern_that_stopped_compiling_never_fires_rather_than_raising():
    """The store must not be able to break a run: a hand-edited file with a broken pattern
    is inert, not an exception on every action."""
    assert _rem(regex="^util:(").matches("util:anything") is False


def test_local_round_trip_and_the_global_tally_share_one_file(tmp_path):
    local = [_rem(rid="rem-a", regex="^util:a"), _rem(rid="rem-b", regex="^util:b", did=2)]
    store.save_local(tmp_path, local, {"rem-g": {**store.blank_stats(), "fires": 3}})
    back, gstats = store.load_local(tmp_path)
    assert [r.id for r in back] == ["rem-a", "rem-b"]
    assert back[1].stats["did"] == 2 and back[1].scope == "local"
    assert gstats["rem-g"]["fires"] == 3
    # a hand-broken file reads as an empty store instead of failing a run at boot
    store.local_path(tmp_path).write_text("{not json", encoding="utf-8")
    assert store.load_local(tmp_path) == ([], {})


def test_the_union_is_local_over_global_by_regex(tmp_path):
    lib = tmp_path / "library" / "reminders"
    store.write_global(lib, _rem(rid="rem-g1", regex="^util:a", desc="the library's take",
                                 scope="global"))
    store.write_global(lib, _rem(rid="rem-g2", regex="^util:z", desc="only global",
                                 scope="global"))
    store.save_local(tmp_path, [_rem(rid="rem-l", regex="^util:a", desc="mine wins")], {})

    assert store.active(tmp_path, lib, "none") == []
    assert [r.id for r in store.active(tmp_path, lib, "local")] == ["rem-l"]
    union = store.active(tmp_path, lib, "global")
    # same regex = the same match class, so the local one shadows the library's; a different
    # regex is a different class and both stay live
    assert [r.id for r in union] == ["rem-l", "rem-g2"]
    assert union[0].description == "mine wins"
    # the library copy carries the definition only — the evidence about it is per-routine
    rec = json.loads((lib / "rem-g1.json").read_text(encoding="utf-8"))
    assert set(rec) == {"id", "regex", "description", "created_run"}


def test_record_bumps_the_tally_for_both_scopes_in_the_local_file(tmp_path):
    local = _rem(rid="rem-l", regex="^util:a")
    store.save_local(tmp_path, [local], {})
    # the tally AFTER the increment comes back, so a frozen in-memory copy can be refreshed
    assert store.record(tmp_path, local, "fires")["fires"] == 1
    assert store.record(tmp_path, local, "would_have")["would_have"] == 1
    store.record(tmp_path, _rem(rid="rem-g", scope="global"), "could_not")
    back, gstats = store.load_local(tmp_path)
    assert back[0].stats["fires"] == 1 and back[0].stats["would_have"] == 1
    assert gstats["rem-g"]["could_not"] == 1
    store.record(tmp_path, local, "nonsense")          # not a stat field: ignored, never raises
    assert store.load_local(tmp_path)[0][0].stats["fires"] == 1


# --- the capability ---------------------------------------------------------------------

def _lib_doc(home, slug, requires):
    (home / "permissions").mkdir(parents=True, exist_ok=True)
    (home / "permissions" / f"{slug}.md").write_text(
        f"---\ntags: [a, b, c]\nrequires:\n{requires}\n---\n# permission: {slug} — doc\nbody\n",
        encoding="utf-8")


def test_a_doc_raises_the_dial_and_the_floor_drops_it_without_the_doc(tmp_path):
    _lib_doc(tmp_path, "reminders", "  reminders: local")
    lib = read_library_requires(tmp_path / "permissions")
    assert capabilities_for(["reminders"], lib)["reminders"] == "local"
    # the user's own deeper choice survives the raise (it only ever rises)
    deeper = capabilities_for(["reminders"], lib, {"reminders": "global"})
    assert deeper["reminders"] == "global"
    # …and is floored away entirely when the doc is not held
    assert floor_capabilities([], lib, {"reminders": "global"})["reminders"] == "none"
    assert floor_capabilities(["reminders"], lib,
                              {"reminders": "global"})["reminders"] == "global"


@pytest.mark.parametrize(("level", "scope", "denied"), [
    ("none", "local", True), ("none", "global", True),
    ("local", "local", False), ("local", "global", True),
    ("global", "local", False), ("global", "global", False),
])
def test_the_write_gate_follows_the_dial(tmp_path, level, scope, denied):
    policy = load_policy(tmp_path, [], {"reminders": level})
    problem = policy.reminder_denial(scope)
    assert (problem is not None) is denied
    if denied:
        # every denial names the way out — the four-state request, not a dead end
        assert "reminders:" in problem


def test_the_global_approval_ladder_is_its_own_dial(tmp_path):
    policy = load_policy(tmp_path, [], {"reminders": "global", "remind_confirm": "creations"})
    assert policy.needs_remind_confirm(creating=True) is True
    assert policy.needs_remind_confirm(creating=False) is False
    assert policy.reminders_on is True
    assert load_policy(tmp_path, [], {}).reminders_on is False


def test_the_fields_are_refused_outright_when_the_capability_is_off(tmp_path):
    off = load_policy(tmp_path, [], {})
    action = {"say": "s", "kind": "read_file", "path": "a.md",
              "remind": {"op": "add", "regex": "^util:x", "description": "d"}}
    problems = validate_action(action, grants=off)
    assert problems and "switched OFF" in problems[0]
    # the gate rides the FIELD, so an ALWAYS_KIND is not a way around it
    always = {"say": "s", "kind": "report", "title": "t",
              "remind": {"op": "add", "regex": "^util:x", "description": "d"}}
    assert validate_action(always, grants=off)


def test_malformed_ops_are_corrected_inside_the_schema_retry_cycle(tmp_path):
    on = load_policy(tmp_path, [], {"reminders": "local"})

    def problems(**fields):
        return validate_action({"say": "s", "kind": "read_file", "path": "a.md", **fields},
                               grants=on)

    assert "must be an object" in problems(remind="add one")[0]
    assert '"add", "revise" or "delete"' in problems(remind={"op": "nope"})[0]
    assert "non-empty pattern" in problems(remind={"op": "add", "description": "d"})[0]
    assert "consequence IS" in problems(remind={"op": "add", "regex": "^util:x"})[0]
    assert "needs the `id`" in problems(remind={"op": "delete"})[0]
    assert "needs a new `regex`" in problems(remind={"op": "revise", "id": "rem-1"})[0]
    assert "must be one of" in problems(remind_feedback={"id": "rem-1", "label": "nope"})[0]
    # …and a well-formed pair passes
    assert not problems(remind={"op": "add", "regex": "^util:x", "description": "d"},
                        remind_feedback={"id": "rem-1", "label": "would_have"})


# --- the pre-execution hold, end to end ---------------------------------------------------

def _server(routine_dir) -> ServerConfig:
    s = ServerConfig()
    s.routines_home = routine_dir.parent
    s.libraries_home = routine_dir.parent.parent / "test-library"
    return s


def _capabilities(routine_dir, **updates):
    path = routine_dir / "routine.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw["capabilities"] = {**(raw.get("capabilities") or {}), **updates}
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")


def _run(make_routine, scripted, replies, *, reminders="local", local=(), **caps):
    d = make_routine(slug="remr")
    _capabilities(d, reminders=reminders, **caps)
    if local:
        store.save_local(d, list(local), {})
    ep = scripted(replies)
    status, run_dir = run_routine(d, _server(d), run_ts=TS)
    events, _ = read_events(run_dir / "transcript.jsonl")
    return d, ep, status, events


def _prompt_text(ep) -> str:
    """Everything the model was shown — the observations live in the message list, not the
    transcript, so this is where a rendered hold or an engine note is asserted."""
    return json.dumps(ep.calls[-1]["messages"], ensure_ascii=False)


def test_a_matching_action_is_held_before_it_runs_and_re_emitting_it_proceeds(
        make_routine, scripted):
    """The whole point is PRE-execution: the first probe must leave no file behind, and the
    identical second one must go through — re-emitting the held action IS the confirmation."""
    caution = "state/probe.txt is the sibling routine's input — writing it clobbers their run"
    d, ep, status, events = _run(
        make_routine, scripted,
        [write_file("state/probe.txt", content="one"),
         write_file("state/probe.txt", content="one"),
         finish()],
        local=[_rem(rid="rem-p", regex=r"^write_file path=state/probe", desc=caution)])

    holds = [e for e in events if e["type"] == "observation"
             and e["payload"].get("kind") == "reminder_hold"]
    assert len(holds) == 1                                  # one hold per action string per run
    assert holds[0]["payload"]["action"] == "write_file path=state/probe.txt"
    assert holds[0]["payload"]["reminders"][0]["id"] == "rem-p"
    assert (d / "state" / "probe.txt").read_text(encoding="utf-8") == "one"   # the SECOND write
    assert status == "ok"
    shown = _prompt_text(ep)
    assert "ACTION HELD" in shown and caution in shown and "remind_feedback" in shown
    # the fire is counted — it is the denominator the tally is read against
    assert store.load_local(d)[0][0].stats["fires"] == 1


def test_a_hold_executes_nothing_and_the_feedback_label_lands_on_the_tally(
        make_routine, scripted):
    d, ep, status, events = _run(
        make_routine, scripted,
        [util("danger", args=["--wipe"]),
         {**write_file("state/other.txt", content="x"),
          "remind_feedback": {"id": "rem-d", "label": "would_have"}},
         finish()],
        local=[_rem(rid="rem-d", regex="^util:danger", desc="it wipes the workdir")])

    kinds = [e["payload"].get("kind") for e in events if e["type"] == "observation"]
    assert kinds == ["reminder_hold", "write_file"]      # the util never reached the executor
    tally = store.load_local(d)[0][0].stats
    assert tally["fires"] == 1 and tally["would_have"] == 1
    assert "rem-d labelled would_have" in _prompt_text(ep)
    assert status == "ok"


def test_an_unlabelled_fire_is_asked_about_once(make_routine, scripted):
    """Not a hard requirement — the field rides every kind, and rejecting an action for
    omitting bookkeeping would put the layer in the way of the work."""
    _d, ep, status, _events = _run(
        make_routine, scripted,
        [util("danger"), write_file("state/a.txt"), write_file("state/b.txt"),
         write_file("state/c.txt"), finish()],
        local=[_rem(rid="rem-d", regex="^util:danger", desc="it wipes the workdir")])
    shown = _prompt_text(ep)
    assert shown.count("STILL unlabelled") == 1
    assert status == "ok"


def test_an_op_rides_the_action_at_no_turn_cost_and_cannot_hold_that_action(
        make_routine, scripted):
    """Ops are applied AFTER the interception check, so a reminder authored this turn can
    never hold the very action it rode on."""
    d, ep, status, events = _run(
        make_routine, scripted,
        [{**write_file("state/probe.txt", content="x"),
          "remind": {"op": "add", "regex": r"^write_file path=state/probe",
                     "description": "the sibling reads that file"}},
         finish()])
    kinds = [e["payload"].get("kind") for e in events if e["type"] == "observation"]
    assert kinds == ["write_file"]                       # written, not held
    assert (d / "state" / "probe.txt").exists()
    saved, _ = store.load_local(d)
    assert len(saved) == 1 and saved[0].regex == r"^write_file path=state/probe"
    assert saved[0].created_run.endswith(TS)
    assert "[REMINDERS: added rem-" in _prompt_text(ep)
    assert status == "ok"


def test_revise_and_delete_are_reported_back_to_the_run(make_routine, scripted):
    d, ep, status, _events = _run(
        make_routine, scripted,
        [{**write_file("state/a.txt"),
          "remind": {"op": "revise", "id": "rem-p", "description": "sharper caution"}},
         {**write_file("state/b.txt"), "remind": {"op": "delete", "id": "rem-p"}},
         {**write_file("state/c.txt"), "remind": {"op": "delete", "id": "rem-gone"}},
         finish()],
        local=[_rem(rid="rem-p", regex="^util:danger", desc="vague")])
    assert store.load_local(d)[0] == []
    shown = _prompt_text(ep)
    assert "rem-p revised (local)" in shown and "rem-p deleted (local)" in shown
    assert "no reminder 'rem-gone' is live" in shown
    assert status == "ok"


def test_a_fire_survives_a_later_definition_write_in_the_same_run(make_routine, scripted):
    """The tally is disk-owned, the definitions are memory-owned. Taking both halves from the
    in-memory set rolled every fire recorded this run back to its boot-time value — the frozen
    Reminder objects never saw the increment."""
    d, _ep, status, _events = _run(
        make_routine, scripted,
        [util("danger"),                      # HELD → rem-d.fires = 1 on disk
         {**write_file("state/a.txt"),        # a local ADD rewrites the whole file
          "remind": {"op": "add", "regex": "^util:other", "description": "another caution"}},
         {**write_file("state/b.txt"),        # …and so does a DELETE of an unrelated one
          "remind": {"op": "delete", "id": "rem-gone-too"}},
         finish()],
        local=[_rem(rid="rem-d", regex="^util:danger", desc="it wipes the workdir"),
               _rem(rid="rem-gone-too", regex="^util:never", desc="deleted below")])
    saved = {r.id: r for r in store.load_local(d)[0]}
    assert saved["rem-d"].stats["fires"] == 1, "the hold's fire was rolled back"
    assert "rem-gone-too" not in saved
    assert [r.regex for r in saved.values() if r.id != "rem-d"] == ["^util:other"]
    assert status == "ok"


def test_a_hold_does_not_spend_an_allow_once_grant(make_routine, scripted, monkeypatch):
    """D65/D76: a once-grant is spent by USE, not by attempt — and a hold is the purest
    attempt-without-use there is. Spending it would deny the re-emitted action that the
    hold's own contract calls the confirmation to proceed."""
    from rsched.engine import requests as requests_mod

    seen: list[str] = []
    real = requests_mod.consume_once_grants

    def spy(loop, action, obs):
        seen.append(action["kind"])
        return real(loop, action, obs)

    monkeypatch.setattr(requests_mod, "consume_once_grants", spy)
    _d, _ep, status, events = _run(
        make_routine, scripted, [util("danger"), util("danger"), finish()],
        local=[_rem(rid="rem-h", regex="^util:danger", desc="it wipes things")])
    kinds = [e["payload"].get("kind") for e in events if e["type"] == "observation"]
    assert kinds == ["reminder_hold", "util"]
    assert seen == ["util"], "the once-grant boundary must not see the HELD action"
    assert status == "ok"


def test_the_side_fields_ride_a_finish(make_routine, scripted):
    """The last turn is where the engine asked for a did/didnt label — dropping it there
    throws away exactly the evidence the layer exists to collect. The write_file is the
    action the finish is grounded on: a HOLD grounds nothing (below)."""
    d, _ep, status, _events = _run(
        make_routine, scripted,
        [util("danger"),
         write_file("state/went-ahead.txt"),
         {**finish(), "remind_feedback": {"id": "rem-f", "label": "didnt"},
          "remind": {"op": "revise", "id": "rem-f", "description": "sharper"}}],
        local=[_rem(rid="rem-f", regex="^util:danger", desc="vague")])
    saved = store.load_local(d)[0]
    assert saved[0].description == "sharper"
    assert saved[0].stats == {"fires": 1, "could_not": 0, "would_have": 0, "did": 0,
                              "didnt": 1}
    assert status == "ok"


def test_a_hold_alone_does_not_ground_a_finish(make_routine, scripted):
    """A held action executed nothing, so the fabrication guard still has nothing to accept —
    the counter the guard reads must not be fed by an action the engine refused to run."""
    d, _ep, status, events = _run(
        make_routine, scripted,
        [util("danger"), finish(), write_file("state/real.txt"), finish()],
        local=[_rem(rid="rem-g", regex="^util:danger", desc="it wipes the workdir")])
    rejected = [e for e in events if e["type"] == "observation"
                and e["payload"].get("rejected")]
    assert rejected, "finish(ok) after nothing but a hold must be refused"
    assert (d / "state" / "real.txt").exists()      # the run went on and did real work
    assert status == "ok"


def test_a_local_reminder_may_be_promoted_to_the_shared_store(make_routine, scripted):
    """The engine's own promotion instruction — add at global scope, then delete the local
    one — has to be followable: the duplicate check is per STORE, because a local reminder
    shadowing a global one IS the union's designed precedence."""
    d, ep, status, _events = _run(
        make_routine, scripted,
        [{**write_file("state/a.txt"),
          "remind": {"op": "add", "scope": "global", "regex": "^util:fs-ops mv ",
                     "description": "mv over an existing destination overwrites it silently"}},
         {**write_file("state/b.txt"), "remind": {"op": "delete", "id": "rem-p"}},
         finish()],
        reminders="global", remind_confirm="never",
        local=[_rem(rid="rem-p", regex="^util:fs-ops mv ", desc="proven locally")])
    written = sorted(_server(d).reminders_home.glob("*.json"))
    assert len(written) == 1                       # the promotion landed
    assert store.load_local(d)[0] == []            # …and the local copy is gone
    assert "already live" not in _prompt_text(ep)
    assert status == "ok"


def test_the_denied_scope_routes_to_an_answerable_access_request(make_routine, scripted):
    """Every reminder denial ends in `ask_user with request: "reminders:…"`. That route has
    to be one the request validator ACCEPTS, or the run is sent down a dead end where it can
    only burn schema retries (and then trip the schema-storm guard)."""
    _d, _ep, status, events = _run(
        make_routine, scripted,
        [{"say": "The shared store would carry this.", "kind": "ask_user", "mode": "deferred",
          "question": "May I keep this caution in the shared library store?",
          "request": "reminders:global"},
         finish()],
        reminders="local")
    asked = [e for e in events if e["type"] == "question"]
    assert asked, "the request was refused instead of reaching the user"
    assert asked[0]["payload"]["request"] == ["reminders:global"]
    assert status == "ok"


def test_two_asks_in_one_turn_get_two_decision_records(tmp_path):
    """A global reminder op riding an `ask_user` files a SECOND question on the same turn —
    the first time one turn could file two. The id was `q-<run-ts>-<turn>`, so the approval
    overwrote the user's own question and the answer settled the wrong one.

    Unit-level on purpose: the end-to-end path needs a BLOCKING approval, which would park
    the test on the ask timeout, and the collision is a property of id allocation alone.
    """
    from types import SimpleNamespace

    from rsched.engine.inbox import file_question, resolve_question
    from rsched.engine.interact import _free_qid

    ctx = SimpleNamespace(routine=SimpleNamespace(dir=tmp_path), run_ts=TS, turn=5)
    first = _free_qid(ctx)
    assert first == f"q-{TS}-5"
    file_question(tmp_path, first, "Which venue?", [], TS)
    second = _free_qid(ctx)
    assert second == f"q-{TS}-5-2" and second != first
    file_question(tmp_path, second, "Approve the global reminder?", [], TS)
    assert _free_qid(ctx) == f"q-{TS}-5-3"
    # both records survive, so the user answers the question they were actually asked
    assert sorted(p.name for p in (tmp_path / "questions" / "pending").glob("*.json")) == [
        f"q-{TS}-5-2.json", f"q-{TS}-5.json"]
    # a SETTLED record frees its id again — the check is against what is still open
    resolve_question(tmp_path, first)
    assert _free_qid(ctx) == first


def test_a_grant_that_lands_mid_run_does_not_truncate_the_store(make_routine, scripted,
                                                                monkeypatch):
    """The set is read once at boot — and at level `none` it is not read at all. A
    `reminders:*` grant arriving mid-run must re-read it, or the run's first write rebuilds
    state/reminders.json from an empty in-memory set and drops everything already there."""
    from rsched.engine import requests as requests_mod

    d = make_routine(slug="remr")
    _capabilities(d, reminders="none")           # the config level: the layer is OFF at boot
    store.save_local(d, [_rem(rid="rem-old-1", regex="^util:a", desc="from an earlier run",
                              fires=3, would_have=2),
                         _rem(rid="rem-old-2", regex="^util:b", desc="also earlier")], {})

    def grant_at_boot(loop, pairs):              # stand in for the Decisions-page answer
        loop.ctx.granted_now = frozenset({"reminders:local"})
        requests_mod.rebuild_policy(loop)

    monkeypatch.setattr(requests_mod, "apply_deferred_decisions", grant_at_boot)
    monkeypatch.setattr("rsched.engine.control.inbox.collect_deferred_answers",
                        lambda *a, **k: [{"question": "may I?", "answer": "allow"}])
    ep = scripted([{**write_file("state/a.txt"),
                    "remind": {"op": "add", "regex": "^util:c", "description": "brand new"}},
                   finish()])
    status, _run_dir = run_routine(d, _server(d), run_ts=TS)
    kept = {r.id: r for r in store.load_local(d)[0]}
    assert set(kept) >= {"rem-old-1", "rem-old-2"}, "the earlier run's reminders were dropped"
    assert kept["rem-old-1"].stats["fires"] == 3 and kept["rem-old-1"].stats["would_have"] == 2
    assert any(r.regex == "^util:c" for r in kept.values())      # …and the new one landed
    assert status == "ok" and ep


def test_a_deferred_finish_does_not_re_record_its_label(make_routine, scripted):
    """Every rung of the finish gate hands the SAME finish back for revision, and the model
    re-emits it with its side fields intact. Charging the tally once per attempt would make
    `fires` minus the labels negative — the quantity the layer is read by."""
    d, _ep, status, events = _run(
        make_routine, scripted,
        # The hold executes nothing, so the FABRICATION guard sets the first finish aside —
        # a real gate rung, reached with the side fields already applied.
        [util("danger"),                                   # HELD → rem-r.fires = 1
         {**finish(), "remind_feedback": {"id": "rem-r", "label": "didnt"}},
         write_file("state/went-ahead.txt"),               # now something has really run
         {**finish(), "remind_feedback": {"id": "rem-r", "label": "didnt"}}],
        local=[_rem(rid="rem-r", regex="^util:danger", desc="it wipes the workdir")])
    rejected = [e for e in events if e["type"] == "observation"
                and e["payload"].get("rejected")]
    assert rejected, "the first finish must have been set aside for this to test anything"
    tally = store.load_local(d)[0][0].stats
    assert tally["fires"] == 1
    assert tally["didnt"] == 1, "the replayed finish charged the label twice"
    assert status == "ok"


def test_a_feedback_label_is_refused_when_the_layer_is_off(tmp_path):
    """The gate rides the FIELD, not the kind — and it gates the LAYER, not just the write:
    a run with no reminders has no fire to label."""
    off = load_policy(tmp_path, [], {})
    problems = validate_action({"say": "s", "kind": "report", "title": "t",
                                "remind_feedback": {"id": "rem-1", "label": "would_have"}},
                               grants=off)
    assert problems and "switched OFF" in problems[0]


def test_a_global_write_lands_in_the_library_when_the_dial_is_autonomous(
        make_routine, scripted):
    d, _ep, status, _events = _run(
        make_routine, scripted,
        [{**write_file("state/a.txt"),
          "remind": {"op": "add", "scope": "global", "regex": "^util:fs-ops mv ",
                     "description": "mv over an existing destination overwrites it silently"}},
         finish()],
        reminders="global", remind_confirm="never")
    home = _server(d).reminders_home
    written = sorted(home.glob("*.json"))
    assert len(written) == 1
    rec = json.loads(written[0].read_text(encoding="utf-8"))
    assert rec["regex"] == "^util:fs-ops mv " and rec["id"].startswith("rem-")
    assert store.load_local(d)[0] == []            # a global reminder is NOT in the local store
    assert status == "ok"
