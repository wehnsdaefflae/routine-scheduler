"""Which files of a run the console may SERVE — one predicate, used by the chip and the route.

A run's file-activity card lists every path the run read or wrote, and each row used to be
rendered as a clickable chip. The route behind it (`api_runs.run_file`) served two trees — the
run dir and its owning routine dir — so a file the run READ from a granted fs root was listed
with a click that answered 400: a chip the console could never honour. The operator's ruling on
D172 (2026-10-08):

    How can the file that is read be outside the routine's permitted paths? If the routine can
    access it, the Web UI should also be able to

The premise is sound even though the two readers are not the same process: a routine's own read
happens inside the Landlock child jail, rebuilt per dispatch from its grants, while the daemon
serving the console is unsandboxed and refuses a path outside the routine's declared roots as a
POLICY choice. So the boundary moves to where the operator put it — a path the routine was
permitted to read is servable to him — and the policy is still a boundary, not an absence of one.

## Two properties this module exists to keep

**The chip and the route agree.** They are the same function here. A check hardened on one and
not the other is a hole, or a dead chip, that nobody sees; that is the defect D172 reported.

**Resolved at SERVE TIME, never recorded.** The roots come from the routine's live
`routine.yaml` on every call, so revoking a grant stops serving at once — a verdict stored in
the transcript when the file was touched would outlive the grant that justified it.

Write roots are not read roots: D172 widens READ roots only. A path under a write-only grant
stays unserved. Containment is still proven on the opened descriptor (`artifacts.open_within`),
which takes this same root list — a path test before the open can be raced by a run's own util.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path


def roots_for_run(server, run_dir: Path, routine_dir: Path) -> tuple[Path, ...]:
    """Every tree this run's files may be served from, widest-intent first: the routine dir,
    the run dir, then the routine's EFFECTIVE READ ROOTS — its configured `fs_read_roots` plus
    the instance-wide shared read-only assets, the same expression the sandbox and the gate
    admission compile (`sandbox.policy_for_ctx`, `daemon/gate_prepare`).

    Read fresh on every call (see the module docstring). A routine whose config will not parse
    contributes no roots rather than raising: the two tree roots still serve, which is exactly
    the behaviour before D172.

    A run's ONE-TIME fs grants are deliberately absent — they live on the live `RunContext` in
    memory and are gone once the run ends, so no later call could resolve them honestly.
    """
    roots = [routine_dir, run_dir]
    try:
        from ..config import load_routine
        from ..sandbox import _shared_read_roots

        cfg, _problems = load_routine(routine_dir)
        granted = list(cfg.fs_read_roots) if cfg is not None else []
        granted += list(_shared_read_roots(server))
    except Exception:                        # a config we cannot read grants nothing
        granted = []
    for root in granted:
        try:
            resolved = Path(root).resolve()
        except OSError:
            continue
        if resolved.is_dir() and resolved not in roots:
            roots.append(resolved)
    return tuple(roots)


def servable(resolved: Path, roots: Sequence[Path]) -> bool:
    """Whether an already-resolved path lies inside one of `roots`.

    `paths.within` is reached THROUGH its module on every call, never bound at import: the
    symlink-race suite proves this route's containment by wrapping `paths.within` so the swap
    lands the moment the check passes (tests/test_served_file_containment.py), and a
    from-import here would quietly escape that wrapper — the check would still pass, the test
    would still be green, and the race would be open again.
    """
    from .. import paths

    return any(paths.within(root, resolved) for root in roots)


def servable_rel(path: str, run_dir: Path, roots: Sequence[Path],
                 bases: Sequence[str] = ()) -> bool:
    """Whether `run_file` would open the file a card ROW names — the chip's predicate.

    It tries the same candidates in the same order as the route: the bases the read model
    recorded for a relative path (a child's working dir, `sub/<n>`), then each root. True only
    when some candidate is a real file inside a root; a row whose file is gone gets no chip
    either, because the click would 404.
    """
    if not path:
        return False
    rel = Path(path)
    candidates = ([rel] if rel.is_absolute()
                  else [run_dir / b / rel for b in bases if b] + [root / rel for root in roots])
    for cand in candidates:
        try:
            resolved = cand.resolve()
        except OSError:
            continue
        if servable(resolved, roots) and resolved.is_file():
            return True
    return False
