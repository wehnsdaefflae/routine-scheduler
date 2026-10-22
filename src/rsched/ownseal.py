"""Which children of a routine's OWN directory the engine refuses to let it write (D179, F571).

Every write protection the engine has lives in the FILE-action gate
(`engine/fileops._write_gate`): the `.memory/` seal, the engine-ownership of `runs/`,
`.util_outputs/` and `.doc_cache/`, the `routine.yaml` config refusal, and — when
`recipe_unlocked` is false — the recipe boundary. A `bash -c` redirect calls none of them, so
every one of them was bypassable by any routine holding `shell`: that is how a run applied an
operator-authorized config change to twelve `routine.yaml` files, and an UNauthorized one would
have been equally unblocked. The recipe half is the serious one — recipe-authoring is a
permission the USER grants, and a routine holding `shell` could rewrite its own `main.md`
without it. It is also invisible rather than merely permissive: a refused `edit_file` leaves an
error in the transcript, a shell write leaves nothing.

The operator decided (D179, 2026-10-12): *"Enumerate own_dir's writable children from the same
predicate the engine's write gate uses — closes the bypass completely; cost is that a routine
writing an unlisted path inside its own directory breaks with a bare kernel error."* That cost
is on the record and accepted.

WHY ENUMERATION, and not a deny rule: Landlock has no deny. A ruleset is an ALLOW-list of path
hierarchies, and a nested rule can only ADD rights — it cannot carve a read-only hole out of a
writable parent. So the only way to seal `.memory/` inside a writable own_dir is to stop
mounting own_dir writable and mount its writable children instead.

ONE source for both enforcers. The names here are the gate's own constants, IMPORTED, not
copied: `CONFIG_FILE` and `RECIPE_PREFIXES` from `grants` — the same two the gate reads through
`grantpolicy.is_recipe_path`. A second list kept in step by hand is how two gates drift, which
is the defect this closes. (The engine-owned directory names are spelled here because the gate
spells them too, each at its own site: a shared tuple is the next step, not this one.)
"""

from __future__ import annotations

from pathlib import Path

from .grants import CONFIG_FILE, RECIPE_PREFIXES

#: Engine-owned or sealed children of a routine dir, as `_write_gate` refuses them. `runs/` is
#: the engine's record of what happened; `.util_outputs/` the saved full text of util output;
#: `.doc_cache/` the converted documents a later read trusts by content hash; `.memory/` the
#: name-addressed store whose INDEX and 100-line note cap the engine owns.
SEALED_DIRS = (".memory", "runs", ".util_outputs", ".doc_cache")

#: Sealed FILES directly under the routine dir. `routine.yaml` is config — permissions,
#: capabilities, budgets, roots — and no run edits it, not even the routine-improver.
SEALED_FILES = (CONFIG_FILE,)


def _recipe_names() -> tuple[str, ...]:
    """The recipe's top-level entries, from the same prefixes `grantpolicy.is_recipe_path`
    tests. A prefix is relative to the routine dir and may name a file (`main.md`) or a
    directory (`stages/`); only its FIRST segment matters here, because the jail mounts (or
    withholds) whole top-level entries.
    """
    return tuple(sorted({p.strip("/").split("/")[0] for p in RECIPE_PREFIXES if p.strip("/")}))


def sealed_names(*, recipe_unlocked: bool) -> tuple[str, ...]:
    """Every top-level name under a routine dir that the engine's write gate refuses.

    `recipe_unlocked` is the routine's recipe-authoring permission as `GrantPolicy` resolves
    it: the recipe seal is the one entry here that a permission can lift, exactly as in
    `_write_gate`, so a routine that MAY edit its own recipe can still do it through `shell`.
    """
    names = {*SEALED_DIRS, *SEALED_FILES}
    if not recipe_unlocked:
        names |= set(_recipe_names())
    return tuple(sorted(names))


def recipe_unlocked_for(ctx) -> bool:
    """Does this run hold recipe-authoring? The recipe seal is the ONE part of the own-dir
    narrowing a permission lifts, exactly as in `fileops._write_gate`: a routine the user
    granted recipe-authoring may edit its `main.md`/`stages/`/`tuning.yaml` through `shell`
    too. Total about a context without grants (the run-less and test policies), because a
    jail composition that raises costs the run every util (R2260).
    """
    return bool(getattr(getattr(ctx, "grants", None), "recipe_unlocked", False))


def mount_own_dir(policy, ro: list[str], rw: list[str]) -> None:
    """Add `policy.own_dir`'s jail rules to `ro`/`rw`, in place — the sandbox's one call site.

    The dir itself is READ-ONLY so a command can still cd into it, list it and read its
    recipe, config and notes; its UNSEALED children are read-write. It is here rather than in
    `sandbox.wrap` so the reason the mount is split lives with the predicate that splits it,
    and so `sandbox.py` (at its size ceiling) carries the call and not the rationale.
    """
    own = policy.own_dir
    if own is None:
        return
    ro.append(str(own))
    rw += [str(c) for c in writable_children(
        own, recipe_unlocked=bool(getattr(policy, "recipe_unlocked", False)))]


def writable_children(own_dir: Path, *, recipe_unlocked: bool) -> list[Path]:
    """`own_dir`'s children that a subprocess may write: everything that exists minus what
    `sealed_names` withholds. `own_dir` itself is NOT included — that is the whole point.

    Only EXISTING children can be mounted (a Landlock rule needs an inode), which is the
    accepted cost: a command creating a NEW top-level entry inside the routine dir meets a
    bare kernel error instead of a readable refusal. Creating one through the file actions
    works as it always did, and that is where a new store belongs.
    """
    sealed = set(sealed_names(recipe_unlocked=recipe_unlocked))
    try:
        children = sorted(own_dir.iterdir())
    except OSError:
        return []
    return [c for c in children if c.name not in sealed]
