"""Workflow library lint + materialization + scaffold, against the real library-seed."""

from pathlib import Path

import pytest
import yaml

from rsched.config import ServerConfig, load_routine
from rsched.workflows.adapt import materialize
from rsched.workflows.lint import (lint_all, lint_materialized_text, lint_workflow_text)
from rsched.workflows.scaffold import scaffold

SEED = Path(__file__).resolve().parents[1] / "library-seed"


def test_seed_library_is_clean():
    results = lint_all(SEED)
    assert results, "seed library found"
    problems = {k: v for k, v in results.items() if v}
    assert problems == {}, problems


def test_lint_catches_defects():
    frags = ["ask-policy"]
    bad = """---
name: X
slug: mismatch
description: d
when_to_use: w
version: 1
status: wild
includes: [nope]
params: []
---
## Run flow
1. do {{undeclared}} things
"""
    problems = lint_workflow_text(bad, filename="bad.md", fragment_slugs=frags)
    text = " | ".join(problems)
    for needle in ("filename does not match", "status must be", "does not resolve",
                   "## Phases", "## Completion criteria", "undeclared"):
        assert needle in text, needle
    assert lint_workflow_text("no frontmatter at all", filename="x.md", fragment_slugs=[])


def test_materialize_carries_workflow_and_provenance():
    from rsched import frontmatter

    # materialize = the un-decomposed baseline: the whole single-file workflow becomes main.md
    content, prov = materialize(SEED, "general-task")
    assert prov["slug"] == "general-task" and prov["version"] == 5
    meta, body = frontmatter.parse(content)
    assert meta["materialized_from"]["slug"] == "general-task" and meta["name"] == "General task"
    assert "## Run flow" in body and "## Completion criteria" in body
    assert "## Standard practices" not in content and "### fragment:" not in content
    assert lint_materialized_text(content) == []


def test_tags_on_library_elements():
    from rsched import fragments_lib, utils_lib
    from rsched.workflows.library import list_workflows

    wfs = {w["slug"]: w for w in list_workflows(SEED)}
    # General Task (user-facing) + the wizard's own clarify-instruction (meta) ship by default
    assert set(wfs) == {"general-task", "clarify-instruction"}
    assert "meta" not in wfs["general-task"]["tags"]      # not meta → stays user-facing
    assert "meta" in wfs["clarify-instruction"]["tags"]   # meta → filtered out of user suggestions
    # every library element carries at least three tags (the universal requirement)
    for w in wfs.values():
        assert len(w["tags"]) >= 3, (w["slug"], w["tags"])

    frags = {f["slug"]: f for f in fragments_lib.list_fragments(SEED / "fragments")}
    for f in frags.values():
        assert len(f["tags"]) >= 3, (f["slug"], f["tags"])
    assert set(frags["web-research"]["tags"]) >= {"web", "research"}
    # a fragment's frontmatter is stripped before its body is inlined into a prompt
    raw = (SEED / "fragments" / "web-research.md").read_text()
    assert raw.startswith("---") and fragments_lib.fragment_body(raw).lstrip().startswith("# fragment:")

    utils = {u["name"]: u for u in utils_lib.list_utils(SEED.parent / "util-seed")}
    for u in utils.values():
        assert len(u["tags"]) >= 3, (u["name"], u["tags"])
    assert utils["pytest-run"]["tags"] == ["dev", "testing", "code"]
    assert utils["websearch"]["tags"] == ["web", "research", "search"]


def test_bootstrap_generates_config_with_token(tmp_path, monkeypatch):
    """Fresh deploy must never serve an open API: ensure_config writes a real token."""
    import yaml
    cfg = tmp_path / "config.yaml"
    monkeypatch.setattr("rsched.bootstrap.config_file", lambda: cfg)
    from rsched.bootstrap import ensure_config
    assert ensure_config() is True and cfg.exists()
    token = yaml.safe_load(cfg.read_text())["token"]
    assert token and token not in ("", "change-me")
    assert ensure_config() is False                 # idempotent — no-op once present


def test_bootstrap_seeds_meta_routines(tmp_path):
    """Fresh install installs the bundled meta routines — disabled, generic (no hardcoded endpoints)."""
    import yaml
    from rsched.bootstrap import seed_routines
    home = tmp_path / "routines"
    assert seed_routines(home) >= 1
    for slug in ("self-audit", "library-sync", "meta-workflows"):
        p = home / slug
        assert (p / "main.md").exists() and (p / ".git").is_dir()
        cfg = yaml.safe_load((p / "routine.yaml").read_text())
        assert cfg["enabled"] is False and "endpoints" not in cfg
    assert seed_routines(home) == 0                  # idempotent — never clobbers an install


def test_bootstrap_seeds_libraries(tmp_path):
    """seed_library populates an empty library from the built-in defaults + git-inits it."""
    from rsched.bootstrap import seed_library
    wf = tmp_path / "wf"
    seed_library("workflows", wf)
    assert (wf / "workflows").is_dir() and list((wf / "workflows").glob("*.md"))
    assert (wf / ".git").is_dir()
    # fragments live in their OWN repo — the workflow library must not carry a stray copy
    assert not (wf / "fragments").exists()
    ut = tmp_path / "ut"
    seed_library("utils", ut)
    assert (ut / "utils").is_dir() and any((ut / "utils").iterdir())


def test_util_declares_secrets(tmp_path):
    """A util's `secrets:` header line is parsed → the UI can tell users which vars to set."""
    from rsched import utils_lib
    d = tmp_path / "utils" / "foo"
    d.mkdir(parents=True)
    (d / "main.py").write_text(
        '"""foo — does foo.\n\nusage: gu foo\nsecrets: FOO_TOKEN, FOO_USER\ntags: a, b, c\n"""\n')
    u = utils_lib.list_utils(tmp_path)[0]
    assert u["secrets"] == ["FOO_TOKEN", "FOO_USER"] and u["tags"] == ["a", "b", "c"]
    # a util with no secrets (or "(none)") declares none
    (d / "main.py").write_text('"""foo — x.\n\nusage: gu foo\nsecrets: (none)\n"""\n')
    assert utils_lib.list_utils(tmp_path)[0]["secrets"] == []


def test_lint_requires_three_tags():
    from rsched.workflows.lint import lint_fragment_text, lint_workflow_text
    two_tag_wf = ("---\nname: X\nslug: x\ndescription: d\nwhen_to_use: w\nversion: 1\n"
                  "status: draft\ntags: [a, b]\n---\n## Run flow\n## Phases\n## Completion criteria\n")
    assert any("at least 3 tags" in p for p in lint_workflow_text(two_tag_wf, filename="x.md", fragment_slugs=[]))
    three_tag_wf = two_tag_wf.replace("[a, b]", "[a, b, c]")
    assert not any("tags" in p for p in lint_workflow_text(three_tag_wf, filename="x.md", fragment_slugs=[]))
    two_tag_frag = "---\ntags: [a, b]\n---\n# fragment: x — y\n\nbody line one\nbody line two\n"
    assert any("at least 3 tags" in p for p in lint_fragment_text(two_tag_frag, filename="x.md"))


def test_tag_suggestion_helpers(tmp_path):
    from rsched.config import ServerConfig
    from rsched.workflows.suggest import existing_tags, normalize_tags

    assert normalize_tags(["Web", "web", "Tool Use", "a", "b"]) == ["web", "tool-use", "a"]  # dedup, kebab, <=3
    assert normalize_tags([]) == []

    server = ServerConfig()
    server.library_home = SEED
    server.fragments_home = SEED / "fragments"
    server.utils_home = SEED.parent / "util-seed"
    server.routines_home = tmp_path                      # no routines → vocab from library only
    vocab = existing_tags(server)
    assert vocab == sorted(set(vocab))                   # deduped + sorted
    for t in ("research", "web", "dev", "git"):          # spans workflows, fragments, utils
        assert t in vocab, t


def test_suggest_candidate_filter_uses_meta_tag():
    from rsched.workflows.library import list_workflows
    from rsched.workflows.suggest import INTERNAL_TAG

    candidates = [w["slug"] for w in list_workflows(SEED)
                  if INTERNAL_TAG not in (w.get("tags") or []) and w["status"] == "stable"]
    assert candidates == ["general-task"]                 # the only shipped workflow, user-facing


def test_lint_rejects_non_list_tags():
    from rsched.workflows.lint import lint_fragment_text

    bad_wf = ("---\nname: X\nslug: x\ndescription: d\nwhen_to_use: w\nversion: 1\n"
              "status: draft\ntags: not-a-list\n---\n## Run flow\n## Phases\n## Completion criteria\n")
    assert any("tags must be a list" in p for p in lint_workflow_text(bad_wf, filename="x.md", fragment_slugs=[]))
    bad_frag = "---\ntags: nope\n---\n# fragment: x — y\n\nbody line one\nbody line two\n"
    assert any("tags must be a list" in p for p in lint_fragment_text(bad_frag, filename="x.md"))


def test_scaffold_writes_and_loads_tags(tmp_path):
    server = ServerConfig()
    server.routines_home = tmp_path / "routines"
    server.routines_home.mkdir()
    server.library_home = SEED
    server.fragments_home = SEED / "fragments"
    d = scaffold(server, slug="tagged", name="Tagged", instruction="x",
                 workflow_slug="general-task", tags=["meta", "custom"])
    cfg, problems = load_routine(d)
    assert problems == [] and cfg.tags == ["meta", "custom"]
    assert yaml.safe_load((d / "routine.yaml").read_text())["tags"] == ["meta", "custom"]


def test_materialize_missing_param(tmp_path):
    import shutil

    home = tmp_path
    shutil.copytree(SEED / "workflows", home / "workflows")
    shutil.copytree(SEED / "fragments", home / "fragments")
    wf = home / "workflows" / "general-task.md"                     # a workflow is a single file
    text = wf.read_text().replace("params: []", "params: [deliverable]")
    wf.write_text(text.replace("## Run flow", "## Run flow\nDeliver {{deliverable}}."))
    with pytest.raises(KeyError):
        materialize(home, "general-task")
    content, _ = materialize(home, "general-task", params={"deliverable": "a weekly report"})
    assert "a weekly report" in content and "{{deliverable}}" not in content


def test_scaffold_creates_valid_routine(tmp_path):
    server = ServerConfig()
    server.routines_home = tmp_path / "routines"
    server.routines_home.mkdir()
    server.library_home = SEED
    server.fragments_home = SEED / "fragments"
    d = scaffold(server, slug="papers-radar", name="Papers radar",
                 instruction="# Instruction\n\nCollect papers.",
                 workflow_slug="general-task", cron="0 8 * * 1")
    cfg, problems = load_routine(d)
    assert cfg is not None and problems == [], problems
    assert cfg.cron == "0 8 * * 1" and cfg.workflow_slug == "general-task"
    assert (d / ".git").is_dir()
    assert (d / ".git" / "hooks" / "post-commit").stat().st_mode & 0o111
    # the workflow is materialized into the routine's OWN main.md — self-contained (no library
    # at run time). Without a generator endpoint, decompose falls back to the whole workflow.
    assert (d / "main.md").exists()
    raw = yaml.safe_load((d / "routine.yaml").read_text())
    assert raw["budgets"]["max_turns"] == 60 and "self" not in raw
    # active fragments = the workflow's includes, materialized as editable routine files
    assert set(cfg.fragments) == set(raw["fragments"])
    assert "self-audit" in cfg.fragments and "global-utils" in cfg.fragments
    assert (d / "fragments" / "self-audit.md").exists()
    assert (d / ".gitignore").read_text().startswith("runs/")
    with pytest.raises(ValueError):
        scaffold(server, slug="papers-radar", name="dup", instruction="x",
                 workflow_slug="general-task")
    with pytest.raises(ValueError):
        scaffold(server, slug="Bad Slug", name="x", instruction="x",
                 workflow_slug="general-task")


def test_scaffold_writes_step_modules(tmp_path):
    server = ServerConfig()
    server.routines_home = tmp_path / "routines"
    server.routines_home.mkdir()
    server.library_home = SEED
    server.fragments_home = SEED / "fragments"
    # the wizard passes extra step modules; they land in the routine's steps/ (the LLM-decomposed
    # modules would too, but there's no generator endpoint in this test)
    d = scaffold(server, slug="split-routine", name="Split",
                 instruction="# Entry\n\nSteps in steps/.", workflow_slug="general-task",
                 steps={"discover": "# Discover step\n\nHow to discover.",
                        "compose.md": "# Compose step\n\nHow to compose."})
    assert (d / "steps" / "discover.md").read_text().startswith("# Discover step")
    assert (d / "steps" / "compose.md").read_text().startswith("# Compose step")
