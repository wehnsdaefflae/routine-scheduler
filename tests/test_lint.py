"""Workflow library lint + materialization + scaffold, against the real library-seed."""

from pathlib import Path

import pytest
import yaml

from rsched.config import ServerConfig, load_routine
from rsched.workflows.adapt import materialize
from rsched.workflows.lint import lint_all, lint_workflow_py
from rsched.workflows.scaffold import scaffold

SEED = Path(__file__).resolve().parents[1] / "library-seed"
UTIL_SEED = Path(__file__).resolve().parents[1] / "util-seed"


def merged_library(tmp_path) -> Path:
    """A library-repo layout (workflows/ + rules/ + permissions/ + utils/) built from the repo seeds."""
    import shutil

    home = tmp_path / "libraries"
    shutil.copytree(SEED / "workflows", home / "workflows")
    shutil.copytree(SEED / "rules", home / "rules")
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
           '        "version": 1, "includes": ["nope"], "tags": ["a", "b", "c"]}\n')
    problems = lint_workflow_py(bad, filename="bad.py", rule_slugs=traits)
    text = " | ".join(problems)
    for needle in ("filename does not match", "does not resolve",
                   "no top-level main()", "PHASES", "COMPLETION"):
        assert needle in text, needle


def test_materialize_carries_workflow_and_provenance():
    import frontmatter

    # materialize = the un-decomposed baseline: the Python workflow rendered into main.md (the
    # orchestrator acts the pattern out; the pattern is fenced in the body).
    content, prov = materialize(SEED, "general-task")
    assert prov["slug"] == "general-task" and prov["version"] == 9
    meta, body = frontmatter.parse(content)
    assert meta["materialized_from"]["slug"] == "general-task" and meta["name"] == "General task"
    assert "## Run flow" in body and "## Completion criteria" in body
    assert "```python" in body and "def main():" in body          # the pattern is carried verbatim
    assert "## Standing practices" not in content and "# trait:" not in content


def test_python_workflow_parse_and_lint():
    from rsched.workflows.lint import lint_workflow_py
    from rsched.workflows.pyworkflow import parse_py, render_markdown

    src = (SEED / "workflows" / "general-task.py").read_text()
    meta = parse_py(src)                                  # parsed statically — never executed
    assert meta["slug"] == "general-task" and meta["has_main"] and meta["format"] == "py"
    assert meta["phases"] == ["bootstrap", "steady", "wrap-up"] and meta["completion"]
    rules = ["ask-policy", "web-research", "decision-record", "intent-inference"]
    assert lint_workflow_py(src, filename="general-task.py", rule_slugs=rules) == []
    # defects: no META / no run()
    probs = lint_workflow_py("x = 1\n", filename="paperbot.py", rule_slugs=[])
    assert any("META" in p for p in probs)
    # a syntax error is reported, not raised
    assert any("invalid Python" in p for p in lint_workflow_py("def (:\n", filename="x.py", rule_slugs=[]))
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

    rules = {d["slug"]: d for d in library_docs.list_docs(SEED / "rules")}
    for d in rules.values():
        assert len(d["tags"]) >= 3, (d["slug"], d["tags"])
    assert set(rules["web-research"]["tags"]) >= {"web", "research"}
    perms = {d["slug"]: d for d in library_docs.list_docs(SEED / "permissions")}
    assert set(perms) == {"util-authoring", "util-revision", "util-removal",
                          "memory", "messaging-discord",
                          "run-history", "shell", "workflow-generation", "background-tasks",
                          "scheduling", "global-utils", "rule-authoring",
                          "remote-machines", "darknet", "outbound-mail",
                          "messaging-signal", "messaging-telegram", "messaging-whatsapp",
                          "messaging-zulip", "usenet", "scripts",
                          "recipe-authoring"}  # variants collapsed: level = capability
    # `self-modification` was retired when own-recipe writes became a fixed engine rule; 0.261.0
    # brought the DECISION back as `recipe-authoring`, because keying it on an fs write root
    # meant granting a working directory silently granted the right to reword the task.
    assert "self-modification" not in perms
    # Each act gets its own permission: writing a util adds a capability, removing one takes
    # it away from every caller, and each messenger reaches a different person differently.
    assert perms["util-authoring"]["requires"]["actions"] == ["write_util"]
    assert perms["util-revision"]["requires"]["actions"] == ["revise_util"]
    assert perms["util-removal"]["requires"]["actions"] == ["remove_util"]
    for channel in ("signal", "telegram", "whatsapp", "zulip"):
        doc = perms[f"messaging-{channel}"]
        assert doc["requires"]["utils"] == [channel], channel
        # no util_tags wildcard: the retired bundle's [chat, messaging] also swept in
        # discord and ntfy, so a "Signal only" grant was not expressible
        assert not doc["requires"].get("util_tags"), channel
    # a doc's frontmatter is stripped before its body is shown/inlined
    raw = (SEED / "rules" / "web-research.md").read_text()
    assert raw.startswith("---") and library_docs.doc_body(raw).lstrip().startswith("# rule:")

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


def test_bootstrap_seeds_libraries(tmp_path):
    """seed_libraries populates an empty library repo (workflows/ + rules/ + permissions/ +
    utils/) from the built-in defaults + git-inits it."""
    from rsched.bootstrap import seed_libraries
    home = tmp_path / "libraries"
    seed_libraries(home)
    assert (home / "workflows").is_dir() and list((home / "workflows").glob("*.py"))  # Python patterns
    assert (home / "rules").is_dir() and list((home / "rules").glob("*.md"))
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
            f'        "version": 1, "tags": {tags}}}\n'
            'PHASES = ["steady"]\n'
            'COMPLETION = "done"\n'
            "def main():\n    pass\n")


def test_lint_requires_three_tags():
    from rsched.workflows.lint import lint_rule_text
    assert any("at least 3 tags" in p
               for p in lint_workflow_py(_py_workflow('["a", "b"]'), filename="x.py", rule_slugs=[]))
    assert not any("tags" in p
                   for p in lint_workflow_py(_py_workflow('["a", "b", "c"]'), filename="x.py", rule_slugs=[]))
    two_tag_trait = "---\ntags: [a, b]\n---\n# rule: x — y\n\nbody line one\nbody line two\n"
    assert any("at least 3 tags" in p for p in lint_rule_text(two_tag_trait, filename="x.md"))


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


def test_lint_rejects_non_list_tags():
    from rsched.workflows.lint import lint_rule_text

    assert any("tags must be a list" in p
               for p in lint_workflow_py(_py_workflow('"not-a-list"'), filename="x.py", rule_slugs=[]))
    bad_trait = "---\ntags: nope\n---\n# rule: x — y\n\nbody line one\nbody line two\n"
    assert any("tags must be a list" in p for p in lint_rule_text(bad_trait, filename="x.md"))


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


def test_scaffold_degrade_names_the_cause_and_logs_a_health_event(tmp_path):
    """F197: a degraded build (here: no endpoint configured at all) must write WHY into the
    LEDGER ⚠ block and append a wizard_build_degraded health event — the 2026-07-24 credit
    outage was only diagnosable through the daemon journal, which audits cannot read."""
    import json as _json

    server = ServerConfig()
    server.routines_home = tmp_path / "routines"
    server.routines_home.mkdir()
    server.libraries_home = SEED
    d = scaffold(server, slug="born-degraded", name="Degraded", instruction="x",
                 workflow_slug="general-task")
    ledger = (d / "LEDGER.md").read_text(encoding="utf-8")
    assert "scaffolded without generated stages" in ledger
    assert "fully functional" in ledger           # a degraded build still runs — not a hard fail
    assert "Cause: " in ledger                    # the ⚠ block still names the failure
    stream = (server.routines_home / ".control" / "health-events.jsonl")\
        .read_text(encoding="utf-8").splitlines()
    ev = _json.loads(stream[-1])
    assert ev["event"] == "wizard_build_degraded"
    assert ev["routine"] == "born-degraded" and ev["detail"]


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
    cfg, problems = load_routine(d, libraries_home=server.libraries_home)
    assert cfg is not None and problems == [], problems
    assert cfg.cron == "0 8 * * 1" and cfg.workflow_slug == "general-task"
    assert (d / ".git").is_dir()
    assert (d / ".git" / "hooks" / "post-commit").stat().st_mode & 0o111
    # the workflow is materialized into the routine's OWN main.md — self-contained (no library
    # at run time). Without a generator endpoint, decompose falls back to the whole workflow.
    assert (d / "main.md").exists()
    raw = yaml.safe_load((d / "routine.yaml").read_text())
    assert raw["budgets"]["max_turns"] == 60
    # rules = the workflow's includes, recorded as SLUGS and referenced from main.md's
    # Standing practices tail. Nothing is copied into the routine dir: one library copy is the
    # whole point, so a revision reaches every holder. Since 0.263.0 a new routine ADOPTS a
    # settings template, so its own file records only the differences — the EFFECTIVE set is
    # what the run sees, and what has to carry the workflow's includes.
    assert set(cfg.rules) >= {"web-research", "decision-record"}
    assert not (d / "rules").exists()
    assert "- `web-research` —" in (d / "main.md").read_text(encoding="utf-8")
    main_text = (d / "main.md").read_text()
    assert "## Standing practices" in main_text
    assert "improve-" not in main_text
    # permissions are pure config (no local copies). Since 0.263.0 they mostly arrive through
    # the adopted TEMPLATE, so the routine's own file holds only its differences — and the
    # EFFECTIVE set is the union, which is what the run actually gets.
    assert raw.get("template"), "a scaffolded routine adopts a settings template"
    assert set(raw["permissions"]) <= set(cfg.permissions)
    assert "util-authoring" in cfg.permissions and "memory" in cfg.permissions
    # recipe improvement is centralized — self-modification is NOT a default anymore
    assert "self-modification" not in cfg.permissions
    assert (d / ".gitignore").read_text().startswith("runs/")
    with pytest.raises(ValueError):
        scaffold(server, slug="papers-radar", name="dup", instruction="x",
                 workflow_slug="general-task")
    with pytest.raises(ValueError):
        scaffold(server, slug="Bad Slug", name="x", instruction="x",
                 workflow_slug="general-task")


def test_scaffold_writes_stage_modules(tmp_path):
    server = ServerConfig()
    server.routines_home = tmp_path / "routines"
    server.routines_home.mkdir()
    server.libraries_home = SEED
    # the wizard passes extra stage modules; they land in the routine's stages/ (the LLM-decomposed
    # stages would too, but there's no generator endpoint in this test)
    d = scaffold(server, slug="split-routine", name="Split",
                 instruction="# Entry\n\nStages in stages/.", workflow_slug="general-task",
                 stages={"discover": "# Discover stage\n\nHow to discover.",
                         "compose.md": "# Compose stage\n\nHow to compose."})
    assert (d / "stages" / "discover.md").read_text().startswith("# Discover stage")
    assert (d / "stages" / "compose.md").read_text().startswith("# Compose stage")


def test_dump_markdown_roundtrips_through_engine_parse():
    """What scaffold/adapt/runtime write is exactly what the engine parses back —
    nested provenance, key order, and a body containing its own '---' lines."""
    import frontmatter

    from rsched.workflows.adapt import dump_markdown

    meta = {"name": "N", "slug": "s",
            "materialized_from": {"slug": "wf", "commit": "abc123", "version": 3},
            "tools": ["ask_user", "finish"]}
    body = "## Run flow\n1. x\n\n---\n\n## Completion criteria\n- done\n"
    text = dump_markdown(meta, body)
    meta2, body2 = frontmatter.parse(text)
    assert meta2 == meta and list(meta2) == list(meta)     # values AND key order survive
    assert body2 == body.strip()                           # later --- stays in the body
    assert text.endswith("\n") and not text.endswith("\n\n")



def test_cmd_lint_libraries_home_skips_server_config(tmp_path, monkeypatch):
    """`rsched lint --libraries-home DIR` lints the given library directly, WITHOUT reading the
    (sandbox-jailed) server config — the path sandboxed callers like the gu rsched-lint util use.
    """
    from types import SimpleNamespace

    from rsched import cli

    def _boom(*a, **k):
        raise AssertionError("load_server_config must not run when --libraries-home is given")

    monkeypatch.setattr(cli, "load_server_config", _boom)
    home = merged_library(tmp_path)
    assert cli.cmd_lint(SimpleNamespace(target=None, libraries_home=str(home))) == 0
    # without the flag it falls back to load_server_config (here our boom fires)
    with pytest.raises(AssertionError):
        cli.cmd_lint(SimpleNamespace(target=None, libraries_home=None))


def test_lint_validates_meta_tools_vocabulary():
    """META tools: entries must name real action kinds (engine/actionschema.KINDS) — a typo'd
    allowlist used to pass lint and silently allow nothing at run time."""
    # the seed META carries an explicit `"tools": None` (= everything allowed) — REPLACE
    # that entry; a duplicate key inserted at the top would be overwritten by it (dict
    # literals are last-wins)
    src = (SEED / "workflows" / "general-task.py").read_text()
    assert '"tools": None,' in src
    rules = ["ask-policy", "web-research", "decision-record", "intent-inference"]
    good = src.replace('"tools": None,', '"tools": ["read_file", "finish"],', 1)
    assert lint_workflow_py(good, filename="general-task.py", rule_slugs=rules) == []
    bad = src.replace('"tools": None,', '"tools": ["read_file", "reed_file"],', 1)
    probs = lint_workflow_py(bad, filename="general-task.py", rule_slugs=[])
    assert any("unknown action kind" in p and "reed_file" in p for p in probs)
    notlist = src.replace('"tools": None,', '"tools": "read_file",', 1)
    probs = lint_workflow_py(notlist, filename="general-task.py", rule_slugs=[])
    assert any("tools must be a list" in p for p in probs)


def test_merged_seed_library_is_clean(tmp_path):
    """The seed linted the way a real instance is laid out — workflows/rules/permissions
    PLUS utils/. lint_all(SEED) alone cannot cover this: library-seed carries no utils/ dir."""
    results = lint_all(merged_library(tmp_path))
    problems = {k: v for k, v in results.items() if v}
    assert problems == {}, problems
