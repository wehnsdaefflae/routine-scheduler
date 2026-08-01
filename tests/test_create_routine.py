"""The `create_routine` action (D58): registration + schema, the structural root-conversation
gate, and the real materialization through workflows.scaffold against the seeded library.

Routine creation is initiated from a CONVERSATION only — the handler mirrors detach's
root-conversation gate, and the engine only surfaces the kind to a root conversation
(loop.allowed_tools injection), so a scheduled routine never sees it.
"""

import shutil
from pathlib import Path
from types import SimpleNamespace

import yaml

from rsched.config import ServerConfig
from rsched.engine import create_routine
from rsched.engine.actions import KINDS, validate_action

REPO = Path(__file__).resolve().parents[1]
SEED = REPO / "library-seed"


def _server(tmp_path):
    """Tmp homes with the REAL library-seed workflows/traits/permissions copied in, so
    scaffold's decompose degrades to its no-LLM fallback (no endpoint) and still writes a
    complete routine dir."""
    lib = tmp_path / "library"
    for kind in ("workflows", "traits", "permissions"):
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
    return SimpleNamespace(server=server, routine=routine, depth=depth)


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


def test_create_routine_rejected_outside_root_conversation(tmp_path):
    server = _server(tmp_path)
    for ctx in (_ctx(server, home="routines_home"),                     # a scheduled routine
                _ctx(server, home="conversations_home", depth=1)):      # a within-reply child
        obs = create_routine.handle_create_routine(
            ctx, {"kind": "create_routine", "target": "x", "name": "X", "prompt": "p"})
        assert obs["rejected"] and "conversation" in obs["reason"]
    assert not list((server.routines_home).glob("x"))                   # nothing created


def test_create_routine_from_conversation_materializes(tmp_path):
    server = _server(tmp_path)
    ctx = _ctx(server, home="conversations_home")
    obs = create_routine.handle_create_routine(
        ctx, {"kind": "create_routine", "target": "arxiv-reading-list",
              "name": "Arxiv reading list", "prompt": "collect new AI papers and keep a list",
              "workflow": "general-task"})
    assert obs.get("created") and obs["slug"] == "arxiv-reading-list"
    new_dir = server.routines_home / "arxiv-reading-list"
    assert new_dir.is_dir()
    assert (new_dir / "main.md").is_file()          # the decomposed workflow
    cfg = yaml.safe_load((new_dir / "routine.yaml").read_text(encoding="utf-8"))
    assert cfg["slug"] == "arxiv-reading-list" and cfg["name"] == "Arxiv reading list"


def test_create_routine_rejects_duplicate_slug(tmp_path):
    server = _server(tmp_path)
    ctx = _ctx(server, home="conversations_home")
    (server.routines_home / "taken").mkdir()
    obs = create_routine.handle_create_routine(
        ctx, {"kind": "create_routine", "target": "taken", "name": "Taken", "prompt": "p"})
    assert obs.get("already_exists") and not obs.get("created")
