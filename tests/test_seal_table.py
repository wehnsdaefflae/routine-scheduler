"""ONE table of sealed paths, every layer agreeing with it (`rsched/sealtable.py`)."""

from __future__ import annotations

import pytest

from helpers import run_context, server_config
from rsched import ownseal, runledger, sealtable
from rsched.engine import docread, finishline, obligations, outputs
from rsched.engine.actions import validate_action
from rsched.engine.fileops import _memory_gate, _write_gate
from rsched.grantpolicy import GrantPolicy
from rsched.grants import CONFIG_FILE
from rsched.jobs import JOBS_FILE
from rsched.rundepth import RUNS_DIR

TS = "20261121-060000"
SEALS = sealtable.SEALS


def _ctx(d, tmp_path):
    """A top-level run that keeps jobs and holds no recipe-authoring: every row is sealed."""
    return run_context(d, TS, server=server_config(libraries_home=tmp_path / "libraries"),
                       grants=GrantPolicy(jobs="on"))


def _target(d, seal):
    """A path the row holds: the file itself, or a file inside the directory."""
    return d / seal.name / "inside.txt" if seal.kind == "dir" else d / seal.name


def test_the_names_are_their_owners_constants():
    """The table imports each name from the module that owns it; the one it must spell (the
    document cache — `docread` imports the sandbox, which imports this table) is pinned here.
    """
    names = {s.name for s in SEALS}
    assert sealtable.DOC_CACHE_DIR == docread.CACHE_DIR
    for owned in (CONFIG_FILE, RUNS_DIR, outputs.OUTPUTS_DIR, docread.CACHE_DIR,
                  str(finishline.FILE), str(obligations.FILE), runledger.FILE,
                  runledger.ARCHIVE_DIR, JOBS_FILE, ".memory", "main.md", "stages",
                  "tuning.yaml"):
        assert owned in names, owned


@pytest.mark.parametrize("seal", SEALS, ids=lambda s: s.name)
def test_the_resolved_gate_refuses_every_row_in_its_own_words(seal, make_routine, tmp_path):
    d = make_routine()
    ctx = _ctx(d, tmp_path)
    target = _target(d, seal)
    route = ctx.grants.request_route(sealtable.RECIPE_ENTITY)
    rel = target.name if seal.by_name else str(target.relative_to(d))

    assert _write_gate(ctx, target.resolve()) == seal.worded(path=rel, route=route)
    if seal.reads:
        assert _memory_gate(ctx, target.resolve()) == seal.refusal


@pytest.mark.parametrize("seal", [s for s in SEALS if s.removal and not s.jailed],
                         ids=lambda s: s.name)
def test_the_removal_gate_refuses_taking_a_removal_row_with_its_directory(seal, make_routine,
                                                                          tmp_path):
    d = make_routine()
    target = d / seal.name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("{}\n", encoding="utf-8")

    err = _write_gate(_ctx(d, tmp_path), target.parent.resolve(), creates=False)

    assert err == f"this would remove {seal.name}: {seal.refusal}"


def test_the_jail_withholds_exactly_the_tables_top_level_rows():
    """Every top-level row is withheld; the recipe's rows lift with recipe-authoring and
    nothing else does. A nested row cannot be withheld without sealing its parent, so the
    file gate alone holds it — and the jail must not pretend otherwise.
    """
    locked = set(ownseal.sealed_names(recipe_unlocked=False))
    unlocked = set(ownseal.sealed_names(recipe_unlocked=True))
    assert locked == {s.name for s in SEALS if s.jailed}
    assert unlocked == {s.name for s in SEALS if s.jailed and not s.recipe}
    assert set(ownseal.SEALED_DIRS) | set(ownseal.SEALED_FILES) == unlocked
    assert not any("/" in n for n in locked)


def test_the_lexical_gates_say_what_the_resolved_gate_says(make_routine, tmp_path):
    """The two layers of one seal: where both refuse, the words come from one row — `runs/`
    and the recipe word-for-word in both, `routine.yaml` and `.memory/` in each layer's own
    pinned wording from that same row.
    """
    d = make_routine()
    ctx = _ctx(d, tmp_path)
    policy = ctx.grants
    route = policy.request_route(sealtable.RECIPE_ENTITY)

    def deny(path):
        return policy.deny({"kind": "write_file", "path": path, "content": "x"})

    assert deny("runs/x.txt") == sealtable.RUNS.refusal
    assert _write_gate(ctx, (d / "runs" / "x.txt").resolve()) == sealtable.RUNS.refusal
    assert deny("main.md") == sealtable.recipe_refusal("main.md", route)
    assert _write_gate(ctx, (d / "main.md").resolve()) == sealtable.recipe_refusal("main.md",
                                                                                 route)
    assert deny(CONFIG_FILE) == sealtable.CONFIG.worded(path=CONFIG_FILE, lexical=True)
    assert _write_gate(ctx, (d / CONFIG_FILE).resolve()) == sealtable.CONFIG.refusal

    problems = validate_action({"say": "x", "kind": "read_file", "path": ".memory/a.md"})
    assert sealtable.MEMORY.worded(kind="read_file", lexical=True) in problems
