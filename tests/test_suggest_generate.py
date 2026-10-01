"""workflows/generate.generate + workflows/suggest.suggest:
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
    for kind in ("workflows", "rules", "permissions"):
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


def test_a_natively_parsed_reply_is_held_to_the_schema_too(server, monkeypatch):
    """A provider's native schema mode (Anthropic tool use) hands back `parsed` without the
    schema enforced; trusting it unchecked let a suggestion without `confidence` raise out of
    suggest() — a 500 in the form a person is waiting on — instead of costing one retry."""
    from rsched.workflows import suggest as sug_mod

    ep = _SysEndpoint([{"suggestions": [{"slug": "general-task"}], "none_fit": False},
                       {"suggestions": [{"slug": "general-task", "confidence": 0.7,
                                         "reason": "fits"}], "none_fit": False}])
    _patch_system_model(monkeypatch, "rsched.workflows.suggest", ep)
    result = sug_mod.suggest(server, "task")
    assert [s["slug"] for s in result["suggestions"]] == ["general-task"]
    assert len(ep.calls) == 2
    retry = ep.calls[1]["messages"]
    assert retry[-2]["role"] == "assistant" and retry[-2]["content"]   # never an empty turn
    assert retry[-1]["content"].startswith("Invalid:")


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
# ---------------------------------------------------- generate_description()


def test_generate_description_returns_the_generated_text(server, monkeypatch):
    ep = _SysEndpoint([{"description": "Polls the arxiv grammar feed each run and publishes a "
                        "deduped digest; needs the websearch util; writes digest.html."}])
    _patch_system_model(monkeypatch, "rsched.workflows.suggest", ep)
    from rsched.workflows import suggest as sug_mod

    out = sug_mod.generate_description(
        server, name="Arxiv Digest",
        instruction="Watch the arxiv grammar feed and publish a digest")
    assert out.startswith("Polls the arxiv grammar feed")
    prompt = ep.calls[0]["messages"][0]["content"]
    assert "PURPOSE" in prompt and "DEPENDENCIES WITH OTHER ROUTINES" in prompt
    assert "Watch the arxiv grammar feed" in prompt      # the task rides the prompt


def test_generate_description_falls_back_to_name_when_no_endpoint(server, monkeypatch):
    ep = _SysEndpoint([RuntimeError("no endpoint")])
    _patch_system_model(monkeypatch, "rsched.workflows.suggest", ep)
    from rsched.workflows import suggest as sug_mod

    assert sug_mod.generate_description(server, name="Fallback Name",
                                        instruction="do a thing") == "Fallback Name"


def test_generate_description_empty_task_never_calls_the_model(server, monkeypatch):
    ep = _SysEndpoint([])            # would raise "ran out of replies" if the model were called
    _patch_system_model(monkeypatch, "rsched.workflows.suggest", ep)
    from rsched.workflows import suggest as sug_mod

    assert sug_mod.generate_description(server, name="Just A Name", instruction="  ") == "Just A Name"
    assert ep.calls == []


def test_generate_description_survives_a_routine_yaml_that_is_no_mapping(server, monkeypatch):
    """The sibling catalog reads every routine.yaml; one that parses to a list made `.get`
    raise out of a generator whose contract is to never fail the creation flow."""
    for slug, text in (("listy", "- a\n- b\n"), ("named", "name: Named one\ntags: [x]\n")):
        (server.routines_home / slug).mkdir()
        (server.routines_home / slug / "routine.yaml").write_text(text, encoding="utf-8")
    ep = _SysEndpoint([{"description": "It watches things."}])
    _patch_system_model(monkeypatch, "rsched.workflows.suggest", ep)
    from rsched.workflows import suggest as sug_mod

    assert sug_mod.generate_description(server, name="N", instruction="watch") == (
        "It watches things.")
    prompt = ep.calls[0]["messages"][0]["content"]
    assert "- named: Named one [x]" in prompt and "listy" not in prompt
