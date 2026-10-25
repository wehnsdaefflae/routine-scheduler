"""Which PREVIOUS run directories a run may read — the one predicate both enforcers of the
run-history depth compile from (D207).

The depth is an operator setting (`capabilities.runs`: none | last | all, carried as
`grantpolicy.GrantPolicy.run_history`). Until D207 it was enforced in the ACTION layer alone
(`engine/fileops._runs_read_gate`): `read_file` refused an older `runs/<ts>/gate.json` with
"previous runs are readable at the default 'last' depth only", while `gu json get <that same
absolute path>` returned it — a util is handed the path and opens it with ordinary access
inside a jail that mounted the whole routine dir readable, its own `runs/` tree included. The
operator decided it is a real boundary (2026-10-25): *enforce the depth where the jail is
composed, so a util handed a path cannot read deeper than the cap allows.*

LANDLOCK HAS NO DENY, which decides the shape — the same constraint that made the own-dir
write seal a split mount (`ownseal`). A ruleset is an allow-list of path hierarchies and a
nested rule can only ADD rights, so `runs/<old-ts>/` cannot be carved out of a readable
routine dir by subtraction. Enforcing a depth therefore means not mounting the routine dir
READABLE at all: it is mounted LIST-ONLY (the `ls` spec key — `landlock.ACCESS_LS`), its other
children are mounted readable one by one, and under `runs/` only the permitted run dirs are.

THREE CONSEQUENCES, accepted and documented in docs/sandboxing.md:

* Run ids stay ENUMERABLE. FS_READ_DIR is inherited down the hierarchy, so a list-only routine
  dir leaves every directory beneath it listable: `ls runs/` and a glob still see the names.
  What the boundary refuses is CONTENT, which is what the measurement was about. The only
  alternative Landlock offers is a routine dir that cannot be listed at all, breaking every
  `ls .` a jailed command makes — so an enumerate-then-open glob fails LOUDLY at the open
  (EACCES) rather than silently returning nothing.
* A run directory created AFTER the jail closed is not in it (a Landlock rule needs a live
  inode) — the same accepted cost the own-dir write seal carries.
* A granted fs root that CONTAINS the routine dir (`~/routines`, or the ROUTINE dir itself,
  which a child run holds as a root — F185/childrun.py) is mounted as one hierarchy, so the
  boundary cannot bind inside it. That is reported rather than left quietly untrue:
  `uncarvable`, and the `not_enforced_under` field of `note`.

The CURRENT run's own tree stays readable at every depth, exactly as the action gate has it:
the engine itself points the model there after compaction, and a child run's workspace lives
under `runs/<ts>/sub/<n>/`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

RUNS_DIR = "runs"

#: The depths an operator can set, weakest first (`grants.effective_settings`).
DEPTHS = ("none", "last", "all")


@dataclass(frozen=True)
class Scope:
    """The run-history facts one jail composition needs.

    `root` is the ROUTINE's own directory — never a child run's workspace, which is where
    `ctx.routine.dir` points inside a sub-run (F185). `current` is the ROOT run directory's
    name, whose tree stays readable at every depth.
    """

    root: Path
    depth: str = "last"
    current: str = ""


def scope_for_ctx(ctx) -> Scope | None:
    """The scope of one LIVE run, or None when the context carries no grants to read a depth
    from (the run-less and synthetic policies). Total about that, because a jail composition
    that raises costs the run every util (R2260).
    """
    grants = getattr(ctx, "grants", None)
    if grants is None:
        return None
    from .engine.reach import routine_root

    depth = getattr(grants, "run_history", "none")
    try:
        root = routine_root(ctx).resolve()
        current = ctx.root_run_dir.name
    except (AttributeError, OSError):
        # TOTAL about a context that is not a full live run (a partial stand-in, a synthetic
        # policy): a jail composition that raises costs the run EVERY util, including the ones
        # that open nothing (R2260). No scope means the pre-D207 own-dir mount, and the
        # ACTION gate still refuses the path.
        return None
    return Scope(root=root, depth=depth if depth in DEPTHS else "none", current=current)


def default_scope(own_dir: Path) -> Scope:
    """The scope for a jail whose policy carries a routine dir but NO run from which to read a
    depth — the daemon's admission jails (`daemon/gate_prepare.py`, the second door into
    `sandbox.wrap`), which are composed before a run exists.

    It FAILS CLOSED at `last`, the floor a routine's own policy always carries (D96), rather
    than at the routine's configured depth: the admission child has no `GrantPolicy` to read
    and a jail built on a guess should guess narrow. The cost is real and bounded — a routine
    at `runs: all` whose `scripts/admit.py` wanted an OLDER run than the last would be refused
    (none of the four admission scripts on the instance reads `runs/` at all, 2026-10-25) —
    and the alternative is the hole this whole change closes, in the door nobody tests.
    """
    return Scope(root=Path(own_dir).resolve(), depth="last")


def runs_dir(scope: Scope) -> Path:
    """The tree the depth governs: the ROUTINE's `runs/`, resolved."""
    return (scope.root / RUNS_DIR).resolve()


def _names(scope: Scope) -> list[str]:
    """Every run directory name under `runs/`, sorted — timestamps, so sorted is chronological.
    Total about a `runs/` that does not exist or cannot be listed (R2260).
    """
    try:
        return sorted(d.name for d in runs_dir(scope).iterdir() if d.is_dir())
    except OSError:
        return []


def last_prior(runs: Path, current: str) -> str | None:
    """The newest run directory under `runs` that is not `current` — what the 'last' depth
    means, for the ACTION gate (`engine/fileops._runs_read_gate`) and for the JAIL alike.

    One source for both enforcers, as `ownseal` does with the write gate's names: a second
    implementation kept in step by hand is how two gates drift, and a drift here would read as
    a permission bug on one path and a silent hole on the other.
    """
    try:
        prior = sorted(d.name for d in runs.iterdir() if d.is_dir() and d.name != current)
    except OSError:
        return None
    return prior[-1] if prior else None


def readable(scope: Scope) -> list[Path]:
    """The PRIOR run directories this depth may read — one read-only jail rule each.

    Empty at depth `none` (a sub-workflow child, a synthetic policy): a child's context is its
    brief, not the archive. The current run's own tree is NOT here — `current_dir` carries it,
    because it is readable at every depth including `none`.
    """
    if scope.depth == "all":
        names = [n for n in _names(scope) if n != scope.current]
    elif scope.depth == "last":
        last = last_prior(runs_dir(scope), scope.current)
        names = [last] if last else []
    else:
        names = []
    return [runs_dir(scope) / n for n in names]


def current_dir(scope: Scope) -> Path | None:
    """The current ROOT run's directory, when it exists — readable at every depth. It holds the
    run's status, its archived history and every child run's workspace, and the engine points
    the model there after compaction.
    """
    if not scope.current:
        return None
    d = runs_dir(scope) / scope.current
    return d if d.exists() else None


def refused(scope: Scope) -> list[str]:
    """The run directory names this depth withholds — the boundary's live cost, named."""
    allowed = {p.name for p in readable(scope)} | {scope.current}
    return [n for n in _names(scope) if n not in allowed]


def uncarvable(scope: Scope, roots: tuple[Path, ...]) -> list[str]:
    """Granted roots that CONTAIN the governed `runs/`, so the boundary cannot bind inside them.

    A root like `~/routines` is mounted as one hierarchy, and for a routine granted it every
    OTHER routine's `runs/` is readable by that grant anyway — which the depth never governed.
    Carving it would mean enumerating the whole routines home. So the mount is left alone and
    the fact is REPORTED: the operator asked for a boundary with its cost named, not for a
    green gate that cannot say what it would refuse.
    """
    governed = runs_dir(scope)
    out = []
    for root in roots:
        resolved = Path(root).resolve()
        if governed.is_relative_to(resolved) and resolved != governed:
            out.append(str(resolved))
    return sorted(set(out))


def read_gate(ctx, resolved) -> str | None:
    """The ACTION layer's refusal for a previous-run path, or None when the read is allowed —
    the FIRST of the two enforcers (`engine/fileops.do_read_file` calls it; `GrantPolicy.deny`
    handles the relative-path form inside the schema-retry cycle, so this catches absolute
    paths and scopes `runs: last`).

    It lives here, beside the jail's carve, because the two used to disagree: this gate refused
    an older `runs/<ts>/gate.json` while a util handed the same absolute path opened it (D207).
    Both now read one depth and one `last_prior`.

    The current run's own tree — status, archived history, a child's own workspace — is always
    readable. Anchored on the ROUTINE's runs/: a child (run history `none`) reaches it through
    the parent's tree, and a gate on its own workspace's runs/ never saw that.
    """
    g = getattr(ctx, "grants", None)
    if g is None:
        return None
    from .engine.reach import routine_root

    runs = (routine_root(ctx).resolve() / RUNS_DIR).resolve()
    try:
        rel = resolved.relative_to(runs)
    except ValueError:
        return None
    if resolved.is_relative_to(ctx.root_run_dir.resolve()):
        return None
    if g.run_history == "none":
        # after D96 a routine's own policy always carries at least "last" — this branch
        # serves scopes genuinely without history (sub-workflow children, synthetic
        # policies), so the copy must not claim the ROUTINE lacks a permission (R46).
        return ("previous runs are not readable in this scope — a routine reads its own "
                "last run by default, but sub-workflows run without run history; fold "
                "what the child needs into its brief instead")
    if g.run_history == "last":
        last = last_prior(runs, ctx.root_run_dir.name)
        if not rel.parts or rel.parts[0] != last:
            return (f"previous runs are readable at the default 'last' depth only "
                    f"({'runs/' + last if last else 'none exists yet'}); the routine's "
                    f"run-history setting at 'all' opens them for longitudinal work — the "
                    f"operator's choice")
    return None


def note(scope: Scope | None, roots: tuple[Path, ...] = ()) -> dict:
    """The observation field naming what the jail withheld from THIS call — {} when the
    boundary refuses nothing (a routine at depth `all`, or a first run).

    A refusal must TEACH: a util handed a deeper path meets a bare kernel permission error,
    which says nothing about why — so the engine says it instead, naming the depth, what IS
    readable, and that the depth is the operator's setting rather than a defect to work around.
    """
    if scope is None or not (held := refused(scope)):
        return {}
    open_roots = uncarvable(scope, roots)
    text = (
        f"this routine's run-history depth is '{scope.depth}', so the jail mounts only the run "
        f"directories that depth allows: a util, script or shell command handed a path under "
        f"runs/ outside them cannot open it, and meets a kernel permission error rather than a "
        f"readable refusal (Landlock has no deny rule, so the depth is enforced by mounting "
        f"the permitted run dirs and nothing else — the names stay listable, the contents do "
        f"not). The current run's own tree is always readable. The depth is the operator's "
        f"setting (`capabilities.runs`: none | last | all) — if longitudinal work genuinely "
        f"needs the archive, ask for 'all' with a deferred ask_user instead of reaching around "
        f"it.")
    if open_roots:
        text += (f" NOTE: the granted root(s) {', '.join(open_roots)} contain this routine's "
                 f"directory, and a root is mounted as one hierarchy — inside them the depth "
                 f"does NOT bind.")
    out = {"run_depth": {
        "depth": scope.depth,
        "readable_runs": sorted({*(p.name for p in readable(scope)), scope.current}),
        "refused_runs": len(held),
        "note": text}}
    if open_roots:
        out["run_depth"]["not_enforced_under"] = open_roots
    return out
