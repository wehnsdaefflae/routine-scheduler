"""The shell/util jail withholds what the engine's write gate refuses (D179, F571).

Every write protection the engine has lived in the FILE-action gate alone
(`engine/fileops._write_gate`): the `.memory/` seal, the engine-ownership of `runs/`,
`.util_outputs/` and `.doc_cache/`, the `routine.yaml` config refusal and the recipe boundary.
A `bash -c` redirect calls none of them, so all five were bypassable by any routine holding
`shell` — which is how one run applied an operator-authorized config change to twelve
`routine.yaml` files (F571, authorized; an unauthorized one would have been equally unblocked),
and how a routine without the recipe-authoring permission could rewrite its own `main.md`.
It was also INVISIBLE: a refused `edit_file` leaves an error in the transcript, a shell write
leaves nothing.

The operator decided (D179, 2026-10-12): enumerate own_dir's writable children from the same
predicate the write gate uses, accepting that a write to an unlisted path inside the routine dir
now fails with a bare kernel error. Landlock has no deny rule — a nested rule only ADDS rights —
so the seal is necessarily a split mount: the dir read-only, its unsealed children read-write.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from helpers import run_context, server_config
from rsched import ownseal, sandbox
from rsched.engine.executor import dispatch
from rsched.grantpolicy import GrantPolicy


def _dir_with_children(tmp_path: Path) -> Path:
    own = tmp_path / "a-routine"
    for name in (".memory", "runs", ".util_outputs", ".doc_cache", "stages", "state",
                 "artifacts", "scripts"):
        (own / name).mkdir(parents=True)
    for name in ("routine.yaml", "main.md", "tuning.yaml", "LEDGER.md", "notes.md"):
        (own / name).write_text("x\n", encoding="utf-8")
    return own


def _spec(own: Path, *, recipe_unlocked: bool = False) -> tuple[set[str], set[str]]:
    ro: list[str] = []
    rw: list[str] = []
    policy = sandbox.SandboxPolicy(own_dir=own, recipe_unlocked=recipe_unlocked)
    ownseal.mount_own_dir(policy, ro, rw)
    return set(ro), set(rw)


def _writable(rw: set[str], path: Path) -> bool:
    """Is `path` writable UNDER the spec — i.e. is any rw rule it or an ancestor of it?

    Landlock rights are inherited down the hierarchy, so literal set membership is the wrong
    question and asserting it is how a seal test passes on unsealed code: mounting the routine
    dir `rw` makes `.memory/` writable without ever naming it. (Caught here by stubbing the
    pre-fix mount back in — the first version of this test was green against it.)
    """
    return any(path == Path(r) or path.is_relative_to(Path(r)) for r in rw)


def test_every_path_the_write_gate_refuses_is_withheld_from_the_jail(tmp_path):
    own = _dir_with_children(tmp_path)
    _, rw = _spec(own)
    for sealed in (".memory", "runs", ".util_outputs", ".doc_cache", "routine.yaml",
                   "main.md", "stages", "tuning.yaml"):
        assert not _writable(rw, own / sealed), f"{sealed} is writable in the jail"
        # and so is anything beneath a sealed DIRECTORY, which is what a redirect targets
        assert not _writable(rw, own / sealed / "inside.txt")


def test_the_own_dir_is_still_visible_and_its_ordinary_children_writable(tmp_path):
    """The seal must not cost the working directory. A command still cds into it, lists it and
    reads its recipe; `state/`, `artifacts/`, `scripts/` and ordinary files stay writable —
    `cat >> LEDGER.md`, `cp … state/x.json` and a python write to `state/*.json` are what
    shell-holding routines measurably do (the 30 `ungated_write` health records of 2026-10-18
    to 2026-10-21).
    """
    own = _dir_with_children(tmp_path)
    ro, rw = _spec(own)
    assert str(own) in ro
    for ordinary in ("state", "artifacts", "scripts", "LEDGER.md", "notes.md"):
        assert str(own / ordinary) in rw, f"{ordinary} lost its write access"


def test_recipe_authoring_lifts_the_recipe_half_and_nothing_else(tmp_path):
    """The recipe seal is the one part a PERMISSION lifts, exactly as in `_write_gate`: a
    routine the user granted recipe-authoring may edit main.md/stages/ through `shell` too.
    Config and the engine-owned stores are not permissions and never lift.
    """
    own = _dir_with_children(tmp_path)
    _, rw = _spec(own, recipe_unlocked=True)
    for recipe in ("main.md", "stages", "tuning.yaml"):
        assert str(own / recipe) in rw, f"{recipe} is sealed from a routine that may write it"
    for never in (".memory", "runs", "routine.yaml"):
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

    The sandbox is ON for this one (`test_shell_action.shell_ctx` runs with `sandbox: off`,
    where there is no jail to assert about). Skipped where Landlock cannot engage — with no
    jail the protection genuinely does not exist, which is the documented permissive mode.

    AND the toolchain's tmp rules are withheld for the duration, which is not a convenience:
    `_toolchain()` mounts `/tmp` and `/var/tmp` read-WRITE for every jailed command (uv, pip,
    compilers and bash itself need a scratch dir), and pytest's `tmp_path` lives under `/tmp`.
    So without this, a routine dir under `tmp_path` is writable by the TOOLCHAIN rule whatever
    the own-dir mount says, and the first version of this test passed its redirect at exit 0
    with the seal fully in place. A filesystem refusal cannot be proven at a path some other
    rule already grants. (On the live instance routine dirs are under the routines home, not
    under /tmp, so the seal is not weakened there — this is a test-location artefact.)
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

    for target in (".memory/stolen.md", "routine.yaml", "main.md"):
        obs = dispatch({"kind": "shell", "command": f"echo pwned > {target}"}, ctx)
        assert obs["exit"] != 0, f"the jail let a shell redirect write {target}: {obs}"
    assert "pwned" not in (own / "routine.yaml").read_text(encoding="utf-8")
    assert "pwned" not in (own / "main.md").read_text(encoding="utf-8")
    assert not (own / ".memory" / "stolen.md").exists()

    # …and the ordinary work of a shell-holding routine still succeeds
    ok = dispatch({"kind": "shell", "command": "echo fine > state/scratch.txt"}, ctx)
    assert ok["exit"] == 0, ok
    assert (own / "state" / "scratch.txt").read_text(encoding="utf-8").strip() == "fine"


def test_a_missing_or_unreadable_own_dir_mounts_nothing_rather_than_raising(tmp_path):
    """Composition is total about a broken root (R2260): a dir that cannot be listed costs the
    run no util. Nothing is mounted writable, which fails closed."""
    ro, rw = _spec(tmp_path / "never-created")
    assert rw == set()
    assert ro == {str(tmp_path / "never-created")}
