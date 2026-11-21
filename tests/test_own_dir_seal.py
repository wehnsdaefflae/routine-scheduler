"""The shell/util jail withholds what the engine's write gate refuses (D179, F571)."""

from __future__ import annotations

from pathlib import Path

import pytest

from helpers import run_context, server_config
from rsched import ownseal, sandbox
from rsched.engine.executor import dispatch
from rsched.grantpolicy import GrantPolicy


def _dir_with_children(tmp_path: Path) -> Path:
    own = tmp_path / "a-routine"
    for name in (".memory", "runs", ".util_outputs", ".doc_cache", ".ledger", "stages",
                 "state", "artifacts", "scripts"):
        (own / name).mkdir(parents=True)
    for name in ("routine.yaml", "main.md", "tuning.yaml", "LEDGER.md", "notes.md"):
        (own / name).write_text("x\n", encoding="utf-8")
    return own


def _spec(own: Path, *,
          recipe_unlocked: bool = False) -> tuple[set[str], set[str], set[str]]:
    """The own-dir mount's READ, WRITE and LIST-ONLY rules."""
    ro: list[str] = []
    rw: list[str] = []
    ls: list[str] = []
    policy = sandbox.SandboxPolicy(own_dir=own, recipe_unlocked=recipe_unlocked)
    ownseal.mount_own_dir(policy, ro, rw, ls)
    return set(ro), set(rw), set(ls)


def _writable(rw: set[str], path: Path) -> bool:
    """Is `path` writable UNDER the spec — i.e. is any rw rule it or an ancestor of it?"""
    return any(path == Path(r) or path.is_relative_to(Path(r)) for r in rw)


def test_every_path_the_write_gate_refuses_is_withheld_from_the_jail(tmp_path):
    own = _dir_with_children(tmp_path)
    _, rw, _ = _spec(own)
    for sealed in (".memory", "runs", ".util_outputs", ".doc_cache", "routine.yaml",
                   "main.md", "stages", "tuning.yaml", "LEDGER.md", ".ledger"):
        assert not _writable(rw, own / sealed), f"{sealed} is writable in the jail"
        # and so is anything beneath a sealed DIRECTORY, which is what a redirect targets
        assert not _writable(rw, own / sealed / "inside.txt")


def test_the_own_dir_is_still_visible_and_its_ordinary_children_writable(tmp_path):
    """The seal must not cost the working directory. A command still cds into it, lists it and
    reads its recipe; `state/`, `artifacts/`, `scripts/` and ordinary files stay writable —
    `cp … state/x.json` and a python write to `state/*.json` are what shell-holding routines
    measurably do (the 30 `ungated_write` health records of 2026-10-18 to 2026-10-21). The
    third shape they used, `cat >> LEDGER.md`, is the one P1 SEALED: the engine writes that
    record from the finish summary (`rsched/runledger.py`).
    """
    own = _dir_with_children(tmp_path)
    ro, rw, ls = _spec(own)
    # LIST-ONLY since D207 (the dir was `ro` before): a command still cds into it and lists
    # it, and each child it may READ is mounted beneath — what changed is that `runs/` is no
    # longer among them wholesale. `rundepth` carries the why.
    assert str(own) in ls and str(own) not in ro
    assert str(own / "main.md") in ro and str(own / "routine.yaml") in ro
    for ordinary in ("state", "artifacts", "scripts", "notes.md"):
        assert str(own / ordinary) in rw, f"{ordinary} lost its write access"


def test_recipe_authoring_lifts_the_recipe_half_and_nothing_else(tmp_path):
    """The recipe seal is the one part a PERMISSION lifts, exactly as in `_write_gate`: a
    routine the user granted recipe-authoring may edit main.md/stages/ through `shell` too.
    Config and the engine-owned stores are not permissions and never lift.
    """
    own = _dir_with_children(tmp_path)
    _, rw, _ = _spec(own, recipe_unlocked=True)
    for recipe in ("main.md", "stages", "tuning.yaml"):
        assert str(own / recipe) in rw, f"{recipe} is sealed from a routine that may write it"
    for never in (".memory", "runs", "routine.yaml", "LEDGER.md", ".ledger"):
        assert str(own / never) not in rw


def test_the_sealed_names_are_the_gates_own_constants(tmp_path):
    """One source, two enforcers. `CONFIG_FILE` and `RECIPE_PREFIXES` are imported from
    `grants` — the same two the file gate reads through `grantpolicy.is_recipe_path` — so a
    rename cannot leave the kernel jail enforcing yesterday's names.
    """
    from rsched.grants import CONFIG_FILE, RECIPE_PREFIXES

    sealed = ownseal.sealed_names(recipe_unlocked=False)
    assert CONFIG_FILE in sealed
    for prefix in RECIPE_PREFIXES:
        assert prefix.strip("/").split("/")[0] in sealed
    assert CONFIG_FILE in ownseal.sealed_names(recipe_unlocked=True)


def test_the_kernel_actually_refuses_the_write_a_shell_redirect_used_to_make(
        make_routine, tmp_path, monkeypatch):
    """The one test here that is evidence rather than arithmetic: the seal is a KERNEL
    property, so it is proven by running a real `bash -c` redirect at the paths F571 measured.
    A spec dict with the right strings in it proves only that the strings are right.
    """
    import tempfile

    if not sandbox.available():
        pytest.skip("Landlock unavailable on this kernel — the jail cannot engage")
    real_toolchain = sandbox._toolchain
    # the tmp roots as the toolchain itself names them, so this cannot drift from _SYSTEM_RW
    scratch = Path(tempfile.gettempdir()).resolve()

    def _is_scratch(root: str) -> bool:
        p = Path(root).resolve()
        return p == scratch or scratch.is_relative_to(p)

    def _tmp_read_only():
        ro, rw = real_toolchain()
        moved = [r for r in rw if _is_scratch(r)]
        return [*ro, *moved], [r for r in rw if not _is_scratch(r)]

    monkeypatch.setattr(sandbox, "_toolchain", _tmp_read_only)
    server = server_config(libraries_home=tmp_path / "libraries", sandbox="permissive")
    (server.libraries_home / "utils").mkdir(parents=True)
    ctx = run_context(make_routine(slug="sealed"), "20260903-120000", server=server,
                      grants=GrantPolicy(actions=frozenset({"shell"})))
    own = ctx.routine.dir
    (own / ".memory").mkdir(exist_ok=True)
    (own / "state").mkdir(exist_ok=True)
    (own / "routine.yaml").write_text("budgets: {}\n", encoding="utf-8")
    (own / "main.md").write_text("# the recipe\n", encoding="utf-8")

    (own / "LEDGER.md").write_text("# LEDGER\n", encoding="utf-8")
    for target in (".memory/stolen.md", "routine.yaml", "main.md", "LEDGER.md"):
        obs = dispatch({"kind": "shell", "command": f"echo pwned >> {target}"}, ctx)
        assert obs["exit"] != 0, f"the jail let a shell redirect write {target}: {obs}"
    assert "pwned" not in (own / "routine.yaml").read_text(encoding="utf-8")
    assert "pwned" not in (own / "LEDGER.md").read_text(encoding="utf-8")
    assert "pwned" not in (own / "main.md").read_text(encoding="utf-8")
    assert not (own / ".memory" / "stolen.md").exists()

    # …and the ordinary work of a shell-holding routine still succeeds
    ok = dispatch({"kind": "shell", "command": "echo fine > state/scratch.txt"}, ctx)
    assert ok["exit"] == 0, ok
    assert (own / "state" / "scratch.txt").read_text(encoding="utf-8").strip() == "fine"


def test_a_missing_or_unreadable_own_dir_mounts_nothing_rather_than_raising(tmp_path):
    """Composition is total about a broken root (R2260): a dir that cannot be listed costs the
    run no util. Nothing is mounted writable, which fails closed."""
    ro, rw, ls = _spec(tmp_path / "never-created")
    assert rw == set()
    # the dir is still NAMED (list-only), and nothing beneath it is readable: a `runs/` that
    # cannot be listed mounts no run dir rather than raising (R2260, and `rundepth._names`).
    assert ls == {str(tmp_path / "never-created")}
    assert ro == set()
