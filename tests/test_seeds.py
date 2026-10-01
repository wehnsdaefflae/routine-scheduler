"""Every bundled seed artifact validated against the LIVE contracts it must satisfy.

The seeds drifted through three renames because nothing pinned them: a workflow naming a kind
that no longer exists, a permission whose `requires:` stopped normalizing — all invisible until
a fresh install broke. This suite makes seed drift a test failure in the same commit as the
rename.

Covered: library-seed/ (workflows parse via pyworkflow and lint clean; rules/permissions/
playbooks lint clean; permission `requires:` normalize; `state/phase.json` instructions in the
canonical {"phase": ...} shape; action-kind references), and util-seed/ (docstring headers pass
the engine's own write_util gate, and every util's own offline `--selftest` passes).
"""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from rsched import library_docs
from rsched.daemon.gate_prepare import ADMIT
from rsched.engine.actionschema import KINDS
from rsched.engine.capabilities import PERMISSION_NOTE_MAX_CHARS
from rsched.entities import guarded_roots
from rsched.utils_header import header_problems
from rsched.workflows.lint import lint_all
from rsched.workflows.pyworkflow import parse_py

REPO = Path(__file__).resolve().parent.parent
LIBRARY_SEED = REPO / "library-seed"
UTIL_SEEDS = sorted((REPO / "util-seed" / "utils").glob("*/main.py"))

SEED_MD = sorted((REPO / "library-seed").rglob("*.md"))
PATTERN_SEEDS = sorted((LIBRARY_SEED / "patterns").glob("*.yaml"))
PERMISSION_SEEDS = sorted((LIBRARY_SEED / "permissions").glob("*.md"))
#: Every seed document a run or a person reads as instructions.
SEED_TEXTS = sorted([*SEED_MD, *(LIBRARY_SEED / "workflows").glob("*.py"), *PATTERN_SEEDS])


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


def test_seed_prose_names_the_script_the_run_gate_runs():
    """The run gate's custom predicate is `scripts/<ADMIT>.py` — named so because routines
    already ran a `scripts/gate.py` of their own as an IN-RUN check. A recipe told that some
    other file belongs to the run gate writes the predicate where the gate never looks, and
    steers its own check away from a name that was always free."""
    problems = []
    for doc in SEED_TEXTS:
        text = " ".join(doc.read_text(encoding="utf-8").split())   # sentences, not lines
        for sentence in re.split(r"(?<=[.;])\s", text):
            if "run gate" in sentence:
                problems += [f"{doc.relative_to(REPO)}: calls scripts/{name}.py the run gate's "
                             f"(it runs scripts/{ADMIT}.py)"
                             for name in re.findall(r"scripts/(\w+)\.py", sentence)
                             if name != ADMIT]
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


@pytest.mark.parametrize("pattern", PATTERN_SEEDS, ids=_ids(PATTERN_SEEDS))
def test_seed_pattern_grants_no_credential_store(pattern):
    """Creation writes a pattern's roots straight into the new routine.yaml, past the PATCH
    that refuses a credential store as a folder grant (entities.NEVER_GRANTABLE). A seed
    carrying one would mount the console token and the central secrets into every run of
    every routine that follows it."""
    settings = yaml.safe_load(pattern.read_text(encoding="utf-8"))["settings"]
    roots = [*(settings.get("fs_read_roots") or []), *(settings.get("fs_write_roots") or [])]
    assert guarded_roots(roots) == []


@pytest.mark.parametrize("perm", PERMISSION_SEEDS, ids=_ids(PERMISSION_SEEDS))
def test_seed_permission_note_reaches_the_prompt_whole(perm):
    """A held permission's body is its whole contribution to the prompt, cut on a line
    boundary past PERMISSION_NOTE_MAX_CHARS. The cap is a backstop for library edits; a SEED
    doc past it would lose its last lines of conduct in every run that holds it."""
    body = library_docs.doc_body(perm.read_text(encoding="utf-8")).strip()
    assert len(body) <= PERMISSION_NOTE_MAX_CHARS


# ---- util-seed: docstring headers pass the engine's own write_util gate ------------------


@pytest.mark.parametrize("util", UTIL_SEEDS, ids=_ids(UTIL_SEEDS))
def test_util_seed_headers_pass_engine_gate(util):
    problems = header_problems(util.read_text(encoding="utf-8"))
    assert problems == [], f"{util.name}: {problems}"


# The selftests ARE the seed utils' regression tests — `write_util` runs them before a util
# lands — yet a seed util ships to every instance without ever passing through that gate, so
# nothing ran them: git's hook-refusal fixture sat uncalled while it was the only proof of a
# fix. Run here without uv: a selftest is offline by contract, and this interpreter carries
# every import one reaches — pyyaml is a package dependency, and the network clients (paramiko,
# httpx, ddgs) are imported only in the code that talks to the network. In its own process, so
# a selftest's env and signal changes stay there.
@pytest.mark.parametrize("util", UTIL_SEEDS, ids=_ids(UTIL_SEEDS))
def test_util_seed_selftest_passes(util, tmp_path):
    if util.parent.name == "instance-export" and os.geteuid() == 0:
        pytest.skip("its selftest proves a chmod-000 directory is reported; root reads it anyway")
    done = subprocess.run([sys.executable, str(util), "--selftest"], cwd=tmp_path,
                          capture_output=True, text=True, timeout=300, check=False)
    assert done.returncode == 0, f"{util.parent.name}:\n{done.stdout}\n{done.stderr}"
