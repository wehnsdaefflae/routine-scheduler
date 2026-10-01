"""Rule assists — the curated half of the relevance-trigger layer: the declaration, its
validation, the three moments, and the guards that keep a layer live in every routine from
becoming rent on every turn."""

import json
from pathlib import Path

import pytest
import yaml

from conftest import finish, util, write_file
from rsched import assists as lib
from rsched.assists import normalize_assists
from rsched.config import ServerConfig
from rsched.engine.assist_predicates import PREDICATES
from rsched.engine.observations import is_failure
from rsched.engine.runtime import run_routine
from rsched.engine.transcript import read_events
from rsched.reminders import Reminder
from rsched.workflows.lint import lint_rule_text

TS = "20260905-190000"
SEED = Path(__file__).resolve().parents[1] / "library-seed" / "rules"


def _rem(rid="rem-1", regex="^util:danger", desc="it deletes the target"):
    from rsched import reminders as rem_store
    return Reminder(id=rid, regex=regex, description=desc, scope="local",
                    created_run="r:1", stats=rem_store.blank_stats())


def _capabilities(routine_dir, **updates):
    path = routine_dir / "routine.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw["capabilities"] = {**(raw.get("capabilities") or {}), **updates}
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")


def _assist(**over):
    base = {"id": "a1", "moment": "observation", "predicate": "repeated-failure",
            "payload": "remind", "line": "change route after the second failure"}
    return [{**base, **over}]


# --- the declaration ----------------------------------------------------------------------

def test_a_well_formed_block_normalizes():
    got, problems = normalize_assists(_assist(), rule="fix-the-cause")
    assert problems == []
    assert len(got) == 1
    a = got[0]
    assert a.rule == "fix-the-cause" and a.id == "a1" and a.key == "fix-the-cause/a1"
    assert a.moment == "observation" and a.payload == "remind"


def test_no_block_is_not_a_problem():
    assert normalize_assists(None) == ([], [])


@pytest.mark.parametrize(("over", "fragment"), [
    ({"id": "Not A Slug"}, "kebab-case"),
    ({"moment": "whenever"}, "'moment' must be one of"),
    ({"predicate": "reads-the-models-mind"}, "unknown predicate"),
    ({"payload": "scaffold"}, "not built yet"),
    ({"payload": "hold"}, "carries ['remind'], not 'hold'"),
    ({"moment": "pre-action", "predicate": "uncheckpointed-repo-write"},
     "carries ['hold'], not 'remind'"),
    ({"line": ""}, "operative instruction"),
    ({"line": "x" * (lib.MAX_LINE_CHARS + 1)}, "at most"),
    ({"extra": "key"}, "unknown key"),
])
def test_a_malformed_entry_is_dropped_and_reported(over, fragment):
    got, problems = normalize_assists(_assist(**over))
    assert got == []
    assert problems and fragment in problems[0]


def test_a_predicate_answering_a_different_moment_is_refused():
    """A rule may not ask a pre-finish question at an observation — the situation the
    predicate reads simply is not there."""
    got, problems = normalize_assists(_assist(predicate="ledger-untouched"))
    assert got == []
    assert problems and "answers at the 'pre-finish' moment" in problems[0]


def test_the_block_must_be_a_list_and_ids_unique():
    assert "must be a LIST" in normalize_assists({"id": "a"})[1][0]
    dupe = _assist() + _assist(moment="boundary", predicate="user-corrected")
    got, problems = normalize_assists(dupe)
    assert len(got) == 1 and "duplicate id" in problems[0]


def test_the_rule_linter_rejects_a_bad_block(tmp_path):
    """One call in lint_rule_text covers all four authoring surfaces — write_rule, the
    Library PUT, `rsched lint`, and the Library GET's per-rule problems."""
    head = ("---\ntags: [a, b, c]\neffect:\n  with: does the thing it is asked to do here\n"
            "  without: does not do the thing it is asked to do\n"
            "  when: the situation the rule governs comes up\n")
    body = "---\n# rule: x — y\n\nbody line one\nbody line two\n"
    bad = head + "assists:\n  - id: a1\n    moment: nowhere\n    predicate: repeated-failure\n"
    problems = lint_rule_text(bad + body, filename="x.md")
    assert any("'moment' must be one of" in p for p in problems)
    good = head + ("assists:\n  - id: a1\n    moment: observation\n"
                   "    predicate: repeated-failure\n    payload: remind\n"
                   "    line: change route after the second failure\n")
    assert lint_rule_text(good + body, filename="x.md") == []


def test_the_seed_rules_that_declare_assists_are_valid():
    """Every shipped declaration, checked against the live predicate registry."""
    declared = {}
    for path in sorted(SEED.glob("*.md")):
        meta = yaml.safe_load(path.read_text(encoding="utf-8").split("---")[1])
        got, problems = normalize_assists(meta.get("assists"), rule=path.stem)
        assert problems == [], (path.stem, problems)
        if got:
            declared[path.stem] = got
    assert set(declared) == {"ask-policy", "fix-the-cause", "problem-routing",
                             "decision-record", "interface-craft", "git-checkpoint"}
    # every registered predicate is used by a shipped rule — a predicate nothing declares is
    # engine code with no reader
    used = {a.predicate for rule in declared.values() for a in rule}
    assert used == set(PREDICATES)
    # every moment is exercised by a real rule, and both built payloads with it
    assert {a.moment for rule in declared.values() for a in rule} == set(lib.MOMENTS)
    assert {a.payload for rule in declared.values() for a in rule} == set(lib.PAYLOADS)



def test_a_hand_broken_fire_count_never_fails_the_turn(tmp_path):
    """The tally is best-effort — a failed count must never fail the turn that fired. Only the
    write was guarded: a count that does not parse raised from the read, mid-observation."""
    assist = normalize_assists(_assist(), rule="fix-the-cause")[0][0]
    lib.state_path(tmp_path).parent.mkdir(parents=True)
    lib.state_path(tmp_path).write_text(json.dumps({assist.key: "many"}), encoding="utf-8")
    assert lib.record_fire(tmp_path, assist) == 1
    assert lib.record_fire(tmp_path, assist) == 2


def test_only_the_rules_a_routine_holds_contribute(tmp_path):
    rules = tmp_path / "rules"
    rules.mkdir()
    for slug, moment, pred in (("held", "observation", "repeated-failure"),
                               ("unheld", "boundary", "user-corrected")):
        (rules / f"{slug}.md").write_text(
            f"---\ntags: [a, b, c]\nassists:\n  - id: x\n    moment: {moment}\n"
            f"    predicate: {pred}\n    payload: remind\n    line: a line\n---\n"
            f"# rule: {slug} — s\nbody\n", encoding="utf-8")
    assert [a.rule for a in lib.for_rules(rules, ["held"])] == ["held"]
    assert lib.for_rules(rules, []) == []
    assert {a.rule for a in lib.read_library_assists(rules)} == {"held", "unheld"}


# --- the failure classifier ----------------------------------------------------------------

@pytest.mark.parametrize("obs", [
    {"kind": "util", "exit": 2}, {"kind": "read_file", "error": "no such file"},
    {"kind": "util", "missing": True}, {"kind": "finish", "rejected": True},
    {"kind": "util", "declined_secrets": ["X"]}, {"kind": "write_rule", "lint_ok": False},
    {"kind": "read_file", "files": [{"path": "a"}, {"path": "b", "error": "gone"}]},
])
def test_is_failure_recognises_every_spelling(obs):
    assert is_failure(obs) is True


@pytest.mark.parametrize("obs", [
    {"kind": "util", "exit": 0}, {"kind": "write_file", "bytes": 10},
    {"kind": "read_file", "files": [{"path": "a"}]}, {"kind": "write_rule", "problems": []},
])
def test_is_failure_does_not_cry_wolf(obs):
    assert is_failure(obs) is False


# --- the three moments, end to end ---------------------------------------------------------

def _server(routine_dir) -> ServerConfig:
    s = ServerConfig()
    s.routines_home = routine_dir.parent
    s.libraries_home = routine_dir.parent.parent / "test-library"
    return s


def _rule(server, slug: str, moment: str, predicate: str, line: str) -> None:
    """A library rule declaring one assist. The payload follows the MOMENT, because the two
    are coupled: a chosen action can only be reached by stopping it, and a moment with no
    action in hand has nothing to stop."""
    home = server.rules_home
    home.mkdir(parents=True, exist_ok=True)
    payload = lib.MOMENT_PAYLOADS[moment][0]
    (home / f"{slug}.md").write_text(
        f"---\ntags: [a, b, c]\nassists:\n  - id: m\n    moment: {moment}\n"
        f"    predicate: {predicate}\n    payload: {payload}\n    line: {line}\n---\n"
        f"# rule: {slug} — s\nbody\n", encoding="utf-8")


def _write_root(routine_dir, path) -> None:
    """Grant the routine a write root. Without one the engine refuses the write on its own
    terms, and a test asserting the proceed path would prove nothing about the hold."""
    cfg = routine_dir / "routine.yaml"
    raw = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
    raw["fs_write_roots"] = [str(path)]
    cfg.write_text(yaml.safe_dump(raw), encoding="utf-8")


def _hold_rule(routine_dir, slugs: list[str]) -> None:
    path = routine_dir / "routine.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw["rules"] = slugs
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")


def _age_ledger(routine_dir) -> None:
    """Date the fixture's LEDGER.md before the run's start. The ledger predicate compares the
    file's mtime with the run's stamp; the fixture writes it at test time — after TS."""
    import datetime as dt
    import os

    before = dt.datetime.strptime(TS, "%Y%m%d-%H%M%S").astimezone().timestamp() - 3600
    os.utime(routine_dir / "LEDGER.md", (before, before))


def _run(make_routine, scripted, replies, *, slug, moment, predicate,
         line="the operative line"):
    d = make_routine(slug="assistr")
    server = _server(d)
    _rule(server, slug, moment, predicate, line)
    _hold_rule(d, [slug])
    _age_ledger(d)
    ep = scripted(replies)
    status, run_dir = run_routine(d, server, run_ts=TS)
    events, _ = read_events(run_dir / "transcript.jsonl")
    return d, ep, status, events


def _shown(ep) -> str:
    return json.dumps(ep.calls[-1]["messages"], ensure_ascii=False)


def test_an_observation_assist_rides_the_tail_of_the_second_failure(make_routine, scripted):
    """fix-the-cause's moment. Costs no turn: it appends to the observation the run was
    getting anyway — and only on the SECOND failure of the same call, where a run either
    changes route or starts engineering around a wall."""
    d, ep, status, events = _run(
        make_routine, scripted,
        [util("nonexistent-util"), util("nonexistent-util"), util("nonexistent-util"),
         write_file("state/a.txt"), finish()],
        slug="fix-the-cause", moment="observation", predicate="repeated-failure",
        line="change route after the second failure")
    shown = _shown(ep)
    assert "[RULE fix-the-cause — the same call has now failed twice this run]" in shown
    assert "change route after the second failure" in shown
    assert "read_rule name=fix-the-cause" in shown           # the rest of the rule stays put
    assert shown.count("[RULE fix-the-cause") == 1           # once per run, not per failure
    observations = [e for e in events if e["type"] == "observation"]
    assert "[RULE" not in json.dumps(observations[0])        # the FIRST failure is information
    assert json.loads(lib.state_path(d).read_text())["fix-the-cause/m"] == 1
    assert status == "ok"


def test_a_boundary_assist_arrives_as_an_engine_note(make_routine, scripted):
    """fix-the-cause's correction moment. The user speaking to a run IN FLIGHT is the edge — a
    message waiting before the run is its task, not an intervention in it — and the note is
    appended at the turn boundary, the same carrier a mid-run rule binding uses."""
    from rsched.engine.inbox import file_message

    d = make_routine(slug="assistr")
    server = _server(d)
    _rule(server, "fix-the-cause", "boundary", "user-corrected", "name the intention")
    _hold_rule(d, ["fix-the-cause"])

    def correcting():
        # lands AFTER this turn's drain, so the NEXT boundary is where it is delivered
        file_message(d, "actually, always use the short form", via="web")
        return write_file("state/a.txt")

    ep = scripted([correcting, write_file("state/b.txt"), finish()])
    status, run_dir = run_routine(d, server, run_ts=TS)
    events, _ = read_events(run_dir / "transcript.jsonl")
    notes = [e["payload"]["text"] for e in events if e["type"] == "user_injection"
             and e["payload"].get("source") == "engine"]
    assert notes, "the boundary assist never fired"
    assert "[RULE fix-the-cause — the user just said something to this run]" in notes[0]
    assert "name the intention" in _shown(ep)
    assert len(notes) == 1, "one fire per run, however often the user speaks"
    assert status == "ok"


def test_the_message_that_opens_a_resumed_leg_is_its_task_not_a_correction(
        make_routine, scripted):
    """A conversation's every later reply is a RESUMED leg, opened by the user's message —
    injected at boot, so it counts as the user speaking. Counted against a watermark of zero,
    the leg's first boundary read it as an intervention and the correction line fired on every
    reply. A fresh run's opening prose is never counted; a resumed leg's is now not either."""
    from rsched.engine.inbox import file_message
    from rsched.paths import atomic_write_json

    d = make_routine(slug="assistr")
    server = _server(d)
    _rule(server, "fix-the-cause", "boundary", "user-corrected", "name the intention")
    _hold_rule(d, ["fix-the-cause"])
    scripted([write_file("state/a.txt"), finish(summary="first reply")])
    assert run_routine(d, server, run_ts=TS)[0] == "ok"

    atomic_write_json(d / "inbox" / "msg-1.json", {"text": "now do b", "via": "web-converse"})

    def corrected_in_flight():
        # lands AFTER this turn's drain: a correction to the leg in progress, which IS the edge
        file_message(d, "no — use the short form", via="web")
        return write_file("state/b.txt")

    ep = scripted([corrected_in_flight, write_file("state/c.txt"),
                   finish(summary="second reply")])
    status, run_dir = run_routine(d, server, run_ts=TS, resume_from=TS)
    assert status == "ok"
    first_prompt = json.dumps(ep.calls[0]["messages"], ensure_ascii=False)
    assert "now do b" in first_prompt and "[RULE fix-the-cause" not in first_prompt
    events, _ = read_events(run_dir / "transcript.jsonl")
    notes = [e["payload"]["text"] for e in events if e["type"] == "user_injection"
             and e["payload"].get("source") == "engine" and "[RULE" in e["payload"]["text"]]
    assert len(notes) == 1, notes          # the in-flight correction still fires, once


def _signal_at_next_boundary(routine_dir, signal: str, slug: str) -> None:
    """What the web layer writes when the user binds or unbinds a rule on a LIVE run."""
    from rsched.paths import atomic_write_json

    atomic_write_json(routine_dir / "runs" / TS / "control.json",
                      {signal: {"slugs": [slug], "ts": f"{signal}-1"}})


def test_an_unbound_rule_stops_assisting_the_live_run(make_routine, scripted):
    """An unbind reaches a live run at once — "they no longer bind this routine. Stop applying
    them" — so the rule's assists must stop too, or it goes on holding actions and deferring
    the finish of a run it no longer binds."""
    d = make_routine(slug="assistr")
    server = _server(d)
    _rule(server, "fix-the-cause", "observation", "repeated-failure", "change route now")
    _hold_rule(d, ["fix-the-cause"])

    def fail_then_unbind():
        _signal_at_next_boundary(d, "drop_rules", "fix-the-cause")
        return util("nonexistent-util")

    ep = scripted([fail_then_unbind, util("nonexistent-util"), write_file("state/a.txt"),
                   finish()])
    status, _run_dir = run_routine(d, server, run_ts=TS)
    shown = _shown(ep)
    assert "UNBOUND the general rule(s) 'fix-the-cause'" in shown
    assert "change route now" not in shown          # the second failure found no assist
    assert status == "ok"


def test_a_rule_bound_mid_run_brings_its_assists(make_routine, scripted):
    """Binding is the same act in the other direction: the rule's prose reaches the live run as
    a note, and the rule's assists — part of the rule — reach it with the prose."""
    d = make_routine(slug="assistr")
    server = _server(d)
    _rule(server, "fix-the-cause", "observation", "repeated-failure", "change route now")
    _hold_rule(d, [])

    def fail_then_bind():
        _signal_at_next_boundary(d, "add_rules", "fix-the-cause")
        return util("nonexistent-util")

    ep = scripted([fail_then_bind, util("nonexistent-util"), write_file("state/a.txt"),
                   finish()])
    status, _run_dir = run_routine(d, server, run_ts=TS)
    shown = _shown(ep)
    assert "the user bound the general rule 'fix-the-cause'" in shown
    assert "[RULE fix-the-cause — the same call has now failed twice this run]" in shown
    assert status == "ok"


def test_a_pre_finish_assist_defers_the_finish_exactly_once(make_routine, scripted):
    """decision-record's moment — the one that costs a turn, because a line surfaced as the
    run ends is a line nobody can act on."""
    _d, ep, status, events = _run(
        make_routine, scripted,
        [write_file("artifacts/report.md"), finish(), finish()],
        slug="decision-record", moment="pre-finish", predicate="ledger-untouched",
        line="append one LEDGER entry before you finish")
    deferred = [e for e in events if e["type"] == "observation"
                and e["payload"].get("assist")]
    assert len(deferred) == 1, "the finish should be set aside once, and only once"
    assert "append one LEDGER entry" in _shown(ep)
    assert status == "ok"


def test_a_satisfied_pre_finish_assist_never_fires(make_routine, scripted):
    """A run that DID write its ledger is not asked to."""
    _d, _ep, status, events = _run(
        make_routine, scripted,
        [write_file("artifacts/report.md"), write_file("LEDGER.md", content="### run — x"),
         finish()],
        slug="decision-record", moment="pre-finish", predicate="ledger-untouched")
    assert not [e for e in events if e["type"] == "observation"
                and e["payload"].get("assist")]
    assert status == "ok"


def test_a_run_that_changed_nothing_lasting_is_not_asked_for_a_ledger_entry(
        make_routine, scripted):
    """decision-record is a DEFAULT rule. A predicate that fired on every ledger-less run
    would take a turn from every routine in the instance, every run — and `state/` is the
    run's own working scratch, not something a later reader interprets."""
    _d, _ep, status, events = _run(
        make_routine, scripted,
        [write_file("state/probe.txt"), finish()],
        slug="decision-record", moment="pre-finish", predicate="ledger-untouched")
    assert not [e for e in events if e["type"] == "observation"
                and e["payload"].get("assist")]
    assert status == "ok"


def test_a_conversation_reply_is_never_held_for_a_ledger_entry(make_routine, scripted,
                                                               tmp_path):
    """A conversation's product is the reply and its reasoning is in the thread the user can
    already see; its spine is state/plan.md, not a ledger. Holding a reply for one costs the
    user a turn for nothing."""
    convs = tmp_path / "conversations"
    convs.mkdir(parents=True, exist_ok=True)
    d = make_routine(slug="c-assist")
    server = _server(d)
    server.conversations_home = convs
    # the run dir must sit directly under conversations_home for runkind.is_conversation to see it
    moved = convs / d.name
    d.rename(moved)
    server.routines_home = convs
    _rule(server, "decision-record", "pre-finish", "ledger-untouched", "append one entry")
    _hold_rule(moved, ["decision-record"])
    _age_ledger(moved)
    ep = scripted([write_file("artifacts/reply.md"), finish()])
    status, run_dir = run_routine(moved, server, run_ts=TS)
    events, _ = read_events(run_dir / "transcript.jsonl")
    assert not [e for e in events if e["type"] == "observation"
                and e["payload"].get("assist")]
    assert "[RULE" not in _shown(ep)
    assert status == "ok"


def test_a_routine_that_does_not_hold_the_rule_is_untouched(make_routine, scripted):
    d = make_routine(slug="assistr")
    server = _server(d)
    _rule(server, "fix-the-cause", "observation", "repeated-failure", "a line")
    _hold_rule(d, [])                       # the rule exists in the library, unheld here
    ep = scripted([util("nonexistent-util"), util("nonexistent-util"),
                   write_file("state/a.txt"), finish()])
    status, _run_dir = run_routine(d, server, run_ts=TS)
    assert "[RULE" not in _shown(ep)
    assert not lib.state_path(d).exists()
    assert status == "ok"


def test_a_predicate_that_raises_can_never_fail_a_turn(make_routine, scripted, monkeypatch):
    """A library document declares a check by name; a check that blows up is inert, never
    fatal — the run's work is not the layer's to lose."""
    from rsched.engine import assist_predicates

    def boom(_situation):
        raise RuntimeError("predicate exploded")

    monkeypatch.setitem(assist_predicates.PREDICATES, "repeated-failure",
                        assist_predicates.Predicate(moment="observation", check=boom,
                                                    describes="d"))
    _d, ep, status, _events = _run(
        make_routine, scripted,
        [util("nonexistent-util"), util("nonexistent-util"), write_file("state/a.txt"),
         finish()],
        slug="fix-the-cause", moment="observation", predicate="repeated-failure")
    assert "[RULE" not in _shown(ep)
    assert status == "ok"


def test_every_registered_predicate_declares_a_reachable_moment():
    assert set(PREDICATES), "the registry must not be empty"
    for name, predicate in PREDICATES.items():
        assert predicate.moment in lib.MOMENTS, (name, predicate.moment)
        assert predicate.describes.strip(), name

# --- the predicates that read the world rather than the actions ------------------------------

def test_a_ledger_appended_outside_the_actions_still_counts(make_routine, scripted):
    """An append through a shell heredoc, a script or a util is invisible to the actions; the
    file's own mtime is not. Reading the actions made 48 of 75 of this assist's deferrals
    false."""
    d = make_routine(slug="assistr")
    server = _server(d)
    _rule(server, "decision-record", "pre-finish", "ledger-untouched", "append one entry")
    _hold_rule(d, ["decision-record"])
    ledger = d / "LEDGER.md"
    _age_ledger(d)

    def append_by_other_means():
        with ledger.open("a", encoding="utf-8") as fh:     # what a shell append looks like
            fh.write("### run — kept X because Y\n")
        return finish()

    scripted([write_file("artifacts/report.md"), append_by_other_means])
    status, run_dir = run_routine(d, server, run_ts=TS)
    events, _ = read_events(run_dir / "transcript.jsonl")
    assert not [e for e in events if e["type"] == "observation"
                and e["payload"].get("assist")]
    assert status == "ok"


def test_a_denied_call_the_run_routed_around_is_named(make_routine, scripted):
    """ask-policy's moment: a refusal costs a turn at the validation seam; the action the
    run chose INSTEAD is where the line belongs — a request to make, not a wall to engineer
    around."""
    from types import SimpleNamespace

    from rsched.engine.assist_predicates import Situation, _capability_denied

    ctx = SimpleNamespace(turn=4, last_denial_turn=4)
    loop = SimpleNamespace(ctx=ctx)
    routed = Situation(loop=loop, action={"kind": "shell", "command": "curl x"}, obs={})
    assert _capability_denied(routed)
    asked = Situation(loop=loop, action={"kind": "ask_user", "request": "util:x",
                                         "question": "q"}, obs={})
    assert not _capability_denied(asked)                   # the line was already followed
    later = Situation(loop=SimpleNamespace(ctx=SimpleNamespace(turn=5, last_denial_turn=4)),
                      action={"kind": "shell", "command": "ls"}, obs={})
    assert not _capability_denied(later)
    from rsched.grantpolicy import REQUEST_ROUTE_MARK
    fs = Situation(loop=later.loop, action={"kind": "write_file", "path": "/x"},
                   obs={"error": f"outside the roots. If it is essential, {REQUEST_ROUTE_MARK} "
                                 '"fs-write:/x"'})
    assert _capability_denied(fs)                          # a gate that refuses AT execution


def test_the_validation_seam_marks_the_turn_a_denial_cost(make_routine, scripted):
    d = make_routine(slug="assistr")
    server = _server(d)
    perms = server.libraries_home / "permissions"
    perms.mkdir(parents=True, exist_ok=True)
    (perms / "shell.md").write_text("---\ntags: [a, b, c]\nrequires:\n  actions: [shell]\n"
                                    "---\n# permission: shell — x\nbody\n", encoding="utf-8")
    _rule(server, "ask-policy", "observation", "capability-denied", "file the request now")
    _hold_rule(d, ["ask-policy"])
    ep = scripted([{"say": "s", "kind": "shell", "command": "ls"},
                   write_file("state/a.txt"), finish()])
    status, _run_dir = run_routine(d, server, run_ts=TS)
    assert "file the request now" in _shown(ep)
    assert status == "ok"


def test_rendered_output_counts_as_seen_only_when_looked_at():
    from types import SimpleNamespace

    from rsched.engine.assist_predicates import Situation, _rendered_output_unseen

    def situation(*records):
        return Situation(loop=SimpleNamespace(ctx=None, turn_records=list(records)))

    page = {"kind": "write_file", "brief": json.dumps("site/index.html")}
    assert _rendered_output_unseen(situation(page))
    assert not _rendered_output_unseen(situation(page, {"kind": "view_image",
                                                        "brief": '"shot.png"'}))
    assert not _rendered_output_unseen(situation(page, {"kind": "util",
                                                        "brief": '"browser-session"'}))
    assert not _rendered_output_unseen(situation({"kind": "write_file",
                                                  "brief": '"notes.md"'}))


def test_a_report_answered_elsewhere_is_no_longer_owed(tmp_path):
    """R2086: the run's own list only learns of the replies it files through `report`; the
    ledger knows about every other way a thread closes."""
    from rsched.report_threads import still_owed

    rows = [{"id": "R1"}, {"id": "R2"}, {"id": "R3"}, {"id": "R4", "closes": True},
            {"id": "R5", "superseded": {"by": "R9"}},
            {"id": "R6", "answers": "R1", "closes": True},
            {"id": "R7", "settles": ["r2"]}]
    assert still_owed(rows, ["R1", "R2", "R3", "R4", "R5", "R404"]) == ["R3"]


def test_a_clean_repo_is_its_own_undo_point(tmp_path):
    """54 of the old hold's 102 fires were repos clean at HEAD, each clicked past — which
    trains a run to override the one hold guarding an irreversible write."""
    import subprocess

    from rsched.engine.assist_predicates import _dirty

    repo = tmp_path / "repo"
    repo.mkdir()
    git = ["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t"]
    subprocess.run([*git, "init", "-q"], check=True)
    (repo / "a.txt").write_text("a", encoding="utf-8")
    subprocess.run([*git, "add", "a.txt"], check=True)
    subprocess.run([*git, "commit", "-qm", "a"], check=True)
    assert not _dirty(repo)
    (repo / "a.txt").write_text("b", encoding="utf-8")
    assert _dirty(repo)
    assert _dirty(tmp_path / "not-a-repo")                  # unreadable reads as dirty


# --- the shared hold seam ------------------------------------------------------------------

def test_is_hold_covers_every_hold_kind():
    """The predicate two counters depend on — a held action grounds no finish and spends no
    allow-once grant — must know about EVERY source, including a future third."""
    from rsched.engine.hold import HOLD_KINDS, is_hold

    assert {"reminder_hold", "assist_hold"} == HOLD_KINDS
    for kind in HOLD_KINDS:
        assert is_hold({"kind": kind}) is True
    assert is_hold({"kind": "util", "exit": 0}) is False
    assert is_hold({}) is False


def test_the_two_sources_do_not_cannibalise_each_others_hold(make_routine, scripted):
    """Keyed on the bare action string, a reminder hold would silently spend the rule layer's
    only hold on the same action and the rule's caution would never be seen. The ledger
    carries the SOURCE, so each layer gets its own budget — but the model is still stopped
    ONCE per action, with precedence deciding which caution it hears first."""
    from rsched import reminders as rem_store

    d = make_routine(slug="assistr")
    server = _server(d)
    _rule(server, "git-checkpoint", "pre-action", "uncheckpointed-repo-write", "commit first")
    _hold_rule(d, ["git-checkpoint"])
    _capabilities(d, reminders="local")
    repo = d.parent.parent / "project"
    (repo / ".git").mkdir(parents=True)
    target = repo / "src.py"
    target.write_text("x", encoding="utf-8")
    rem_store.save_local(d, [_rem(rid="rem-c", regex=r"^write_file path=", desc="mine first")],
                         {})
    scripted([write_file(str(target), content="y"),        # held by the REMINDER (precedence)
                   write_file(str(target), content="y"),   # held by the RULE, not skipped
                   write_file(str(target), content="y"),   # neither: both budgets spent
                   finish()])
    status, run_dir = run_routine(d, server, run_ts=TS)
    events, _ = read_events(run_dir / "transcript.jsonl")
    kinds = [e["payload"].get("kind") for e in events if e["type"] == "observation"]
    assert kinds == ["reminder_hold", "assist_hold", "write_file"], kinds
    assert status == "ok"


def test_a_pre_action_assist_holds_the_write_and_re_emitting_it_proceeds(make_routine,
                                                                        scripted):
    """git-checkpoint's moment: the engine versions its OWN directory, not a project repo the
    routine was granted a write root into."""
    d = make_routine(slug="assistr")
    server = _server(d)
    _rule(server, "git-checkpoint", "pre-action", "uncheckpointed-repo-write",
          "commit a checkpoint before the first edit")
    _hold_rule(d, ["git-checkpoint"])
    repo = d.parent.parent / "project"
    (repo / ".git").mkdir(parents=True)
    # a NEW file: overwriting an existing one outside the routine dir needs the run to have
    # read it first (the write_file grounding gate), which is a different refusal entirely
    target = repo / "auth.py"
    _write_root(d, repo)
    ep = scripted([write_file(str(target), content="changed"),
                   write_file(str(target), content="changed"),
                   finish()])
    status, run_dir = run_routine(d, server, run_ts=TS)
    events, _ = read_events(run_dir / "transcript.jsonl")
    holds = [e for e in events if e["type"] == "observation"
             and e["payload"].get("kind") == "assist_hold"]
    assert len(holds) == 1
    assert holds[0]["payload"]["assists"] == ["git-checkpoint/m"]
    assert target.read_text(encoding="utf-8") == "changed"   # the SECOND write went through
    assert target.exists()
    shown = _shown(ep)
    assert "ACTION HELD — it did NOT run." in shown
    assert "commit a checkpoint before the first edit" in shown
    assert "emit the SAME action again" in shown             # the escape is always offered
    assert status == "ok"


def test_a_repo_clean_at_the_runs_first_edit_is_not_held_for_its_second(make_routine,
                                                                        scripted):
    """A clean tree is an undo point — and it stays one for the whole run: HEAD restores what
    the run found. Asking `git status` afresh at every edit read the run's OWN first edit as
    uncommitted work, so the second edit into a clean repo was held — the false positive the
    clean-tree check exists to remove, one write later."""
    import subprocess

    d = make_routine(slug="assistr")
    server = _server(d)
    _rule(server, "git-checkpoint", "pre-action", "uncheckpointed-repo-write", "commit first")
    _hold_rule(d, ["git-checkpoint"])
    repo = d.parent.parent / "project"
    repo.mkdir()
    git = ["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t"]
    subprocess.run([*git, "init", "-q"], check=True)
    (repo / "README").write_text("r", encoding="utf-8")
    subprocess.run([*git, "add", "README"], check=True)
    subprocess.run([*git, "commit", "-qm", "init"], check=True)
    _write_root(d, repo)
    scripted([write_file(str(repo / "a.py"), content="a"),
              write_file(str(repo / "b.py"), content="b"), finish()])
    status, run_dir = run_routine(d, server, run_ts=TS)
    events, _ = read_events(run_dir / "transcript.jsonl")
    kinds = [e["payload"].get("kind") for e in events if e["type"] == "observation"]
    assert kinds == ["write_file", "write_file"], kinds
    assert (repo / "b.py").read_text(encoding="utf-8") == "b"
    assert status == "ok"


def test_the_routines_own_directory_is_never_held_for_a_checkpoint(make_routine, scripted):
    """The engine autocommits the routine's own tree at run end, so it always has an undo
    point — holding a write there would be a turn spent on a problem that does not exist."""
    d = make_routine(slug="assistr")
    server = _server(d)
    _rule(server, "git-checkpoint", "pre-action", "uncheckpointed-repo-write", "commit first")
    _hold_rule(d, ["git-checkpoint"])
    (d / ".git").mkdir(exist_ok=True)          # the routine dir IS a git repo — still exempt
    ep = scripted([write_file("state/a.txt"), write_file(str(d / "artifacts" / "b.md")),
                   finish()])
    status, run_dir = run_routine(d, server, run_ts=TS)
    events, _ = read_events(run_dir / "transcript.jsonl")
    assert not [e for e in events if e["type"] == "observation"
                and e["payload"].get("kind") == "assist_hold"]
    assert "[RULE" not in _shown(ep) and status == "ok"


def test_a_held_action_grounds_no_finish_whichever_source_held_it(make_routine, scripted):
    """The fabrication guard reads one counter, and both hold kinds must be absent from it."""
    d = make_routine(slug="assistr")
    server = _server(d)
    _rule(server, "git-checkpoint", "pre-action", "uncheckpointed-repo-write", "commit first")
    _hold_rule(d, ["git-checkpoint"])
    repo = d.parent.parent / "project"
    (repo / ".git").mkdir(parents=True)
    scripted([write_file(str(repo / "x.py")), finish(),
                   write_file("state/real.txt"), finish()])
    status, run_dir = run_routine(d, server, run_ts=TS)
    events, _ = read_events(run_dir / "transcript.jsonl")
    rejected = [e for e in events if e["type"] == "observation"
                and e["payload"].get("rejected")]
    assert rejected, "a finish grounded only on a HELD action must be refused"
    assert status == "ok"


def test_the_new_predicates_read_the_signals_the_engine_already_keeps(tmp_path):
    """asks-piling-up and the routing check, at the unit level — both are cheap because the
    engine already counts what they ask about."""
    from types import SimpleNamespace

    from rsched.engine.assist_predicates import PREDICATES, Situation

    asks = PREDICATES["asks-piling-up"].check
    loop = SimpleNamespace(ctx=SimpleNamespace(asks_deferred=0))
    assert asks(Situation(loop=loop)) is False
    loop.ctx.asks_deferred = 3
    assert asks(Situation(loop=loop)) is True

    # D131: the receiving half of problem-routing. `ctx.reports_open` is engine bookkeeping —
    # the drain fills it, the report handler empties it as each `answers` lands — and the
    # ledger is asked whether anything ELSE closed the thread since (R2086).
    from rsched import reports

    owes = PREDICATES["unclosed-delivered-report"].check
    loop.ctx.server = SimpleNamespace(routines_home=tmp_path)
    ledger = reports.reports_path(tmp_path)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(json.dumps({"id": "R42", "routine": "a", "target": "b"}) + "\n",
                      encoding="utf-8")
    loop.ctx.reports_open = []
    assert owes(Situation(loop=loop)) is False
    loop.ctx.reports_open = ["R42"]
    assert owes(Situation(loop=loop)) is True
    with ledger.open("a", encoding="utf-8") as fh:        # settled by someone else
        fh.write(json.dumps({"id": "R43", "routine": "c", "settles": ["R42"]}) + "\n")
    assert owes(Situation(loop=loop)) is False

# --- the library surface --------------------------------------------------------------------

def test_a_rules_row_carries_its_assists(tmp_path):
    """A rule with an assist behaves differently for every holder — it can hold an action or
    defer a finish — so a rules listing that omits it describes a rule that no longer exists."""
    from rsched.library_docs import list_docs

    rules = tmp_path / "rules"
    rules.mkdir()
    (rules / "git-checkpoint.md").write_text(
        "---\ntags: [a, b, c]\nassists:\n  - id: m\n    moment: pre-action\n"
        "    predicate: uncheckpointed-repo-write\n    payload: hold\n    line: commit first\n"
        "---\n# rule: git-checkpoint — s\nbody\n", encoding="utf-8")
    (rules / "plain.md").write_text("---\ntags: [a, b, c]\n---\n# rule: plain — s\nbody\n",
                                    encoding="utf-8")
    rows = {d["slug"]: d for d in list_docs(rules)}
    assert rows["plain"]["assists"] == []
    assert rows["git-checkpoint"]["assists"] == [
        {"id": "m", "moment": "pre-action", "payload": "hold",
         "predicate": "uncheckpointed-repo-write", "line": "commit first"}]


def test_the_library_page_lists_the_curated_reminder_store(api_client):
    """It is written by the curator under approval and reaches every routine it names — so the
    surface that shows what is in there, and can take one out again, has to exist."""
    from rsched import reminders as store

    client, tmp_path = api_client
    home = tmp_path / "library" / "reminders"
    store.write_global(home, Reminder(id="rem-20260905-1", regex="^util:fs-ops mv ",
                                      description="mv overwrites silently", scope="global",
                                      created_run="r:1", stats=store.blank_stats(),
                                      reach="universal"))
    body = client.get("/api/library").json()
    assert [r["id"] for r in body["reminders"]] == ["rem-20260905-1"]
    assert body["reminders"][0]["regex"] == "^util:fs-ops mv "
    assert body["reminders"][0]["reach"] == "universal"
    # …and removal is the one lever the page must have: an approval decides what gets IN
    assert client.delete("/api/library/reminders/rem-20260905-1").status_code == 200
    assert client.get("/api/library").json()["reminders"] == []
    assert not store.global_path(home, "rem-20260905-1").exists()


def test_removing_a_reminder_refuses_a_bad_id_and_a_missing_one(api_client):
    client, _tmp = api_client
    assert client.delete("/api/library/reminders/rem-nope").status_code == 404
    # an id is a path segment; one that is not a reminder id never reaches the filesystem
    assert client.delete("/api/library/reminders/notanid").status_code == 400


def test_every_library_kind_the_api_returns_is_a_kind_the_page_shows():
    """The Library page is the whole library or it is misleading: a kind that rides the payload
    with no section of its own is a kind nobody sees."""
    view = (Path(__file__).resolve().parents[1] / "static/views/library.js").read_text(
        encoding="utf-8")
    listed = {"workflows", "rules", "permissions", "playbooks", "utils", "reminders"}
    for kind in listed:
        assert f"data.{kind}" in view, f"the library page never reads data.{kind}"
    # and the counts index names each one, so the page says what it holds before you scroll —
    # and, since the declutter, each count is also the jump to that kind's own section, which is
    # the only way into a catalogue eleven thousand pixels tall.
    start = view.index("const COUNTS = [")
    index_block = view[start:view.index("];", start)]
    for kind in listed:
        assert f'"{kind}"' in index_block, f"the counts index omits {kind}"


# --- on by default ---------------------------------------------------------------------------

def test_the_reminder_layer_is_on_by_default(tmp_path):
    """A caution a run leaves itself is ordinary conduct, not an opt-in capability — and a
    layer nobody switches on is a layer that never learns anything. It is a SETTING, so no
    permission carries it and the floor cannot take it away."""
    from rsched.config.base import DEFAULT_CAPABILITIES, DEFAULT_PERMISSIONS
    from rsched.grants import capabilities_for, floor_capabilities, read_library_requires

    perms = tmp_path / "permissions"
    perms.mkdir(parents=True)
    lib = read_library_requires(perms)
    caps = floor_capabilities(list(DEFAULT_PERMISSIONS), lib,
                              capabilities_for(list(DEFAULT_PERMISSIONS), lib,
                                               dict(DEFAULT_CAPABILITIES)))
    # LOCAL, not global: born local, global is earned — the shared store still needs the dial
    # raised deliberately, and a write there still needs the user's approval
    assert caps["reminders"] == "local"
