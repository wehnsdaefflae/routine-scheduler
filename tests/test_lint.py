"""Workflow library lint + materialization + scaffold, against the real library-seed."""

from pathlib import Path

import pytest
import yaml

from rsched.config import ServerConfig, load_routine
from rsched.workflows.adapt import materialize
from rsched.workflows.lint import lint_all, lint_materialized_text, lint_workflow_py
from rsched.workflows.scaffold import scaffold

SEED = Path(__file__).resolve().parents[1] / "library-seed"
UTIL_SEED = Path(__file__).resolve().parents[1] / "util-seed"


def merged_library(tmp_path) -> Path:
    """A library-repo layout (workflows/ + traits/ + permissions/ + utils/) built from the repo seeds."""
    import shutil

    home = tmp_path / "libraries"
    shutil.copytree(SEED / "workflows", home / "workflows")
    shutil.copytree(SEED / "traits", home / "traits")
    shutil.copytree(SEED / "permissions", home / "permissions")
    shutil.copytree(UTIL_SEED / "utils", home / "utils")
    return home


def test_seed_library_is_clean():
    results = lint_all(SEED)
    assert results, "seed library found"
    problems = {k: v for k, v in results.items() if v}
    assert problems == {}, problems


def test_lint_catches_defects():
    traits = ["ask-policy"]
    bad = ('"""bad pattern"""\n'
           'META = {"name": "X", "slug": "mismatch", "description": "d", "when_to_use": "w",\n'
           '        "version": 1, "status": "wild", "includes": ["nope"], "tags": ["a", "b", "c"]}\n')
    problems = lint_workflow_py(bad, filename="bad.py", trait_slugs=traits)
    text = " | ".join(problems)
    for needle in ("filename does not match", "status must be", "does not resolve",
                   "no top-level main()", "PHASES", "COMPLETION"):
        assert needle in text, needle


def test_materialize_carries_workflow_and_provenance():
    import frontmatter

    # materialize = the un-decomposed baseline: the Python workflow rendered into main.md (the
    # orchestrator acts the pattern out; the pattern is fenced in the body).
    content, prov = materialize(SEED, "general-task")
    assert prov["slug"] == "general-task" and prov["version"] == 8
    meta, body = frontmatter.parse(content)
    assert meta["materialized_from"]["slug"] == "general-task" and meta["name"] == "General task"
    assert "## Run flow" in body and "## Completion criteria" in body
    assert "```python" in body and "def main():" in body          # the pattern is carried verbatim
    assert "## Standing practices" not in content and "# trait:" not in content
    assert lint_materialized_text(content) == []


def test_python_workflow_parse_and_lint():
    from rsched.workflows.lint import lint_workflow_py
    from rsched.workflows.pyworkflow import parse_py, render_markdown

    src = (SEED / "workflows" / "general-task.py").read_text()
    meta = parse_py(src)                                  # parsed statically — never executed
    assert meta["slug"] == "general-task" and meta["has_main"] and meta["format"] == "py"
    assert meta["phases"] == ["bootstrap", "steady", "wrap-up"] and meta["completion"]
    traits = ["ask-policy", "global-utils", "web-research", "ledger-discipline",
              "improve-bugfix", "improve-research", "improve-features", "improve-ui", "improve-efficiency"]
    assert lint_workflow_py(src, filename="general-task.py", trait_slugs=traits) == []
    # defects: no META / no run()
    probs = lint_workflow_py("x = 1\n", filename="paperbot.py", trait_slugs=[])
    assert any("META" in p for p in probs)
    # a syntax error is reported, not raised
    assert any("invalid Python" in p for p in lint_workflow_py("def (:\n", filename="x.py", trait_slugs=[]))
    # rendering carries the required routine sections
    md = render_markdown(src, meta)
    assert all(s in md for s in ("## Run flow", "## Phases", "## Completion criteria", "```python"))


def test_tags_on_library_elements():
    from rsched import library_docs, utils_lib
    from rsched.workflows.library import list_workflows

    wfs = {w["slug"]: w for w in list_workflows(SEED)}
    # General Task (user-facing) + the wizard's clarify-instruction (meta) + the
    # Conversations tab's converse pattern ship by default
    assert set(wfs) == {"general-task", "clarify-instruction", "converse"}
    assert "meta" not in wfs["general-task"]["tags"]      # not meta → stays user-facing
    assert "meta" in wfs["clarify-instruction"]["tags"]   # meta → filtered out of user suggestions
    # every library element carries at least three tags (the universal requirement)
    for w in wfs.values():
        assert len(w["tags"]) >= 3, (w["slug"], w["tags"])

    traits = {d["slug"]: d for d in library_docs.list_docs(SEED / "traits")}
    for d in traits.values():
        assert len(d["tags"]) >= 3, (d["slug"], d["tags"])
    assert set(traits["web-research"]["tags"]) >= {"web", "research"}
    perms = {d["slug"]: d for d in library_docs.list_docs(SEED / "permissions")}
    assert {"util-authoring", "memory", "communication", "self-modification",
            "run-history", "run-history-full", "shell"} <= set(perms)
    # a doc's frontmatter is stripped before its body is shown/inlined
    raw = (SEED / "traits" / "web-research.md").read_text()
    assert raw.startswith("---") and library_docs.doc_body(raw).lstrip().startswith("# trait:")

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
    for slug in ("self-audit", "workflow-curator", "routine-improver"):
        p = home / slug
        assert (p / "main.md").exists() and (p / ".git").is_dir()
        cfg = yaml.safe_load((p / "routine.yaml").read_text())
        assert cfg["enabled"] is False and "endpoints" not in cfg
    assert seed_routines(home) == 0                  # idempotent — never clobbers an install


def test_bootstrap_seeds_libraries(tmp_path):
    """seed_libraries populates an empty library repo (workflows/ + traits/ + permissions/ +
    utils/) from the built-in defaults + git-inits it."""
    from rsched.bootstrap import seed_libraries
    home = tmp_path / "libraries"
    seed_libraries(home)
    assert (home / "workflows").is_dir() and list((home / "workflows").glob("*.py"))  # Python patterns
    assert (home / "traits").is_dir() and list((home / "traits").glob("*.md"))
    assert (home / "permissions").is_dir() and list((home / "permissions").glob("*.md"))
    assert (home / "utils").is_dir() and any((home / "utils").iterdir())
    assert (home / ".git").is_dir()


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


def _py_workflow(tags: str) -> str:
    return ('"""x pattern"""\n'
            'META = {"name": "X", "slug": "x", "description": "d", "when_to_use": "w",\n'
            f'        "version": 1, "status": "draft", "tags": {tags}}}\n'
            'PHASES = ["steady"]\n'
            'COMPLETION = "done"\n'
            "def main():\n    pass\n")


def test_lint_requires_three_tags():
    from rsched.workflows.lint import lint_trait_text
    assert any("at least 3 tags" in p
               for p in lint_workflow_py(_py_workflow('["a", "b"]'), filename="x.py", trait_slugs=[]))
    assert not any("tags" in p
                   for p in lint_workflow_py(_py_workflow('["a", "b", "c"]'), filename="x.py", trait_slugs=[]))
    two_tag_trait = "---\ntags: [a, b]\n---\n# trait: x — y\n\nbody line one\nbody line two\n"
    assert any("at least 3 tags" in p for p in lint_trait_text(two_tag_trait, filename="x.md"))


def test_tag_suggestion_helpers(tmp_path):
    from rsched.config import ServerConfig
    from rsched.workflows.suggest import existing_tags, normalize_tags

    assert normalize_tags(["Web", "web", "Tool Use", "a", "b"]) == ["web", "tool-use", "a"]  # dedup, kebab, <=3
    assert normalize_tags([]) == []

    server = ServerConfig()
    server.libraries_home = merged_library(tmp_path)
    server.routines_home = tmp_path / "routines"         # no routines → vocab from library only
    vocab = existing_tags(server)
    assert vocab == sorted(set(vocab))                   # deduped + sorted
    for t in ("research", "web", "dev", "git"):          # spans workflows, traits, utils
        assert t in vocab, t


def test_suggest_candidate_filter_uses_meta_tag():
    from rsched.workflows.library import list_workflows
    from rsched.workflows.suggest import INTERNAL_TAG

    candidates = [w["slug"] for w in list_workflows(SEED)
                  if INTERNAL_TAG not in (w.get("tags") or []) and w["status"] == "stable"]
    assert candidates == ["general-task"]                 # the only shipped workflow, user-facing


def test_lint_rejects_non_list_tags():
    from rsched.workflows.lint import lint_trait_text

    assert any("tags must be a list" in p
               for p in lint_workflow_py(_py_workflow('"not-a-list"'), filename="x.py", trait_slugs=[]))
    bad_trait = "---\ntags: nope\n---\n# trait: x — y\n\nbody line one\nbody line two\n"
    assert any("tags must be a list" in p for p in lint_trait_text(bad_trait, filename="x.md"))


def test_scaffold_writes_and_loads_tags(tmp_path):
    server = ServerConfig()
    server.routines_home = tmp_path / "routines"
    server.routines_home.mkdir()
    server.libraries_home = SEED
    d = scaffold(server, slug="tagged", name="Tagged", instruction="x",
                 workflow_slug="general-task", tags=["meta", "custom"])
    cfg, problems = load_routine(d)
    assert problems == [] and cfg.tags == ["meta", "custom"]
    assert yaml.safe_load((d / "routine.yaml").read_text())["tags"] == ["meta", "custom"]


def test_scaffold_stamps_tools_allowlist(tmp_path):
    """A workflow META `tools:` allowlist lands in the routine's main.md frontmatter, where
    the engine reads and enforces it at run time (clarify-instruction is the shipped case)."""
    import frontmatter

    server = ServerConfig()
    server.routines_home = tmp_path / "routines"
    server.routines_home.mkdir()
    server.libraries_home = SEED
    d = scaffold(server, slug="clarify-sess", name="Clarify", instruction="x",
                 workflow_slug="clarify-instruction")
    meta = frontmatter.load(d / "main.md").metadata
    assert meta["tools"] == ["ask_user", "read_file", "write_file", "finish"]
    # general-task has no tools META → no allowlist is stamped (unrestricted)
    d2 = scaffold(server, slug="unrestricted", name="U", instruction="x",
                  workflow_slug="general-task")
    meta2 = frontmatter.load(d2 / "main.md").metadata
    assert "tools" not in meta2


def test_materialize_unknown_workflow(tmp_path):
    (tmp_path / "workflows").mkdir()
    with pytest.raises(FileNotFoundError):
        materialize(tmp_path, "no-such-flow")


def test_scaffold_creates_valid_routine(tmp_path):
    server = ServerConfig()
    server.routines_home = tmp_path / "routines"
    server.routines_home.mkdir()
    server.libraries_home = SEED
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
    assert raw["budgets"]["max_turns"] == 60
    # traits = the workflow's includes, adapted (here: copied — no generator endpoint) into
    # the routine's OWN traits/ and referenced from main.md's Standing practices tail.
    # improve-* passes are NOT among them — the routine-improver meta routine owns those.
    assert (d / "traits" / "web-research.md").exists()
    assert (d / "traits" / "global-utils.md").exists()
    assert not list((d / "traits").glob("improve-*.md"))
    main_text = (d / "main.md").read_text()
    assert "## Standing practices" in main_text and "traits/web-research.md" in main_text
    assert "improve-" not in main_text
    # permissions default in and are pure config (no local copies)
    assert set(cfg.permissions) == set(raw["permissions"])
    assert "util-authoring" in cfg.permissions and "self-modification" in cfg.permissions
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
    server.libraries_home = SEED
    # the wizard passes extra step modules; they land in the routine's steps/ (the LLM-decomposed
    # modules would too, but there's no generator endpoint in this test)
    d = scaffold(server, slug="split-routine", name="Split",
                 instruction="# Entry\n\nSteps in steps/.", workflow_slug="general-task",
                 steps={"discover": "# Discover step\n\nHow to discover.",
                        "compose.md": "# Compose step\n\nHow to compose."})
    assert (d / "steps" / "discover.md").read_text().startswith("# Discover step")
    assert (d / "steps" / "compose.md").read_text().startswith("# Compose step")


def test_dump_markdown_roundtrips_through_engine_parse():
    """What scaffold/adapt/runtime write is exactly what the engine parses back —
    nested provenance, key order, and a body containing its own '---' lines."""
    import frontmatter

    from rsched.workflows.adapt import dump_markdown

    meta = {"name": "N", "slug": "s",
            "materialized_from": {"slug": "wf", "commit": "abc123", "version": 3},
            "adapted": "2026-07-10", "modules": ["a-step", "b-step"],
            "tools": ["ask_user", "finish"]}
    body = "## Run flow\n1. x\n\n---\n\n## Completion criteria\n- done\n"
    text = dump_markdown(meta, body)
    meta2, body2 = frontmatter.parse(text)
    assert meta2 == meta and list(meta2) == list(meta)     # values AND key order survive
    assert body2 == body.strip()                           # later --- stays in the body
    assert text.endswith("\n") and not text.endswith("\n\n")
