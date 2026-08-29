"""Every bundled seed artifact validated against the LIVE contracts it must satisfy.

The seeds drifted through three renames because nothing pinned them: a workflow naming a kind
that no longer exists, a permission whose `requires:` stopped normalizing — all invisible until
a fresh install broke. This suite makes seed drift a test failure in the same commit as the
rename.

Covered: library-seed/ (workflows parse via pyworkflow and lint clean; rules/permissions/
playbooks lint clean; permission `requires:` normalize; `state/phase.json` instructions in the
canonical {"phase": ...} shape; action-kind references), and util-seed/ (docstring headers pass
the engine's own write_util gate).
"""

import re
from pathlib import Path

import pytest

from rsched.engine.actionschema import KINDS
from rsched.utils_header import header_problems
from rsched.workflows.lint import lint_all
from rsched.workflows.pyworkflow import parse_py

REPO = Path(__file__).resolve().parent.parent
LIBRARY_SEED = REPO / "library-seed"
UTIL_SEEDS = sorted((REPO / "util-seed" / "utils").glob("*/main.py"))

SEED_MD = sorted((REPO / "library-seed").rglob("*.md"))


def _ids(paths):
    return [str(p.relative_to(REPO)) for p in paths]


# ---- all seed markdown: phase.json shape + action-kind references ------------------------


@pytest.mark.parametrize("md", SEED_MD, ids=_ids(SEED_MD))
def test_seed_phase_instructions_use_canonical_shape(md):
    """A recipe telling the run to write state/phase.json must show the canonical
    {"phase": ...} shape (or {} to reset) — the state digest's Current-phase line keys
    off it (the LIVE diagram doesn't: the engine tracks stage-module reads instead)."""
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
