"""The `create_routine` action (D58 + D92): registration + schema, the structural
root-conversation gate, and the TWO-STEP preview→confirm flow ending in real
materialization through workflows.scaffold against the seeded library.

Routine creation is initiated from a CONVERSATION only — the handler mirrors detach's
root-conversation gate, and the engine only surfaces the kind to a root conversation
(loop.allowed_tools injection), so a scheduled routine never sees it.

D92 (2026-08-17): the first call stores a draft in the conversation's state/ and returns a
preview; materialization requires the USER to have spoken since. The tests simulate a later
leg by rewriting the draft's recorded pid — each conversation reply runs as its own process,
so "pid differs" IS "a later reply" — and simulate a blocking ask answered mid-reply by
bumping `ctx.user_replies`, which R1310 is: the same leg may confirm once the user has
answered inside it.
"""

import json
import os
import shutil
from pathlib import Path
from types import SimpleNamespace

import yaml

from rsched.config import ServerConfig
from rsched.engine import create_routine
from rsched.engine.actions import validate_action
from rsched.engine.actionschema import KINDS
from rsched.engine.create_routine import DRAFT_RELPATH

REPO = Path(__file__).resolve().parents[1]
SEED = REPO / "library-seed"


def _server(tmp_path):
    """Tmp homes with the REAL library-seed workflows/rules/permissions copied in, so
    scaffold's decompose degrades to its no-LLM fallback (no endpoint) and still writes a
    complete routine dir."""
    lib = tmp_path / "library"
    for kind in ("workflows", "rules", "permissions"):
        shutil.copytree(SEED / kind, lib / kind, ignore=shutil.ignore_patterns("__pycache__"))
    s = ServerConfig()
    s.routines_home = tmp_path / "routines"
    s.routines_home.mkdir()
    s.conversations_home = tmp_path / "conversations"
    s.conversations_home.mkdir()
    s.background_home = tmp_path / "background"
    s.libraries_home = lib
    return s


def _ctx(server, *, home: str, slug="c-1", depth=0):
    routine = SimpleNamespace(slug=slug, dir=getattr(server, home) / slug)
    routine.dir.mkdir(parents=True, exist_ok=True)
    return SimpleNamespace(server=server, routine=routine, depth=depth,
                           run_id=f"{slug}:20260827-030000", user_replies=0,
                           tokens_remaining=lambda: None, add_usage=lambda _usage: None)


def _age_draft(ctx):
    """Simulate a later conversation leg: a reply runs as its own engine process, so a pid
    that differs from the current one is exactly what a real confirm call sees."""
    path = ctx.routine.dir / DRAFT_RELPATH
    draft = json.loads(path.read_text(encoding="utf-8"))
    draft["pid"] = os.getpid() + 1
    path.write_text(json.dumps(draft), encoding="utf-8")


ACTION = {"kind": "create_routine", "target": "arxiv-reading-list",
          "name": "Arxiv reading list", "prompt": "collect new AI papers and keep a list",
          "workflow": "general-task"}


def test_create_routine_registered_and_validated():
    assert "create_routine" in KINDS
    # a well-formed action passes the schema
    assert validate_action({"say": "s", "kind": "create_routine", "target": "my-routine",
                            "name": "My routine", "prompt": "do the thing"}) == []
    # missing required fields → problems
    assert validate_action({"say": "s", "kind": "create_routine", "target": "my-routine"})
    # a non-slug target → a problem
    assert validate_action({"say": "s", "kind": "create_routine", "target": "Not A Slug",
                            "name": "n", "prompt": "p"})


def test_create_routine_queues_outside_a_root_conversation(tmp_path):
    """F328: no user in the loop means PROPOSE, not refuse. Creation stays
    conversation-INITIATED — a scheduled run or a within-reply child leaves a proposal for the
    Decisions page, and still creates nothing itself."""
    from rsched import pending

    server = _server(tmp_path)
    action = {"kind": "create_routine", "target": "x", "name": "X", "prompt": "p"}
    obs = create_routine.handle_create_routine(_ctx(server, home="routines_home"), action)
    assert obs["queued"] and not obs.get("created") and not obs.get("rejected")
    assert "Do NOT re-issue" in obs["next"]

    # a within-reply CHILD is still refused outright — a sub-workflow must not create routines
    # as a side effect, and a proposal from one traces to nothing the user reasoned about
    child = create_routine.handle_create_routine(
        _ctx(server, home="conversations_home", depth=1), action)
    assert child["rejected"] and "child run" in child["reason"]

    assert not (server.routines_home / "x").exists()                    # nothing created
    assert len(pending.load_all(server.routines_home)) == 1             # only the scheduled one


def test_first_call_drafts_and_creates_nothing(tmp_path):
    server = _server(tmp_path)
    ctx = _ctx(server, home="conversations_home")
    obs = create_routine.handle_create_routine(ctx, dict(ACTION))
    assert obs.get("draft") and not obs.get("created")
    assert "confirm" in obs["next"]                       # teaching copy names the next step
    assert (ctx.routine.dir / DRAFT_RELPATH).is_file()    # draft persisted in the conv dir
    assert not (server.routines_home / ACTION["target"]).exists()


def test_unknown_workflow_is_rejected_at_draft_time(tmp_path):
    """F387/R493: a pattern the library does not hold is refused on the FIRST call — before
    the user is asked to confirm — not deep inside scaffold after they already said yes."""
    server = _server(tmp_path)
    ctx = _ctx(server, home="conversations_home")
    obs = create_routine.handle_create_routine(
        ctx, {**ACTION, "workflow": "no-such-pattern"})
    assert obs.get("rejected") and not obs.get("draft")
    assert "no workflow 'no-such-pattern'" in obs["reason"]
    assert not (ctx.routine.dir / DRAFT_RELPATH).exists()   # nothing was stored
    assert {w["slug"] for w in obs["workflow_catalog"]}     # the catalog is offered instead


def test_generate_is_the_last_choice_in_every_catalog(tmp_path):
    """The workflow question is never a closed list: `generate` rides the catalog so "none of
    these fit, build one for this task" is always among the options the user is shown."""
    server = _server(tmp_path)
    ctx = _ctx(server, home="conversations_home")
    obs = create_routine.handle_create_routine(ctx, dict(ACTION))
    assert obs["workflow_catalog"][-1]["slug"] == create_routine.GENERATE_SLUG
    assert obs["workflow_catalog"][-1]["description"]


def test_generate_drafts_the_fitted_pattern_then_builds_on_it(tmp_path, monkeypatch):
    """Picking `generate` is the user's own answer to the workflow question, so the confirming
    call drafts the pattern inline and materializes from the slug it wrote."""
    server = _server(tmp_path)
    ctx = _ctx(server, home="conversations_home")
    seen = {}

    def fake_generate(srv, instruction, hint="", on_usage=None):
        seen.update(instruction=instruction, hint=hint)
        shutil.copy(SEED / "workflows" / "general-task.py",
                    srv.libraries_home / "workflows" / "fitted-pattern.py")
        return "fitted-pattern", ""

    monkeypatch.setattr("rsched.workflows.generate.generate", fake_generate)
    create_routine.handle_create_routine(ctx, {**ACTION, "workflow": "generate"})
    _age_draft(ctx)
    obs = create_routine.handle_create_routine(ctx, {**ACTION, "workflow": "generate"})
    assert obs.get("created") and obs["workflow"] == "fitted-pattern"
    assert seen["instruction"] == ACTION["prompt"] and seen["hint"] == ACTION["name"]
    assert (server.routines_home / ACTION["target"] / "main.md").is_file()


def test_generate_failure_creates_nothing_and_never_falls_back(tmp_path, monkeypatch):
    """The user chose `generate` OVER every catalog entry — silently building on one of them
    would materialize the option they rejected, under the name they approved."""
    server = _server(tmp_path)
    ctx = _ctx(server, home="conversations_home")

    def boom(*_a, **_k):
        raise RuntimeError("the draft never linted clean")

    monkeypatch.setattr("rsched.workflows.generate.generate", boom)
    create_routine.handle_create_routine(ctx, {**ACTION, "workflow": "generate"})
    _age_draft(ctx)
    obs = create_routine.handle_create_routine(ctx, {**ACTION, "workflow": "generate"})
    assert obs.get("rejected") and "never linted clean" in obs["reason"]
    assert not (server.routines_home / ACTION["target"]).exists()
    assert (ctx.routine.dir / DRAFT_RELPATH).is_file()      # the draft stands, re-askable


def test_draft_carries_the_catalog_and_demands_a_decision_not_prose(tmp_path):
    """F383: the pattern choice is surfaced MECHANICALLY — the draft observation lists the
    catalog one line each, and the `next` contract puts every open point to the user as an
    ask_user carrying options, so `general-task` can no longer be a silent default and the
    user is never asked to compose an answer that could have been offered."""
    server = _server(tmp_path)
    ctx = _ctx(server, home="conversations_home")
    obs = create_routine.handle_create_routine(ctx, dict(ACTION))
    catalog = obs["workflow_catalog"]
    assert len(catalog) > 1 and all(c["slug"] and c["description"] for c in catalog)
    assert ACTION["workflow"] in {c["slug"] for c in catalog}
    assert "ask_user" in obs["next"] and "`options`" in obs["next"]
    assert "PRODUCES" in obs["next"] and "DONE" in obs["next"]


def test_same_leg_confirm_is_held(tmp_path):
    """The reply that drafted cannot also confirm while the user has stayed silent — nobody
    has seen the preview yet."""
    server = _server(tmp_path)
    ctx = _ctx(server, home="conversations_home")
    create_routine.handle_create_routine(ctx, dict(ACTION))
    obs = create_routine.handle_create_routine(ctx, dict(ACTION))
    assert obs.get("draft") and obs.get("held") and not obs.get("created")
    assert not (server.routines_home / ACTION["target"]).exists()


def test_same_leg_confirm_materializes_once_the_user_has_answered(tmp_path):
    """R1310: a BLOCKING ask_user is answered inside the drafting leg, so the user's own
    "create it now" arrives without a new process. The pid alone read that as silence and
    bounced them for a second, content-free "go"; the utterance count sees it."""
    server = _server(tmp_path)
    ctx = _ctx(server, home="conversations_home")
    create_routine.handle_create_routine(ctx, dict(ACTION))
    ctx.user_replies += 1                       # the blocking ask came back answered
    obs = create_routine.handle_create_routine(ctx, dict(ACTION))
    assert obs.get("created") and not obs.get("held")
    assert (server.routines_home / ACTION["target"]).is_dir()


def test_a_redraft_rearms_the_hold_at_the_new_count(tmp_path):
    """The count is re-recorded on every (re)draft, so a design change after an answer still
    costs a fresh round-trip — the answer confirmed the OLD draft, not the new one."""
    server = _server(tmp_path)
    ctx = _ctx(server, home="conversations_home")
    create_routine.handle_create_routine(ctx, dict(ACTION))
    ctx.user_replies += 1
    changed = {**ACTION, "name": "Arxiv digest"}
    assert create_routine.handle_create_routine(ctx, changed).get("updated")
    obs = create_routine.handle_create_routine(ctx, changed)
    assert obs.get("held") and not obs.get("created")


def test_confirm_from_later_leg_materializes(tmp_path):
    server = _server(tmp_path)
    ctx = _ctx(server, home="conversations_home")
    create_routine.handle_create_routine(ctx, dict(ACTION))
    _age_draft(ctx)                                       # the user answered; a new leg calls
    obs = create_routine.handle_create_routine(ctx, dict(ACTION))
    assert obs.get("created") and obs["slug"] == "arxiv-reading-list"
    new_dir = server.routines_home / "arxiv-reading-list"
    assert new_dir.is_dir()
    assert (new_dir / "main.md").is_file()                # the decomposed workflow
    cfg = yaml.safe_load((new_dir / "routine.yaml").read_text(encoding="utf-8"))
    assert cfg["slug"] == "arxiv-reading-list" and cfg["name"] == "Arxiv reading list"
    assert not (ctx.routine.dir / DRAFT_RELPATH).exists()  # draft consumed


def test_changed_fields_redraft_instead_of_confirming(tmp_path):
    """A design change on the confirming call is NOT a confirmation — it replaces the draft
    and restarts the round-trip."""
    server = _server(tmp_path)
    ctx = _ctx(server, home="conversations_home")
    create_routine.handle_create_routine(ctx, dict(ACTION))
    _age_draft(ctx)
    changed = dict(ACTION, name="Arxiv digest")
    obs = create_routine.handle_create_routine(ctx, changed)
    assert obs.get("draft") and obs.get("updated") and not obs.get("created")
    assert not (server.routines_home / ACTION["target"]).exists()
    # …and the SAME changed fields from yet another leg now do confirm
    _age_draft(ctx)
    obs = create_routine.handle_create_routine(ctx, changed)
    assert obs.get("created")


def test_create_routine_rejects_duplicate_slug(tmp_path):
    server = _server(tmp_path)
    ctx = _ctx(server, home="conversations_home")
    (server.routines_home / "taken").mkdir()
    obs = create_routine.handle_create_routine(
        ctx, {"kind": "create_routine", "target": "taken", "name": "Taken", "prompt": "p"})
    assert obs.get("already_exists") and not obs.get("created")


def test_draft_observation_never_reads_as_created(tmp_path):
    """0.222.0 (R476/R477/R478): the D92 preview obs fell through to the created-copy,
    telling the agent to announce a routine that did not exist."""
    from rsched.engine.observations import format_observation
    server = _server(tmp_path)
    ctx = _ctx(server, home="conversations_home")
    obs = create_routine.handle_create_routine(ctx, dict(ACTION))
    text = format_observation(obs)
    assert "NOTHING CREATED YET" in text
    assert "created routine" not in text
    # the same-leg confirm attempt renders its hold copy too
    held = create_routine.handle_create_routine(ctx, dict(ACTION))
    held_text = format_observation(held)
    assert "HELD" in held_text and "created routine" not in held_text


def test_created_observation_names_rescan_cadence(tmp_path):
    """R477: 'shortly' was vague — the created obs now carries and renders the actual
    registry-rescan cadence."""
    from rsched.engine.observations import format_observation
    server = _server(tmp_path)
    ctx = _ctx(server, home="conversations_home")
    create_routine.handle_create_routine(ctx, dict(ACTION))
    _age_draft(ctx)
    obs = create_routine.handle_create_routine(ctx, dict(ACTION))
    assert obs.get("created") and obs["rescan_s"] == server.registry_rescan_s
    assert f"~{server.registry_rescan_s}s" in format_observation(obs)


def test_mid_build_oserror_is_teaching_error_and_leaves_no_dir(tmp_path, monkeypatch):
    """R478: a filesystem shift during the slow decompose crashed the engine rc=1 and left
    an empty skeleton. Now: an error observation, and NO half-made dir — the routine dir is
    only created after decompose returns."""
    from rsched.workflows import adapt
    server = _server(tmp_path)
    ctx = _ctx(server, home="conversations_home")
    create_routine.handle_create_routine(ctx, dict(ACTION))
    _age_draft(ctx)

    def boom(*a, **k):
        raise FileNotFoundError("stages/orient-project-state.md vanished mid-build")

    monkeypatch.setattr(adapt, "decompose", boom)
    obs = create_routine.handle_create_routine(ctx, dict(ACTION))
    assert "materialization failed mid-build" in obs.get("error", "")
    assert not (server.routines_home / ACTION["target"]).exists()


def test_the_done_answer_becomes_the_new_routine_s_stopping_conditions(tmp_path):
    """F334/D98 made stopping conditions what decides when a job is finished, and F383 already
    makes creation ask "what DONE looks like for one run, in the user's own words" — but that
    answer only ever reached the instruction prose, so every routine ever created started with
    an empty goal document, bounded by its budgets alone. `stopping` carries it through.
    """
    from rsched.engine import stopping as stopping_mod

    server = _server(tmp_path)
    ctx = _ctx(server, home="conversations_home")
    action = dict(ACTION, stopping=["the digest is published", "  ", "the link resolves"])
    create_routine.handle_create_routine(ctx, action)
    _age_draft(ctx)
    obs = create_routine.handle_create_routine(ctx, action)
    assert obs.get("created")

    doc = stopping_mod.load(server.routines_home / ACTION["target"])
    # blank entries are dropped, order is kept, ids are assigned by the store
    assert [c["text"] for c in doc["conditions"]] == ["the digest is published",
                                                      "the link resolves"]
    assert [c["id"] for c in doc["conditions"]] == ["s1", "s2"]
    assert all(c["status"] == "open" and c["group"] == "g1" for c in doc["conditions"])


def test_no_stopping_answer_seeds_no_conditions(tmp_path):
    """An invented condition is worse than none — every later run has to account for it."""
    from rsched.engine import stopping as stopping_mod

    server = _server(tmp_path)
    ctx = _ctx(server, home="conversations_home")
    create_routine.handle_create_routine(ctx, dict(ACTION))
    _age_draft(ctx)
    create_routine.handle_create_routine(ctx, dict(ACTION))
    assert stopping_mod.load(server.routines_home / ACTION["target"])["conditions"] == []


def test_a_changed_stopping_answer_restarts_the_confirmation(tmp_path):
    """`stopping` is part of the draft's identity like every other field: changing what DONE
    means is a design change, and a design change must go back to the user."""
    server = _server(tmp_path)
    ctx = _ctx(server, home="conversations_home")
    create_routine.handle_create_routine(ctx, dict(ACTION, stopping=["the digest is published"]))
    _age_draft(ctx)
    obs = create_routine.handle_create_routine(ctx, dict(ACTION, stopping=["something else"]))
    assert obs.get("draft") and obs.get("updated") and not obs.get("created")


def test_materialize_generates_a_comprehensive_description(tmp_path, monkeypatch):
    """The materialized routine.yaml carries a GENERATED description (purpose / requirements /
    side effects / dependencies with other routines), not just the routine's name."""
    from rsched.config import ModelRef
    from rsched.endpoints.base import Completion

    generated = ("Collects new AI papers each run and maintains a deduped reading list; uses the "
                 "websearch util; writes list.md to its own dir; feeds no other routine.")

    class _Ep:
        def complete(self, messages, **kw):
            return Completion(text=json.dumps({"description": generated}),
                              parsed={"description": generated} if kw.get("schema") else None,
                              usage={"in": 7, "out": 3})

    class _Reg:
        def __init__(self, server):
            pass

        def for_system(self):
            return _Ep(), ModelRef(endpoint="scripted", model="sys", name="system")

    monkeypatch.setattr("rsched.workflows.suggest.EndpointRegistry", _Reg)

    server = _server(tmp_path)
    ctx = _ctx(server, home="conversations_home")
    create_routine.handle_create_routine(ctx, dict(ACTION))
    _age_draft(ctx)
    obs = create_routine.handle_create_routine(ctx, dict(ACTION))
    assert obs.get("created")
    cfg = yaml.safe_load(
        (server.routines_home / ACTION["target"] / "routine.yaml").read_text(encoding="utf-8"))
    assert cfg["description"] == generated
    assert cfg["description"] != ACTION["name"]         # not the old `description = name`


def test_harness_patterns_are_not_offered_as_buildable(tmp_path):
    """`converse` is a HARNESS: it assumes a present user who reads the reply and writes back,
    which a scheduled routine never has — its own when_to_use says "Not for scheduled
    routines". The `meta` tag existed for exactly this ("keeps it out of spawn-pattern lists
    and wizard suggestions"); the creation catalog was the one surface that never applied it,
    so it offered converse as a choice against the pattern's own instructions.
    """
    from rsched.workflows.library import list_workflows

    server = _server(tmp_path)
    ctx = _ctx(server, home="conversations_home")
    obs = create_routine.handle_create_routine(ctx, dict(ACTION))
    offered = {w["slug"] for w in obs["workflow_catalog"]}
    library = list_workflows(server.libraries_home)
    meta_slugs = {w["slug"] for w in library if create_routine.META_TAG in (w["tags"] or [])}
    assert meta_slugs, "the fixture library carries at least one harness pattern"
    assert not (offered & meta_slugs), f"harness patterns offered: {offered & meta_slugs}"
    # everything else still is, so the filter did not empty the catalog
    assert offered - {create_routine.GENERATE_SLUG}


def test_draft_carries_the_standing_design_checks(tmp_path):
    """The intake contract has ONE live copy. The `clarify-instruction` pattern that once held
    a second went stale unnoticed because nothing executed it — it still described conduct as
    per-routine "traits" long after rules became one shared library doc."""
    server = _server(tmp_path)
    ctx = _ctx(server, home="conversations_home")
    obs = create_routine.handle_create_routine(ctx, dict(ACTION))
    checks = " ".join(obs["design_checks"])
    for needle in ("SHAPE", "MECHANISM", "OWNERSHIP", "SCOPE"):
        assert needle in checks, needle
    assert "read_rule" in checks and "scripts/" in checks
    assert "trait" not in checks.lower()
