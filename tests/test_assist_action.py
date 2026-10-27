"""The `action` PAYLOAD on a rule assist — a rule that DOES the thing its moment calls for.

`tests/test_hooks_perform.py` pins the performer (`hooks/perform.py`: one authored action
through the model's own gates, no model turn, no veto). These pin its first PRODUCTION caller:
a library rule declaring `payload: action` at the `boundary` moment, performed by
`engine/assist.py` when its predicate fires.

Why this is the step that matters, in the operator's own words (2026-10-26): *"The hook system
also should allow to extend the implementation of parts of the harness^2 features we already
started"*. Until something fires an `action` payload in a live run, the payload is honest in the
registry and unexercised — which `assists.py` recorded for a month as the rung that "needs a
helper channel that is not built".

Three invariants are asserted, each one his ruling rather than this layer's taste:

- a performed action RUNS and the run is TOLD, at no turn cost;
- a FAILED one changes nothing about what the run does next — *"No veto ever — a hook performs
  and reports; only a hold (prose to the model) can stop an action"*;
- no assist may perform a kind that STEERS a run, because an assist is authored once in a
  library rule and fires in every routine that holds it, with no model turn in between.

A new file because `tests/test_assists.py` is AT its size-ratchet ceiling (857 lines) and may
only shrink.
"""

import json

import yaml

from conftest import finish, write_file
from helpers import prompt_text, server_for
from rsched import assists as lib
from rsched.assists import normalize_assists
from rsched.engine.runtime import run_routine
from rsched.engine.transcript import read_events

TS = "20260905-190000"


def _assist(**over):
    base = {"id": "a1", "moment": "boundary", "predicate": "user-corrected",
            "payload": "action", "line": "the operative line",
            "action": {"say": "x", "kind": "write_file", "path": "state/hook.txt",
                       "content": "written by a rule"}}
    return [{**base, **over}]


# --- the declaration ------------------------------------------------------------------------

def test_the_action_rung_exists_and_is_named_as_the_registry_names_it():
    """The design note's axis was `remind -> scaffold -> do -> hold` and called the middle rungs
    unbuilt. `do` is built, and it is spelled `action` — the name `hooks/points.PAYLOADS`
    already uses, so the two tables cannot come to disagree (`test_hooks_points` pins that).
    `scaffold` stays out: the engine still cannot honour it.
    """
    from rsched.hooks import points

    assert "action" in lib.PAYLOADS
    assert "scaffold" not in lib.PAYLOADS
    assert "do" not in lib.PAYLOADS
    assert set(points.PAYLOADS) >= set(lib.PAYLOADS)


def test_a_well_formed_action_assist_normalizes_and_keeps_its_action():
    got, problems = normalize_assists(_assist(), rule="fix-the-cause")
    assert problems == []
    assert len(got) == 1
    assert got[0].payload == "action"
    assert got[0].action == {"say": "x", "kind": "write_file", "path": "state/hook.txt",
                             "content": "written by a rule"}


def test_the_free_moment_that_carries_an_action_is_the_boundary_and_only_it():
    """Not policy — ORDERING. The observation-moment assists are asked BEFORE their observation
    is recorded so the record can name what fired on it, which a resumed leg rebuilds its guards
    from; performing there would file the hook's own observation ahead of the one that triggered
    it. The turn boundary is where an authored action already runs with no model turn.
    """
    assert "action" in lib.MOMENT_PAYLOADS["boundary"]
    assert "action" not in lib.MOMENT_PAYLOADS["observation"]
    assert "action" not in lib.MOMENT_PAYLOADS["pre-action"]
    assert "action" not in lib.MOMENT_PAYLOADS["pre-finish"]


def test_an_action_payload_without_an_action_is_refused():
    _got, problems = normalize_assists(
        [{"id": "a1", "moment": "boundary", "predicate": "user-corrected",
          "payload": "action", "line": "l"}], rule="r")
    assert problems and "needs an 'action' mapping" in problems[0]


def test_an_action_on_a_remind_assist_is_refused_rather_than_ignored():
    """A silently ignored field is how an author believes a rule does something it does not."""
    _got, problems = normalize_assists(_assist(payload="remind"), rule="r")
    assert problems and "belongs to an `action` payload" in problems[0]


def test_an_action_naming_no_kind_is_refused():
    _got, problems = normalize_assists(_assist(action={"say": "x"}), rule="r")
    assert problems and "names no 'kind'" in problems[0]


def test_every_steering_kind_is_refused_with_the_set_named():
    """His ruling as a refusal: a hook performs and reports, so an assist may not perform the
    kinds that decide where a run GOES. Each is checked, because a set that drifts open one
    kind at a time is how a hook comes to steer a run nobody asked it to.
    """
    assert lib.STEERING_KINDS, "the steering set is empty — nothing would be refused"
    for kind in lib.STEERING_KINDS:
        _got, problems = normalize_assists(_assist(action={"say": "x", "kind": kind}),
                                           rule="r")
        assert problems, f"{kind} steers a run and must be refused"
        assert "STEERS the run" in problems[0]


def test_the_rule_linter_refuses_a_steering_action_at_authoring_time():
    """The same problems become an authoring error, so a bad block never lands in the library."""
    from rsched.workflows.lint import lint_rule_text

    text = ("---\ntags: [a, b, c]\nassists:\n  - id: m\n    moment: boundary\n"
            "    predicate: user-corrected\n    payload: action\n    line: l\n"
            "    action: {say: x, kind: finish}\n---\n# rule: r — s\nbody\n")
    problems = lint_rule_text(text, filename="r.md")
    assert any("STEERS the run" in p for p in problems), problems


# --- the production caller, end to end ------------------------------------------------------

def _rule_with_action(server, slug: str, action: dict, *, line: str) -> None:
    home = server.rules_home
    home.mkdir(parents=True, exist_ok=True)
    (home / f"{slug}.md").write_text(
        "---\ntags: [a, b, c]\nassists:\n  - id: m\n    moment: boundary\n"
        "    predicate: user-corrected\n    payload: action\n"
        f"    line: {line}\n    action: {json.dumps(action)}\n---\n"
        f"# rule: {slug} — s\nbody\n", encoding="utf-8")


def _hold_rule(routine_dir, slugs: list[str]) -> None:
    path = routine_dir / "routine.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw["rules"] = slugs
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")


def _run_with(make_routine, scripted, action: dict, *, line="do the thing"):
    """A run holding one rule whose boundary assist PERFORMS `action`, fired by the user
    speaking to the run in flight (`user-corrected`, the one boundary predicate a test can
    trigger deterministically).
    """
    from rsched.engine.inbox import file_message

    d = make_routine(slug="assistact")
    server = server_for(d)
    _rule_with_action(server, "fix-the-cause", action, line=line)
    _hold_rule(d, ["fix-the-cause"])

    def correcting():
        file_message(d, "actually, always use the short form", via="web")
        return write_file("state/a.txt")

    ep = scripted([correcting, write_file("state/b.txt"), finish()])
    status, run_dir = run_routine(d, server, run_ts=TS)
    events, _ = read_events(run_dir / "transcript.jsonl")
    return d, ep, status, events, run_dir


def _engine_notes(events) -> list[str]:
    return [e["payload"]["text"] for e in events if e["type"] == "user_injection"
            and e["payload"].get("source") == "engine"]


def test_a_boundary_action_assist_really_runs_its_action_and_tells_the_run(
        make_routine, scripted):
    """The production caller, proven by its EFFECT: the file the rule's action writes exists,
    the run is told in the boundary's own carrier, and the transcript names who ran it.
    """
    d, ep, status, events, _run_dir = _run_with(
        make_routine, scripted,
        {"say": "the rule acts", "kind": "write_file", "path": "state/hook.txt",
         "content": "written by a rule"})

    assert (d / "state" / "hook.txt").read_text(encoding="utf-8") == "written by a rule"
    notes = _engine_notes(events)
    assert any("ran a `write_file` action for you" in n for n in notes), notes
    assert any("the operative line" in n or "do the thing" in n for n in notes), notes
    assert "do the thing" in prompt_text(ep)        # the line is read, whatever the action did

    performed = [e for e in events if e["type"] == "observation"
                 and e["payload"].get("hook_action")]
    assert len(performed) == 1, "the performed action is not in the transcript exactly once"
    assert performed[0]["payload"]["point"] == "turn-boundary"
    assert performed[0]["payload"]["source"] == "rule:fix-the-cause/m"
    assert status == "ok"


def test_a_performed_action_fires_once_per_run_however_often_the_moment_recurs(
        make_routine, scripted):
    """The once-per-run guard is the whole layer's, and an action rung needs it most: a rule
    that acts on every boundary would spend a run's budget on a rule nobody asked twice.
    """
    d, _ep, status, events, _run_dir = _run_with(
        make_routine, scripted,
        {"say": "the rule acts", "kind": "write_file", "path": "state/hook.txt",
         "content": "once"})

    performed = [e for e in events if e["type"] == "observation"
                 and e["payload"].get("hook_action")]
    assert len(performed) == 1
    assert json.loads(lib.state_path(d).read_text())["fix-the-cause/m"] == 1
    assert status == "ok"


def test_an_action_the_routine_may_not_take_performs_nothing_and_the_run_is_told(
        make_routine, scripted):
    """The capability bound, which the assist layer gets for free from `perform`: a rule is
    authored once and held by routines with different capabilities, so the gate is at FIRE
    time. A refused action is reported — it does not become a failed turn, and it does not
    stop the run.

    `write_util` without the capability is REFUSED by its own handler with `declined`, not with
    an `error` key — which is why the note's verdict is `observations.is_failure` and not a
    check of `error`: the first version of this code told the run a declined util authoring
    "completed".
    """
    _d, _ep, status, events, _run_dir = _run_with(
        make_routine, scripted,
        {"say": "the rule acts", "kind": "write_util", "name": "nope", "content": "x"})

    performed = [e["payload"] for e in events if e["type"] == "observation"
                 and e["payload"].get("hook_action")]
    assert len(performed) == 1
    assert performed[0]["header_ok"] is False      # the gate refused; nothing was installed
    notes = _engine_notes(events)
    assert any("did NOT run" in n for n in notes), notes
    assert any("reported, not enforced" in n for n in notes), notes
    assert not any("completed" in n for n in notes), (
        "a refused action read to the run as a completed one", notes)
    assert status == "ok", "a refused hook action must never fail the run"


def test_the_failure_classifier_counts_a_doc_standard_refusal_as_a_failure():
    """The defect this step's first live fire exposed, pinned where it was found.

    `observations.is_failure` is the engine's ONE answer to "did this observation report a
    failure", and it checked `lint_ok`/`selftest_ok` but not `header_ok` — so a `write_util`
    the doc-standard gate REFUSED (the util was never written) read as a success to every
    consumer: the chain stopper, the exit gate, the `turn:error` reminder trigger, and the
    boundary note this step added. The three `*_ok` flags are one shape and are read together.
    """
    from rsched.engine.observations import is_failure

    assert is_failure({"kind": "write_util", "name": "n", "header_ok": False,
                       "problems": ["no module docstring"]}) is True
    assert is_failure({"kind": "write_util", "name": "n", "created": True,
                       "selftest_ok": True}) is False


def test_an_action_outside_the_workflows_tools_never_dispatches(make_routine, scripted):
    """The other half of the same gate, at the kind level: a workflow's `tools:` allowlist.
    The rule is in the library either way — the routine's own vocabulary is what bounds it.
    """
    d = make_routine(slug="assistact", workflow_md=(
        "---\nmaterialized_from: {slug: test-flow, commit: abc123, version: 1}\n"
        "tools: [write_file, read_file]\n---\n\n## Run flow\n1. Act.\n"
        "\n## Phases\n- **only** — single phase.\n"
        "\n## Completion criteria\n- Done.\n"))
    server = server_for(d)
    _rule_with_action(server, "fix-the-cause",
                      {"say": "the rule acts", "kind": "llm", "prompt": "hi"},
                      line="do the thing")
    _hold_rule(d, ["fix-the-cause"])

    from rsched.engine.inbox import file_message

    def correcting():
        file_message(d, "actually, always use the short form", via="web")
        return write_file("state/a.txt")

    ep = scripted([correcting, write_file("state/b.txt"), finish()])
    status, run_dir = run_routine(d, server, run_ts=TS)
    events, _ = read_events(run_dir / "transcript.jsonl")

    notes = _engine_notes(events)
    assert any("did NOT run" in n and "not available in this workflow" in n for n in notes), \
        notes
    assert status == "ok"
    assert prompt_text(ep)       # the run went on


def test_a_failing_action_never_changes_what_the_run_does_next(make_routine, scripted):
    """*"No veto ever"* at the level that matters: the run's own next action still runs, and
    the run still ends `ok`. The action here cannot succeed (no such path to read).
    """
    d, _ep, status, events, _run_dir = _run_with(
        make_routine, scripted,
        {"say": "the rule acts", "kind": "read_file", "path": "state/does-not-exist.txt"})

    assert (d / "state" / "b.txt").exists(), "the run's own next action did not run"
    assert status == "ok"
    performed = [e for e in events if e["type"] == "observation"
                 and e["payload"].get("hook_action")]
    assert len(performed) == 1, "the attempt is recorded whatever came of it"
