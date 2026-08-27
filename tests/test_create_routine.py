"""The `create_routine` action (D58 + D92): registration + schema, the structural
root-conversation gate, and the TWO-STEP preview→confirm flow ending in real
materialization through workflows.scaffold against the seeded library.

Routine creation is initiated from a CONVERSATION only — the handler mirrors detach's
root-conversation gate, and the engine only surfaces the kind to a root conversation
(loop.allowed_tools injection), so a scheduled routine never sees it.

D92 (2026-08-17): the first call stores a draft in the conversation's state/ and returns a
preview; materialization requires an identical call from a LATER leg (different engine
process — i.e. after a user round-trip). The tests simulate the later leg by rewriting the
draft's recorded pid: each conversation reply runs as its own process, so "pid differs"
IS "a later reply".
"""

import json
import os
import shutil
from pathlib import Path
from types import SimpleNamespace

import yaml

from rsched.config import ServerConfig
from rsched.engine import create_routine
from rsched.engine.actions import KINDS, validate_action
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
                           run_id=f"{slug}:20260827-030000")


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


def test_generate_is_rejected_as_a_subtask_capability(tmp_path):
    """F387: `workflow: generate` drafts a NEW pattern in a subtask — it is not a library
    slug. It used to store cleanly and fail at materialize, i.e. after confirmation."""
    server = _server(tmp_path)
    ctx = _ctx(server, home="conversations_home")
    obs = create_routine.handle_create_routine(ctx, {**ACTION, "workflow": "generate"})
    assert obs.get("rejected")
    assert "subtask capability" in obs["reason"]
    assert not (ctx.routine.dir / DRAFT_RELPATH).exists()


def test_draft_carries_the_pattern_catalog_and_demands_an_alternative(tmp_path):
    """F383: the pattern choice is surfaced MECHANICALLY — the draft observation lists the
    library catalog one line each, and the relay contract requires naming the chosen pattern
    plus one alternative, so `general-task` can no longer be a silent default."""
    server = _server(tmp_path)
    ctx = _ctx(server, home="conversations_home")
    obs = create_routine.handle_create_routine(ctx, dict(ACTION))
    catalog = obs["workflow_catalog"]
    assert len(catalog) > 1 and all(c["slug"] and c["description"] for c in catalog)
    assert ACTION["workflow"] in {c["slug"] for c in catalog}
    assert "one alternative" in obs["next"] and "DONE" in obs["next"]


def test_same_leg_confirm_is_held(tmp_path):
    """The reply that drafted cannot also confirm — no user has seen the preview yet."""
    server = _server(tmp_path)
    ctx = _ctx(server, home="conversations_home")
    create_routine.handle_create_routine(ctx, dict(ACTION))
    obs = create_routine.handle_create_routine(ctx, dict(ACTION))
    assert obs.get("draft") and obs.get("held") and not obs.get("created")
    assert not (server.routines_home / ACTION["target"]).exists()


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
