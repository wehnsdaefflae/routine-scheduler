"""recompile_routine: re-derive main.md + the decompose-generated steps/ from the seed, while
preserving the routine's own traits + hand-added step modules, re-stamping the drift baseline, and
refusing to flatten a routine whose steps vanished because the model call failed."""

import types
from pathlib import Path

import frontmatter
import pytest

from rsched.config import ModelRef, ServerConfig
from rsched.endpoints.base import Completion
from rsched.workflows import provenance
from rsched.workflows.adapt import dump_markdown
from rsched.workflows.recompile import recompile_routine

SEED = Path(__file__).resolve().parents[1] / "library-seed"


def _server(tmp_path):
    server = ServerConfig()
    server.routines_home = tmp_path
    server.libraries_home = SEED
    return server


def _routine(tmp_path, *, slug="rc", workflow="general-task", instruction="Collect papers.",
             modules=("old-a", "old-b"), extra_steps=("hand-extra",), traits=("web-research",)) -> Path:
    d = tmp_path / slug
    (d / "steps").mkdir(parents=True)
    (d / "traits").mkdir()
    (d / "instruction.md").write_text(instruction, encoding="utf-8")
    for m in modules:
        (d / "steps" / f"{m}.md").write_text(f"old body of {m}", encoding="utf-8")
    for e in extra_steps:
        (d / "steps" / f"{e}.md").write_text("a hand-authored module, not from decompose", encoding="utf-8")
    for t in traits:
        (d / "traits" / f"{t}.md").write_text(
            f"# trait: {t} — original summary\noriginal practice body\n", encoding="utf-8")
    meta = {"name": "RC", "slug": slug, "modules": sorted(modules),
            "materialized_from": {"slug": workflow, "commit": "old", "version": 0}}
    (d / "main.md").write_text(dump_markdown(meta, "old entry state machine"), encoding="utf-8")
    return d


class _FakeEndpoint:
    def __init__(self, modules, traits=None):
        self.modules, self.traits = modules, traits or []

    def complete(self, messages, *, model, schema=None, effort=None, timeout=600, **kw):
        return Completion(text="", parsed={
            "main": "NEW entry state machine\n\nroutes to the new modules",
            "modules": [{"name": n, "body": f"NEW body of {n}"} for n in self.modules],
            "traits": self.traits})


def _wire(monkeypatch, endpoint):
    import rsched.endpoints as endpoints_mod
    monkeypatch.setattr(endpoints_mod.EndpointRegistry, "for_system",
                        lambda self: (endpoint, ModelRef(endpoint="x", model="m")))


def _cfg(slug="rc", workflow="general-task"):
    return types.SimpleNamespace(workflow_slug=workflow, name="RC", slug=slug)


def test_recompile_replaces_modules_preserves_traits_and_extras(monkeypatch, tmp_path):
    server = _server(tmp_path)
    d = _routine(tmp_path)
    _wire(monkeypatch, _FakeEndpoint(["new-a", "new-b"],
                                     traits=[{"slug": "web-research", "body": "# trait: web-research — REWRITTEN\nx"}]))

    result = recompile_routine(server, d, _cfg())

    assert set(result["modules"]) == {"new-a", "new-b"}
    assert set(result["removed"]) == {"old-a", "old-b"}
    # the previously generated modules are gone, the new ones are written…
    assert not (d / "steps" / "old-a.md").exists()
    assert (d / "steps" / "new-a.md").read_text().startswith("NEW body")
    # …the hand-authored step module is preserved…
    assert (d / "steps" / "hand-extra.md").exists()
    # …traits are the routine's OWN — never re-adapted, even though the model returned a rewrite…
    assert "REWRITTEN" not in (d / "traits" / "web-research.md").read_text()
    assert "original practice body" in (d / "traits" / "web-research.md").read_text()
    # …main.md's module list is refreshed and the Standing-practices tail references the trait…
    meta, body = frontmatter.parse((d / "main.md").read_text(encoding="utf-8"))
    assert meta["modules"] == ["new-a", "new-b"]
    assert "traits/web-research.md" in body
    assert meta["materialized_from"]["slug"] == "general-task"


def test_recompile_clears_drift(monkeypatch, tmp_path):
    server = _server(tmp_path)
    d = _routine(tmp_path)
    _wire(monkeypatch, _FakeEndpoint(["new-a"]))
    recompile_routine(server, d, _cfg())
    assert provenance.drift(d, "Collect papers.") == {"tracked": True, "instruction": False, "steps": False}


def test_degraded_decompose_does_not_flatten_existing_steps(tmp_path):
    # no system endpoint wired → decompose falls back to modules={}; because the routine HAD real
    # modules, recompile refuses rather than wiping them to a single main.md
    server = _server(tmp_path)
    d = _routine(tmp_path)
    with pytest.raises(RuntimeError):
        recompile_routine(server, d, _cfg())
    assert (d / "steps" / "old-a.md").exists()   # untouched


def test_missing_workflow_raises_valueerror(tmp_path):
    server = _server(tmp_path)
    d = _routine(tmp_path, workflow="does-not-exist")
    with pytest.raises(ValueError):
        recompile_routine(server, d, _cfg(workflow="does-not-exist"))


def test_hand_authored_routine_has_nothing_to_recompile(tmp_path):
    server = _server(tmp_path)
    d = _routine(tmp_path, workflow="")
    with pytest.raises(ValueError):
        recompile_routine(server, d, _cfg(workflow=""))
