"""Every bundled seed artifact validated against the LIVE contracts it must satisfy.

The seeds drifted through three renames because nothing pinned them: a routine-seed
referencing a retired module layout, a workflow naming a kind that no longer exists, a
permission whose `requires:` stopped normalizing — all invisible until a fresh install
broke. This suite makes seed drift a test failure in the same commit as the rename.

Covered: routine-seed/ (routine.yaml via load_routine, stages/traits file references,
`state/phase.json` instructions in the canonical {"phase": ...} shape, action-kind
references, permissions that exist in library-seed), library-seed/ (workflows parse via
pyworkflow and lint clean; traits/permissions/playbooks lint clean; permission `requires:`
normalize), and util-seed/ (docstring headers pass the engine's own gate).
"""

import re
from pathlib import Path

import pytest

from rsched.config import load_routine
from rsched.engine.actions import KINDS
from rsched.grants import normalize_capabilities
from rsched.utils_lib import header_problems
from rsched.workflows.lint import lint_all
from rsched.workflows.pyworkflow import parse_py

REPO = Path(__file__).resolve().parent.parent
ROUTINE_SEEDS = sorted(p for p in (REPO / "routine-seed").iterdir() if p.is_dir())
LIBRARY_SEED = REPO / "library-seed"
UTIL_SEEDS = sorted((REPO / "util-seed" / "utils").glob("*/main.py"))

SEED_MD = sorted([*(REPO / "routine-seed").rglob("*.md"),
                  *(REPO / "library-seed").rglob("*.md")])


def _ids(paths):
    return [str(p.relative_to(REPO)) for p in paths]


# ---- routine-seed: config loads clean against the live schema ----------------------------


@pytest.mark.parametrize("seed", ROUTINE_SEEDS, ids=_ids(ROUTINE_SEEDS))
def test_routine_seed_config_loads_clean(seed):
    cfg, problems = load_routine(seed)
    assert cfg is not None, problems
    assert problems == [], f"{seed.name}/routine.yaml: {problems}"
    # capabilities must normalize without complaint (the engine builds run policy from them)
    _caps, cap_problems = normalize_capabilities(cfg.capabilities)
    assert cap_problems == []
    # held permission docs must exist in library-seed (a fresh install seeds exactly those)
    have = {p.stem for p in (LIBRARY_SEED / "permissions").glob("*.md")}
    missing = [p for p in cfg.permissions if p not in have]
    assert not missing, f"{seed.name} holds permissions with no seed doc: {missing}"


@pytest.mark.parametrize("seed", ROUTINE_SEEDS, ids=_ids(ROUTINE_SEEDS))
def test_routine_seed_recipe_structure(seed):
    main = seed / "main.md"
    assert main.is_file(), f"{seed.name} has no main.md"
    body = main.read_text(encoding="utf-8")
    assert "## Standing practices" in body, (
        f"{seed.name}/main.md lacks the Standing practices tail "
        "(scaffold.with_practices_tail guarantees it on every real routine)")
    # every trait the tail references is bundled with the seed
    for name in re.findall(r"traits/([a-z0-9-]+\.md)", body):
        assert (seed / "traits" / name).is_file(), (
            f"{seed.name}/main.md references traits/{name} which the seed does not bundle")


@pytest.mark.parametrize("seed", ROUTINE_SEEDS, ids=_ids(ROUTINE_SEEDS))
def test_routine_seed_stage_references_resolve(seed):
    """Every stages/<name>.md mentioned anywhere in the recipe exists on disk — the drift
    class that broke twice through the step→stage renames."""
    sources = [seed / "main.md", *sorted((seed / "stages").glob("*.md"))]
    problems = []
    for src in sources:
        body = src.read_text(encoding="utf-8")
        for name in set(re.findall(r"stages/([a-z0-9-]+\.md)", body)):
            if not (seed / "stages" / name).is_file():
                problems.append(f"{src.relative_to(REPO)} references stages/{name} (missing)")
    assert not problems, "\n".join(problems)


# ---- all seed markdown: phase.json shape + action-kind references ------------------------


@pytest.mark.parametrize("md", SEED_MD, ids=_ids(SEED_MD))
def test_seed_phase_instructions_use_canonical_shape(md):
    """A recipe telling the run to write state/phase.json must show the canonical
    {"phase": ...} shape (or {} to reset) — statemap and the live rail key off it."""
    problems = []
    for i, line in enumerate(md.read_text(encoding="utf-8").splitlines(), 1):
        # only whole-file assignments count: `state/phase.json = {...}` (subkey updates and
        # prose that merely mentions the file alongside another dict are not the contract)
        m = re.search(r"phase\.json`?\s*=\s*`?(\{.*)", line)
        if not m:
            continue
        payload = m.group(1)
        if payload.startswith(("{}", '{"phase"', "{phase")):
            continue
        problems.append(f"{md.relative_to(REPO)}:{i}: phase.json payload {payload!r} "
                        'is not the canonical {"phase": ...} shape')
    assert not problems, "\n".join(problems)


@pytest.mark.parametrize("md", SEED_MD, ids=_ids(SEED_MD))
def test_seed_action_references_are_live_kinds(md):
    """Prose of the form 'the `X` action' (or '`X` action') must name a kind the engine
    actually has — a renamed action otherwise lives on in seed prose forever."""
    body = md.read_text(encoding="utf-8")
    problems = [f"{md.relative_to(REPO)}: references unknown action kind `{kind}`"
                for kind in re.findall(r"`(\w+)` action\b", body) if kind not in KINDS]
    assert not problems, "\n".join(problems)


# ---- library-seed: workflows parse + the whole tree lints clean ---------------------------


WORKFLOW_SEEDS = sorted((LIBRARY_SEED / "workflows").glob("*.py"))


@pytest.mark.parametrize("wf", WORKFLOW_SEEDS, ids=_ids(WORKFLOW_SEEDS))
def test_workflow_seed_parses_and_matches_contract(wf):
    meta = parse_py(wf.read_text(encoding="utf-8"))   # META keys flat, + phases/funcs
    assert meta["slug"] == wf.stem, f"{wf.name}: META slug {meta['slug']!r} ≠ filename"
    unknown = [t for t in (meta.get("tools") or []) if t not in KINDS]
    assert not unknown, f"{wf.name}: tools allowlist names unknown kinds {unknown}"


def test_library_seed_lints_clean():
    results = lint_all(LIBRARY_SEED)
    assert results, "lint_all found nothing in library-seed — wrong directory layout?"
    dirty = {name: probs for name, probs in results.items() if probs}
    assert not dirty, "\n".join(f"{n}: {p}" for n, p in dirty.items())


# ---- util-seed: docstring headers pass the engine's own write_util gate ------------------


@pytest.mark.parametrize("util", UTIL_SEEDS, ids=_ids(UTIL_SEEDS))
def test_util_seed_headers_pass_engine_gate(util):
    problems = header_problems(util.read_text(encoding="utf-8"))
    assert problems == [], f"{util.name}: {problems}"
