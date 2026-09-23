"""The setup surface's DECLARED NEEDS — what the docs, rules and util headers a routine
holds say it must have, checked against what it actually has.

Three joins, one per kind of declaration: a util header's `secrets:` (the hard edge — the
call fails without them), a util header's `fs:` private store (hard too: a declaration
narrows, it never asks), and a doc's or rule's `expects:` (the SOFT edge — prose that
presumes a binding). Split out of `surface.py` at 663 lines; the row vocabulary they all
emit is `surface_nodes`.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from .surface_nodes import BLOCKS, INTERRUPTS, OK, _covered, _node

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..config.routine import RoutineConfig

def _secret_nodes(cfg: RoutineConfig, needed: dict[str, list[str]],
                  store_keys: set[str]) -> list[dict]:
    """A declared secret is a dependency of whoever declares it. Four outcomes, whose
    distinction is the whole point: absent from the store BLOCKS, undecided INTERRUPTS (one
    blocking access request at the first call), denied forever BLOCKS.

    Two families are NOT store secrets and must not be reported as missing from it: the machine
    vars the engine injects from a binding, plus a connection's `<PROVIDER>_ACCESS_TOKEN`. Their
    real dependency is the binding, which the expects: join already covers.
    """
    from ..machines import machine_env_vars

    out = []
    # Names the ENGINE supplies per run, not the store: reporting them as missing would send
    # the operator looking for a secret to add that nothing can add.
    engine_injected = machine_env_vars() | {"RSCHED_ROUTINE", "RSCHED_API_TOKEN"}
    grants = cfg.grants or {}
    for name in sorted(needed):
        if name in engine_injected or name.endswith("_ACCESS_TOKEN"):
            continue
        eid = f"secret:{name}"
        why = "needed by " + ", ".join(sorted(needed[name]))
        src = {"utils": sorted(needed[name])}
        decided = grants.get(eid)
        if name not in store_keys:
            out.append(_node(eid, "absent", BLOCKS, why,
                             "not in the secrets store — the call runs without it",
                             {"kind": "add_secret", "name": name}, src))
        elif decided is False:
            out.append(_node(eid, "denied", BLOCKS, why,
                             "declined forever — the run no longer asks and the call fails",
                             {"kind": "clear_grant", "entity": eid}, src))
        elif decided is True:
            out.append(_node(eid, "exposed", OK, why, source=src))
        else:
            out.append(_node(eid, "undecided", INTERRUPTS, why,
                             "the first call declaring it stops the run to ask you",
                             {"kind": "grant", "entity": eid}, src))
    return out


def _fs_nodes(cfg: RoutineConfig, needed: list[tuple[str, str, str, str]]) -> list[dict]:
    """`needed` is (mode, path, why). A private store a util declares is only reachable when a
    granted root covers it — the declaration narrows, it never asks — so an uncovered one is a
    hard block, not a prompt.

    `$VAR` resolves exactly as `sandbox.wrap` resolves it (daemon environment, never the run's);
    an UNSET variable names no path at all, so it is skipped rather than reported: the
    messengers declare both `$X_SESSION_DIR` and its literal default; only one of the two
    ever resolves.
    """
    import os

    out = []
    write_roots = [Path(p) for p in cfg.fs_write_roots or []]
    read_roots = [Path(p) for p in cfg.fs_read_roots or []]
    seen: set[str] = set()
    for mode, declared, why, util in needed:
        raw = os.path.expandvars(declared)
        if "$" in raw or raw in seen:
            continue
        seen.add(raw)
        path = Path(raw).expanduser()
        # The declaration's own vocabulary is rw/ro; a root is granted as read or write. The
        # fix speaks the granting side, because that is the thing the operator has to do.
        axis = "write" if mode == "rw" else "read"
        eid = f"fs-{axis}:{raw}"
        ok = (_covered(path, write_roots) if mode == "rw"
              else _covered(path, write_roots + read_roots))
        src = {"utils": [util]}
        if ok:
            out.append(_node(eid, "granted", OK, why, source=src))
        else:
            out.append(_node(eid, "missing", BLOCKS, why,
                             "no granted root covers it — the util cannot reach it",
                             {"kind": "add_root", "mode": axis, "path": raw}, src))
    return out


def _expects_nodes(cfg: RoutineConfig, expects: dict[str, dict],
                   machine_catalog: dict) -> list[dict]:
    """The SOFT edge: entities a doc's or a rule's prose presumes. `"*"` means "at least one of
    this class" — the prose explaining which one lives in the doc body, never in the key.
    """
    out: list[dict] = []
    write_roots = [Path(p) for p in cfg.fs_write_roots or []]
    read_roots = [Path(p) for p in cfg.fs_read_roots or []]
    for slug, mapping in sorted(expects.items()):
        for cls, names in sorted(mapping.items()):
            for name in names:
                eid = f"{cls}:{name}"
                why = f"{slug} expects it"
                src = {"doc": slug}
                if cls == "machine":
                    bound = list(cfg.machines or [])
                    if name == "*":
                        ok, detail = bool(bound), "no machine is bound to this routine"
                    else:
                        ok, detail = name in bound, f"machine {name!r} is not bound"
                    if not ok and not machine_catalog:
                        detail += " (and the machine catalog is empty)"
                    out.append(_node(eid, "bound" if ok else "missing", OK if ok else INTERRUPTS,
                                     why, "" if ok else detail + " — every call returns nothing",
                                     {} if ok else {"kind": "bind_machine", "name": name}, src))
                elif cls in ("fs-write", "fs-read"):
                    roots = write_roots if cls == "fs-write" else write_roots + read_roots
                    ok = bool(roots) if name == "*" else _covered(Path(name), roots)
                    # `"*"` asks for a root, not for THAT root, so the fix names no path and
                    # the panel offers the empty field rather than a path nobody wrote down.
                    axis = "write" if cls == "fs-write" else "read"
                    out.append(_node(eid, "granted" if ok else "missing",
                                     OK if ok else INTERRUPTS, why,
                                     "" if ok else "the routine has no root the prose can use",
                                     {} if ok else {"kind": "add_root", "mode": axis,
                                                    "path": "" if name == "*" else name}, src))
                elif cls == "connection":
                    ok = bool(cfg.connections) if name == "*" else name in (cfg.connections or {})
                    out.append(_node(eid, "bound" if ok else "missing",
                                     OK if ok else INTERRUPTS, why,
                                     "" if ok else "no account is bound for it",
                                     {} if ok else {"kind": "bind_connection", "provider": name},
                                     src))
                # secret: expectations are covered by the util-header join, which is the
                # authority — re-deriving them here would be a second copy that can drift.
    return out
