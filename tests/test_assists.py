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
    base = {"id": "a1", "moment": "observation", "predicate": "observation-failed",
            "payload": "remind", "line": "read the failure before reacting to it"}
    return [{**base, **over}]


# --- the declaration ----------------------------------------------------------------------

def test_a_well_formed_block_normalizes():
    got, problems = normalize_assists(_assist(), rule="error-recovery")
    assert problems == []
    assert len(got) == 1
    a = got[0]
    assert a.rule == "error-recovery" and a.id == "a1" and a.key == "error-recovery/a1"
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
    bad = head + "assists:\n  - id: a1\n    moment: nowhere\n    predicate: observation-failed\n"
    problems = lint_rule_text(bad + body, filename="x.md")
    assert any("'moment' must be one of" in p for p in problems)
    good = head + ("assists:\n  - id: a1\n    moment: observation\n"
                   "    predicate: observation-failed\n    payload: remind\n"
                   "    line: read the failure before reacting to it\n")
    assert lint_rule_text(good + body, filename="x.md") == []


def test_the_seed_rules_that_declare_assists_are_valid():
    """The three shipped declarations, checked against the live predicate registry."""
    declared = {}
    for path in sorted(SEED.glob("*.md")):
        meta = yaml.safe_load(path.read_text(encoding="utf-8").split("---")[1])
        got, problems = normalize_assists(meta.get("assists"), rule=path.stem)
        assert problems == [], (path.stem, problems)
        if got:
            declared[path.stem] = got
    assert set(declared) == {"error-recovery", "intent-inference", "decision-record",
                             "git-checkpoint", "ask-policy", "unexamined-is-not-clean"}
    # every moment is exercised by a real rule, and both built payloads with it
    assert {a.moment for rule in declared.values() for a in rule} == set(lib.MOMENTS)
    assert {a.payload for rule in declared.values() for a in rule} == set(lib.PAYLOADS)
    # …and the migration carries exactly the rules that declare one, or a live library
    # silently keeps the old text
    from rsched.migrate_rule_assists import RULES
    assert set(RULES) == set(declared)


def test_only_the_rules_a_routine_holds_contribute(tmp_path):
    rules = tmp_path / "rules"
    rules.mkdir()
    for slug, moment, pred in (("held", "observation", "observation-failed"),
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


def _run(make_routine, scripted, replies, *, slug, moment, predicate,
         line="the operative line"):
    d = make_routine(slug="assistr")
    server = _server(d)
    _rule(server, slug, moment, predicate, line)
    _hold_rule(d, [slug])
    ep = scripted(replies)
    status, run_dir = run_routine(d, server, run_ts=TS)
    events, _ = read_events(run_dir / "transcript.jsonl")
    return d, ep, status, events


def _shown(ep) -> str:
    return json.dumps(ep.calls[-1]["messages"], ensure_ascii=False)


def test_an_observation_assist_rides_the_tail_of_the_failure(make_routine, scripted):
    """error-recovery's moment. Costs no turn: it appends to the observation the run was
    getting anyway."""
    d, ep, status, _events = _run(
        make_routine, scripted,
        [util("nonexistent-util"), write_file("state/a.txt"), finish()],
        slug="error-recovery", moment="observation", predicate="observation-failed",
        line="read the failure before you react to it")
    shown = _shown(ep)
    assert "[RULE error-recovery — the call you just made failed]" in shown
    assert "read the failure before you react to it" in shown
    assert "read_rule name=error-recovery" in shown          # the rest of the rule stays put
    assert shown.count("[RULE error-recovery") == 1          # once per run, not per failure
    assert json.loads(lib.state_path(d).read_text())["error-recovery/m"] == 1
    assert status == "ok"


def test_a_boundary_assist_arrives_as_an_engine_note(make_routine, scripted):
    """intent-inference's moment. The user speaking to a run IN FLIGHT is the edge — a
    message waiting before the run is its task, not an intervention in it — and the note is
    appended at the turn boundary, the same carrier a mid-run rule binding uses."""
    from rsched.engine.inbox import file_message

    d = make_routine(slug="assistr")
    server = _server(d)
    _rule(server, "intent-inference", "boundary", "user-corrected", "name the intention")
    _hold_rule(d, ["intent-inference"])

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
    assert "[RULE intent-inference — the user just said something to this run]" in notes[0]
    assert "name the intention" in _shown(ep)
    assert len(notes) == 1, "one fire per run, however often the user speaks"
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
    """The predicate reads turn_records, which survives compaction — a run that DID write
    its ledger is not asked to."""
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
    # the run dir must sit directly under conversations_home for _is_conversation to see it
    moved = convs / d.name
    d.rename(moved)
    server.routines_home = convs
    _rule(server, "decision-record", "pre-finish", "ledger-untouched", "append one entry")
    _hold_rule(moved, ["decision-record"])
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
    _rule(server, "error-recovery", "observation", "observation-failed", "a line")
    _hold_rule(d, [])                       # the rule exists in the library, unheld here
    ep = scripted([util("nonexistent-util"), write_file("state/a.txt"), finish()])
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

    monkeypatch.setitem(assist_predicates.PREDICATES, "observation-failed",
                        assist_predicates.Predicate(moment="observation", check=boom,
                                                    describes="d"))
    _d, ep, status, _events = _run(
        make_routine, scripted,
        [util("nonexistent-util"), write_file("state/a.txt"), finish()],
        slug="error-recovery", moment="observation", predicate="observation-failed")
    assert "[RULE" not in _shown(ep)
    assert status == "ok"


def test_every_registered_predicate_declares_a_reachable_moment():
    assert set(PREDICATES), "the registry must not be empty"
    for name, predicate in PREDICATES.items():
        assert predicate.moment in lib.MOMENTS, (name, predicate.moment)
        assert predicate.describes.strip(), name

# --- the one-shot migration ----------------------------------------------------------------

def _live_copy(tmp_path) -> tuple[Path, Path]:
    """A stand-in live library: the seed rules with their assists: blocks stripped, which is
    exactly what a pre-0.305.0 instance holds."""
    import shutil

    from rsched.migrate_rule_assists import _strip_assists

    live = tmp_path / "live-rules"
    live.mkdir()
    for path in SEED.glob("*.md"):
        shutil.copy(path, live / path.name)
        text = (live / path.name).read_text(encoding="utf-8")
        (live / path.name).write_text(_strip_assists(text), encoding="utf-8")
    return live, SEED


def test_the_migration_carries_the_blocks_into_live_rules(tmp_path):
    from rsched.migrate_rule_assists import RULES, migrate

    live, seed = _live_copy(tmp_path)
    assert not any("assists:" in (live / f"{r}.md").read_text(encoding="utf-8") for r in RULES)
    notes = migrate(live, seed)
    assert all(n.endswith("installed") for n in notes), notes
    for slug in RULES:
        text = (live / f"{slug}.md").read_text(encoding="utf-8")
        assert "\nassists:\n" in text
        # the block landed in the FRONTMATTER, above the closing fence, and still parses
        meta = yaml.safe_load(text.split("---")[1])
        got, problems = normalize_assists(meta.get("assists"), rule=slug)
        assert problems == [] and len(got) == 1
        # …and it is now byte-identical to the seed
        assert text == (seed / f"{slug}.md").read_text(encoding="utf-8")


def test_the_migration_is_idempotent(tmp_path):
    from rsched.migrate_rule_assists import RULES, migrate

    live, seed = _live_copy(tmp_path)
    migrate(live, seed)
    before = {r: (live / f"{r}.md").read_text(encoding="utf-8") for r in RULES}
    notes = migrate(live, seed)
    assert all("already carries" in n for n in notes), notes
    assert {r: (live / f"{r}.md").read_text(encoding="utf-8") for r in RULES} == before


def test_the_migration_leaves_an_edited_rule_alone(tmp_path):
    """A local edit outranks the seed — the same rule the add-only seed sync follows. This
    copies a frontmatter block, so it must never become a content sync by accident."""
    from rsched.migrate_rule_assists import migrate

    live, seed = _live_copy(tmp_path)
    edited = live / "error-recovery.md"
    edited.write_text(edited.read_text(encoding="utf-8")
                      + "\n\nAn operator added this paragraph.\n", encoding="utf-8")
    notes = {n.split(":")[0]: n for n in migrate(live, seed)}
    assert "diverged from the seed" in notes["error-recovery"]
    assert "assists:" not in edited.read_text(encoding="utf-8")
    assert "installed" in notes["decision-record"]      # the untouched ones still land


def test_the_migration_survives_a_missing_rule(tmp_path):
    from rsched.migrate_rule_assists import migrate, run

    live, seed = _live_copy(tmp_path)
    (live / "intent-inference.md").unlink()
    notes = {n.split(":")[0]: n for n in migrate(live, seed)}
    assert "skipped" in notes["intent-inference"]
    assert "installed" in notes["error-recovery"]
    assert run(live, seed) == 0                        # already applied by migrate() above

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


def test_the_new_predicates_read_the_signals_the_engine_already_keeps():
    """asks-piling-up and the all-clear check, at the unit level — both are cheap because the
    engine already counts what they ask about."""
    from types import SimpleNamespace

    from rsched.engine.assist_predicates import PREDICATES, Situation

    asks = PREDICATES["asks-piling-up"].check
    loop = SimpleNamespace(ctx=SimpleNamespace(asks_deferred=0))
    assert asks(Situation(loop=loop)) is False
    loop.ctx.asks_deferred = 3
    assert asks(Situation(loop=loop)) is True

    clean = PREDICATES["clean-claim-without-a-denominator"].check
    def sit(summary):
        return Situation(loop=loop, action={"kind": "finish", "summary": summary})
    assert clean(sit("Reviewed the module. All clear.")) is True
    assert clean(sit("Checked 40 of 46 files — all clear on those.")) is False
    assert clean(sit("Found three defects and fixed them.")) is False
