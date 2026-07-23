"""workflows/generate.generate + workflows/suggest.suggest / suggest_traits_permissions:
the system-model completions are scripted — under test is everything AROUND the model
(prompt assembly, lint gating + one repair round, slug uniquing, reply validation against
the library, schema retries, and the no-endpoint fallbacks)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from rsched.config import ModelRef, ServerConfig
from rsched.endpoints.base import Completion

REPO = Path(__file__).resolve().parents[1]
SEED = REPO / "library-seed"


class _SysEndpoint:
    """Scripted system model: one queued reply per complete() call — a str rides
    Completion.text (parsed=None → the schema_guard path), a dict is a parsed schema
    reply, an Exception is raised."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls: list[dict] = []

    def complete(self, messages, **kw):
        self.calls.append({"messages": messages, **kw})
        if not self.replies:
            raise AssertionError("scripted system model ran out of replies")
        item = self.replies.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, dict):
            return Completion(text=json.dumps(item), parsed=item if kw.get("schema") else None,
                              usage={"in": 7, "out": 3})
        return Completion(text=str(item), usage={"in": 7, "out": 3})


def _patch_system_model(monkeypatch, module_path, endpoint):
    class _Reg:
        def __init__(self, server):
            pass

        def for_system(self):
            return endpoint, ModelRef(endpoint="scripted", model="sys", name="system")

    monkeypatch.setattr(f"{module_path}.EndpointRegistry", _Reg)


@pytest.fixture
def server(tmp_path):
    """Tmp homes with the REAL library-seed workflows/traits/permissions copied in."""
    lib = tmp_path / "library"
    for kind in ("workflows", "traits", "permissions"):
        shutil.copytree(SEED / kind, lib / kind, ignore=shutil.ignore_patterns("__pycache__"))
    s = ServerConfig()
    s.routines_home = tmp_path / "routines"
    s.routines_home.mkdir()
    s.libraries_home = lib
    return s


def _seed_pattern_text() -> str:
    return (SEED / "workflows" / "general-task.py").read_text(encoding="utf-8")


# ------------------------------------------------------------------- generate()


def test_generate_lints_writes_and_uniquifies_the_slug(server, monkeypatch):
    from rsched.workflows import generate as gen_mod

    # a known-lint-clean pattern as the draft, fenced — the fence must be stripped
    ep = _SysEndpoint(["```python\n" + _seed_pattern_text() + "\n```"])
    _patch_system_model(monkeypatch, "rsched.workflows.generate", ep)
    spent = []
    slug, note = gen_mod.generate(server, "Watch the arxiv feed for new grammar papers",
                                  hint="a poll-and-digest shape", on_usage=spent.append)
    assert note == ""
    assert slug == "general-task-2"                   # base slug taken by the seed → -2
    written = server.libraries_home / "workflows" / "general-task-2.py"
    assert written.exists() and "META" in written.read_text(encoding="utf-8")
    assert spent == [{"in": 7, "out": 3}]             # the draft call's spend hit on_usage
    prompt = ep.calls[0]["messages"][0]["content"]
    assert "Watch the arxiv feed" in prompt and "SHAPE HINT" in prompt


def test_generate_repairs_a_bad_draft_once(server, monkeypatch):
    from rsched.workflows import generate as gen_mod

    ep = _SysEndpoint(["this is not python at all", _seed_pattern_text()])
    _patch_system_model(monkeypatch, "rsched.workflows.generate", ep)
    spent = []
    slug, note = gen_mod.generate(server, "instruction", on_usage=spent.append)
    assert slug == "general-task-2" and note == ""
    assert len(ep.calls) == 2 and len(spent) == 2     # draft + exactly one repair round
    fix_prompt = ep.calls[1]["messages"][0]["content"]
    assert "failed lint" in fix_prompt and "this is not python at all" in fix_prompt


def test_generate_raises_when_lint_never_passes(server, monkeypatch):
    from rsched.workflows import generate as gen_mod

    ep = _SysEndpoint(["garbage one", "garbage two"])
    _patch_system_model(monkeypatch, "rsched.workflows.generate", ep)
    before = sorted(p.name for p in (server.libraries_home / "workflows").glob("*.py"))
    with pytest.raises(RuntimeError, match="failed lint twice"):
        gen_mod.generate(server, "instruction")
    after = sorted(p.name for p in (server.libraries_home / "workflows").glob("*.py"))
    assert after == before                            # nothing landed in the library


# -------------------------------------------------------------------- suggest()


def test_suggest_filters_unknown_slugs(server, monkeypatch):
    from rsched.workflows import suggest as sug_mod

    ep = _SysEndpoint([{
        "suggestions": [
            {"slug": "general-task", "confidence": 0.4, "reason": "generic fit"},
            {"slug": "ghost-flow", "confidence": 0.99, "reason": "hallucinated"},
        ],
        "none_fit": False,
    }])
    _patch_system_model(monkeypatch, "rsched.workflows.suggest", ep)
    result = sug_mod.suggest(server, "summarize my inbox daily")
    assert [s["slug"] for s in result["suggestions"]] == ["general-task"]   # ghost dropped
    assert result["none_fit"] is False
    listing = ep.calls[0]["messages"][0]["content"]
    assert "slug: general-task" in listing
    assert "slug: converse" in listing                # meta workflows are listed like any other tag now (D15)


def test_suggest_retries_once_on_a_malformed_reply(server, monkeypatch):
    from rsched.workflows import suggest as sug_mod

    ep = _SysEndpoint(["not json at all",
                       {"suggestions": [{"slug": "general-task", "confidence": 0.8,
                                         "reason": "fits"}], "none_fit": False}])
    _patch_system_model(monkeypatch, "rsched.workflows.suggest", ep)
    result = sug_mod.suggest(server, "task")
    assert [s["slug"] for s in result["suggestions"]] == ["general-task"]
    assert len(ep.calls) == 2
    retry_msgs = ep.calls[1]["messages"]
    assert retry_msgs[-1]["content"].startswith("Invalid:")
    assert retry_msgs[-2]["role"] == "assistant"      # the bad reply rides the retry context


def test_suggest_falls_back_when_replies_stay_malformed(server, monkeypatch):
    from rsched.workflows import suggest as sug_mod

    ep = _SysEndpoint(["nope", "still nope"])
    _patch_system_model(monkeypatch, "rsched.workflows.suggest", ep)
    result = sug_mod.suggest(server, "task")
    assert result["suggestions"] == [] and result["none_fit"] is True
    assert "malformed" in result["new_workflow_hint"]


def test_suggest_empty_library_short_circuits(tmp_path, monkeypatch):
    from rsched.workflows import suggest as sug_mod

    s = ServerConfig()
    s.libraries_home = tmp_path / "empty-lib"
    (s.libraries_home / "workflows").mkdir(parents=True)
    ep = _SysEndpoint([])
    _patch_system_model(monkeypatch, "rsched.workflows.suggest", ep)
    result = sug_mod.suggest(s, "task")
    assert result["none_fit"] is True and "no workflows" in result["new_workflow_hint"]
    assert ep.calls == []                             # no model call without candidates


# ---------------------------------------------------- suggest_traits_permissions()


def test_suggest_traits_permissions_validates_against_the_library(server, monkeypatch):
    from rsched.workflows import suggest as sug_mod

    ep = _SysEndpoint([{"traits": ["ask-policy", "made-up-trait"],
                        "permissions": ["memory", "cosmic-powers"],
                        "deliberation": "deliberate"}])
    _patch_system_model(monkeypatch, "rsched.workflows.suggest", ep)
    out = sug_mod.suggest_traits_permissions(server, "watch a git repo",
                                             workflow_slug="general-task")
    assert out == {"traits": ["ask-policy"], "permissions": ["memory"],
                   "deliberation": "deliberate"}   # unknowns dropped, level passes
    prompt = ep.calls[0]["messages"][0]["content"]
    assert "CHOSEN WORKFLOW: general-task" in prompt  # the picked pattern informs the pick


def test_suggest_traits_permissions_falls_back_when_no_endpoint_answers(server, monkeypatch):
    from rsched.config import DEFAULT_PERMISSIONS, DEFAULT_TRAITS
    from rsched.workflows import suggest as sug_mod

    ep = _SysEndpoint([RuntimeError("endpoint down")])
    _patch_system_model(monkeypatch, "rsched.workflows.suggest", ep)
    out = sug_mod.suggest_traits_permissions(server, "anything")
    # the defaults, validated against the seeded library (all present there)
    assert out["traits"] == list(DEFAULT_TRAITS)
    assert out["permissions"] == list(DEFAULT_PERMISSIONS)


def test_suggest_traits_permissions_empty_library_never_calls_the_model(tmp_path, monkeypatch):
    from rsched.workflows import suggest as sug_mod

    s = ServerConfig()
    s.libraries_home = tmp_path / "empty-lib"
    (s.libraries_home / "traits").mkdir(parents=True)
    (s.libraries_home / "permissions").mkdir(parents=True)
    ep = _SysEndpoint([])
    _patch_system_model(monkeypatch, "rsched.workflows.suggest", ep)
    assert sug_mod.suggest_traits_permissions(s, "x") == {
        "traits": [], "permissions": [], "deliberation": "standard"}
    assert ep.calls == []
