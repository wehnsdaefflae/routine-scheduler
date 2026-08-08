"""The grant-entity vocabulary — ONE namespaced id grammar for everything a run can be
granted: `<class>:<name>`. It spans the capability layer (gated action kinds, reserved
utils, run-history depth, workflow generation) AND the resource layer (secrets,
connections, machines, filesystem roots) plus the recreate unlock, so every access
request, denial tombstone and one-run grant speaks the same language.

Four decision states per entity (docs/rules-permissions.md):
- allowed forever  — the NATIVE routine.yaml key (capability via the permission cascade,
  binding present, fs root listed). `secret:` is the one class with no native switch:
  its allow-forever is a `grants:` true row.
- denied forever   — a `grants:` false row (the universal tombstone; asks are suppressed).
- allowed/denied now — run-scoped, in-memory on the RunContext (a resumed leg re-asks).

Deliberately NOT entities — structurally impossible stays impossible, never a deniable
row: routine.yaml writes, runs/ writes, .memory/ via file actions, own-recipe writes,
base action kinds, general rules (library prose, read-only to every run), conduct docs
(they ride the permission cascade; they grant nothing).
"""

from __future__ import annotations

from pathlib import Path

from .ids import is_slug
from .paths import expand
from .secrets import KEY_RE

# class → is the NAME valid for it (shape only — instance existence is checked at request
# time against the live vocabularies: library requires, provider registry, machine catalog,
# secrets store).
CLASSES = ("action", "util", "secret", "connection", "machine",
           "fs-read", "fs-write", "runs", "workflows", "recreate")
# Resource-class entities flow to child tasks (children inherit their parent's resources);
# capability-class ones are top-level-only (sub-workflows run with capabilities off).
RESOURCE_CLASSES = frozenset({"secret", "connection", "machine", "fs-read", "fs-write"})
# `recreate:` never offers "allow forever": a fresh user deletion must always outrank an
# old grant, so recreating a deleted util is decided per run (or tombstoned).
NO_FOREVER_CLASSES = frozenset({"recreate"})
# Classes whose USE the engine observes as a TURN ACTION (validate_action sees the
# consuming call), so `allow once (this action only)` is an exact promise: the next
# successfully-dispatched matching action spends it, then the engine revokes it (D65,
# operator decision 2026-08-05: turn-action classes ONLY). secret:/fs-read:/fs-write:
# are consumed inside a util SUBPROCESS the engine never sees as a turn — "once" for
# them could only mean "the next util call that touches it", a coarser promise than the
# button makes — so they stayed four-state until D76
# (below) accepted the coarser promise.
TURN_ACTION_CLASSES = frozenset({"action", "util", "runs", "workflows"})
# D76 (operator, 2026-08-06, revisiting the D65 scope choice): secret:/fs-read:/fs-write:
# ARE once-grantable, under the explicitly COARSER spend the operator approved ("spent at
# the next requesting util invocation"). Their use happens inside a util subprocess
# (declared-env injection, sandbox-mounted roots), so the engine spends them at the next
# successfully-dispatched action that RECEIVES the entity: a secret at the next util call
# whose script (or its `calls:` tree — utils_lib.util_needs) declares the var; an fs root
# at the next file action under it OR the next util invocation (every util's sandbox
# mounts the granted roots wholesale). connection:/machine: stay four-state — binding
# carries an account/host no single action "uses up"; recreate: is a per-run unlock.
ONCE_CLASSES = TURN_ACTION_CLASSES | frozenset({"secret", "fs-read", "fs-write"})
# grants: TRUE rows are legal only where no native routine.yaml switch exists.
TRUE_ROW_CLASSES = frozenset({"secret"})
# fs paths that are never grantable, whatever the user clicks: the instance's credential
# stores (docs/sandboxing.md keeps them invisible even to fully-granted utils).
NEVER_GRANTABLE = ("~/.config/routine-scheduler", "~/.credentials", "~/.ssh")

_LEVELS = {"runs": ("last", "all"), "workflows": ("generate",)}


def parse_entity(eid: object) -> tuple[str, str] | None:
    """`(class, name)` for a well-shaped entity id, else None. Shape only — no registry
    lookups (those are contextual, at request time). fs-* names are canonicalized to an
    absolute expanded path so the same directory always yields the same id.
    """
    if not isinstance(eid, str) or ":" not in eid:
        return None
    cls, _, name = eid.partition(":")
    name = name.strip()
    if cls not in CLASSES or not name:
        return None
    if cls in ("util", "recreate", "connection") and not is_slug(name):
        return None
    if cls == "action":
        from .grants import GATED_KINDS
        if name not in GATED_KINDS:
            return None
    if cls == "secret" and not KEY_RE.match(name):
        return None
    if cls in _LEVELS and name not in _LEVELS[cls]:
        return None
    if cls.startswith("fs-"):
        name = str(expand(name))
    return cls, name


def canonical(eid: str) -> str:
    """The id in canonical form (fs paths expanded/absolute) — parse_entity must accept it."""
    parsed = parse_entity(eid)
    if parsed is None:
        raise ValueError(f"not a grant entity id: {eid!r}")
    return f"{parsed[0]}:{parsed[1]}"


def never_grantable_fs(path: str | Path) -> bool:
    """True when the path lies in (or contains) a credential store no grant may open."""
    p = expand(path)
    for guarded in (expand(g) for g in NEVER_GRANTABLE):
        if p == guarded or p in guarded.parents or guarded in p.parents:
            return True
    return False


def is_resource(eid: str) -> bool:
    parsed = parse_entity(eid)
    return parsed is not None and parsed[0] in RESOURCE_CLASSES


def normalize_grants(raw: object) -> tuple[dict[str, bool], list[str]]:
    """Validate + canonicalize a routine.yaml `grants:` mapping (entity id → bool).
    Invalid rows are dropped and reported, mirroring normalize_capabilities: a bad edit
    degrades one row, never a run. TRUE rows are legal only for TRUE_ROW_CLASSES — every
    other class's allow-forever lives in its native config key, and a stray true row here
    would be a second, conflicting authority.
    """
    if raw is None:
        return {}, []
    if not isinstance(raw, dict):
        return {}, ["grants must be a mapping of entity id (class:name) → true/false"]
    out: dict[str, bool] = {}
    problems: list[str] = []
    for key, val in raw.items():
        parsed = parse_entity(key)
        if parsed is None:
            problems.append(f"grants: {key!r} is not an entity id "
                            f"(<class>:<name>, classes: {', '.join(CLASSES)})")
            continue
        if not isinstance(val, bool):
            problems.append(f"grants.{key}: must be true (allowed) or false (denied forever)")
            continue
        cls, name = parsed
        if val and cls not in TRUE_ROW_CLASSES:
            problems.append(f"grants.{key}: a true row is only valid for secret:* — "
                            f"'{cls}' grants live in their native routine.yaml key")
            continue
        out[f"{cls}:{name}"] = val
    return out, problems
