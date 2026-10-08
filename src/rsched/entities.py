"""The grant-entity vocabulary — ONE namespaced id grammar for everything a run can be
granted: `<class>:<name>`. It spans the capability layer (gated action kinds, reserved
utils and verbs, run-history depth, the reminder dial) AND the resource layer (secrets,
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
           "fs-read", "fs-write", "runs", "reminders", "recreate")
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
TURN_ACTION_CLASSES = frozenset({"action", "util", "runs"})
# D76 (operator, 2026-08-06, revisiting the D65 scope choice): secret:/fs-read:/fs-write:
# ARE once-grantable, under the explicitly COARSER spend the operator approved ("spent at
# the next requesting util invocation"). Their use happens inside a util subprocess
# (declared-env injection, sandbox-mounted roots), so the engine spends them at the next
# successfully-dispatched action that RECEIVES the entity: a secret at the next util call
# whose script (or its `calls:` tree — utils_run.util_needs) declares the var; an fs root
# at the next file action under it OR the next util invocation (every util's sandbox
# mounts the granted roots wholesale). connection:/machine: stay four-state — binding
# carries an account/host no single action "uses up"; recreate: is a per-run unlock.
ONCE_CLASSES = TURN_ACTION_CLASSES | frozenset({"secret", "fs-read", "fs-write"})
# grants: TRUE rows are legal only where no native routine.yaml switch exists.
TRUE_ROW_CLASSES = frozenset({"secret"})
# fs paths that are never grantable, whatever the user clicks: the instance's credential
# stores (docs/sandboxing.md keeps them invisible even to fully-granted utils). Enforced at
# every door a grant comes through, because one was not enough — a run's access REQUEST
# (engine/availability.py); every config edge, routine and conversation alike
# (web/config_fields.validate_roots); a settings PATTERN, whose roots creation copies
# (patterns/store.problems, patterns/apply.routine_yaml); the jail assembler, for a root that
# reaches a store only through a link planted after approval (sandbox.wrap); and the config
# LOADER (config/routine.py, which REPORTS one already in a file rather than dropping it: a
# root a routine has been running on for months disappears from under its next run otherwise).
#
# D169 (operator, 2026-10-08, answer A) narrowed this from the config DIRECTORY to the
# credential FILES inside it. The guard refuses a path that is a store or CONTAINS one, so
# naming the directory made `~/.config/routine-scheduler/config.yaml` a guarded root too —
# and config.yaml holds no credential: it is the instance's ordinary settings file, which the
# routines whose job is auditing this server's configuration read. The advisory asked those
# routines to narrow their grant to exactly that file and then refused the narrower path, so
# the one wording every door speaks was false about the door it was written for (F635; D159's
# earlier narrowing did not silence the advisory it was chosen to silence).
#
# What is guarded is therefore each credential STORE by name — and a grant on the config dir
# itself still names `secrets.env` and the rest by containment, so nothing was opened up:
# the directory remains ungrantable because it CONTAINS these entries.
#
# The entries inside the config dir are named by the modules that OWN them, never spelled
# again here: a credential file whose name is only a literal in this tuple becomes silently
# GRANTABLE the day its owner renames it, which is the one failure a narrowed guard can have
# that the wide one could not.
CONFIG_DIR_CREDENTIALS = (
    "secrets.env",            # secrets.SECRETS_FILE — the central store + the console token
    "secrets.d",              # secrets.SCOPED_DIR — one <slug>.env per routine (D103)
    "connections.json",       # oauth.store.CONNECTIONS_FILE — OAuth refresh tokens
    "vapid-private.pem",      # web.push._VAPID_FILE — the push signing key
    ".mounts",                # machine_mounts — per-machine ssh keys + known_hosts
)
#: Credential stores outside the instance config dir.
HOME_CREDENTIALS = ("~/.credentials", "~/.ssh")
#: The same credential entries at the DEFAULT config location, guarded whatever RSCHED_CONFIG
#: says — a routine.yaml may name them verbatim (see `never_grantable_stores`).
DEFAULT_CONFIG_CREDENTIALS = tuple(f"~/.config/routine-scheduler/{n}"
                                   for n in CONFIG_DIR_CREDENTIALS)


def _config_dir_credentials() -> tuple[str, ...]:
    """The credential entries of the config dir this instance actually loaded.

    Resolved through `paths.config_file()` rather than the default spelling, because
    `RSCHED_CONFIG` may move the dir — the guard has to name the files that exist, not the
    files that would exist at the default location.
    """
    from .paths import config_file
    base = config_file().parent
    return tuple(str(base / name) for name in CONFIG_DIR_CREDENTIALS)


def never_grantable_stores() -> tuple[str, ...]:
    """Every path no grant may open: the credential entries of the config dir this instance
    actually LOADED, plus `NEVER_GRANTABLE` (the same entries at the default location, and the
    home-level stores).

    A function rather than a constant, for two reasons:

    - `RSCHED_CONFIG` can move the config dir, and the guard has to name the files that exist,
      not the files that would exist at the default spelling;
    - `NEVER_GRANTABLE` is read at CALL time, so patching that constant still reaches every
      enforcement door (the jail-assembler tests do exactly that to point the guard at a
      temporary store).

    Both spellings are guarded, and that is not belt-and-braces: a routine.yaml — hand-written,
    copied from another box, or carried over from before a move — may still name
    `~/.config/routine-scheduler/secrets.env` verbatim, and guarding only the loaded location
    would hand a grant on the default one straight through. Guarding a path that holds nothing
    on this instance costs nobody anything; the reverse is a credential leak.
    """
    return (*_config_dir_credentials(), *NEVER_GRANTABLE)


#: Kept as the module's declarative answer for readers and for the tests that patch it; the
#: enforcement path calls `never_grantable_stores()` so a relocated config dir is honoured.
NEVER_GRANTABLE = (*DEFAULT_CONFIG_CREDENTIALS, *HOME_CREDENTIALS)

_LEVELS = {"runs": ("last", "all"), "reminders": ("local", "global")}


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
    if cls == "util":
        from .grants import is_util_entry
        if not is_util_entry(name) or name.endswith(":"):
            return None
    if cls in ("recreate", "connection") and not is_slug(name):
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


def _spellings(path: str | Path) -> set[Path]:
    """The path as written (expanded) and as the kernel opens it (symlinks resolved).

    Both, because a grant is compiled into a Landlock rule by OPENING the root
    (`landlock.py`), which follows symlinks: a root at `/tmp/x` linking to `~/.ssh` mounts
    `~/.ssh`. A loop or an unreadable component yields only the written form — such a root
    cannot be opened, so it mounts nothing to guard.
    """
    p = expand(path)
    try:
        return {p, p.resolve()}
    except (OSError, RuntimeError):
        return {p}


def never_grantable_fs(path: str | Path) -> bool:
    """True when the path lies in (or contains) a credential store no grant may open —
    compared as written AND as resolved, on both sides: a symlink to a store (or a store
    that is itself a symlink) is the store.
    """
    return any(_touches_store(p) for p in _spellings(path))


def _touches_store(p: Path) -> bool:
    # `never_grantable_stores()`, not the NEVER_GRANTABLE constant: the config dir may be
    # moved by RSCHED_CONFIG, and the guard must name the files that actually hold this
    # instance's credentials. `g in p.parents` is what keeps the config DIRECTORY ungrantable
    # after D169's narrowing — it contains these entries, so a grant on it still contains a
    # store, while a sibling that holds no credential (config.yaml) passes clean.
    guarded = {g for store in never_grantable_stores() for g in _spellings(store)}
    return any(p == g or p in g.parents or g in p.parents for g in guarded)


def reaches_store_only_through_a_link(path: str | Path) -> bool:
    """True when the path's WRITTEN form names no credential store but the path the kernel
    would open does — a symlink aiming a grant at a store.

    The jail assembler's question (`sandbox.wrap`), and deliberately narrower than
    `never_grantable_fs`: a root that names a store openly is a config the loader REPORTS
    and keeps (config/routine.py — two live routines audit the server's own config as their
    job), while one that reaches it only through a link was approved as something else.
    """
    return not _touches_store(expand(path)) and never_grantable_fs(path)


#: Why a guarded root is refused, in the ONE wording every enforcer uses — the PATCH that
#: rejects it, the loader that reports it, and the setup surface that keeps shouting about
#: one already in a file. The promise used to be true only of the ASK path
#: (`engine/availability.py`, the only caller of `never_grantable_fs`), so the docstring above
#: was false for exactly the door SEC-1 came through: an operator typing the path into the
#: routine page's Filesystem-roots panel.
GUARDED_ROOT_REASON = (
    "is an instance credential store. The credential files in the config dir (secrets.env, "
    "secrets.d/, connections.json, the push key, .mounts/), ~/.credentials and ~/.ssh are "
    "never grantable to a routine by design: a run holding one reads every other routine's "
    "secrets and the operator's own token (docs/sandboxing.md). The config dir ITSELF is "
    "refused because it contains them — grant the specific file you need instead, such as "
    "config.yaml, which holds no credential")


def guarded_roots(paths: object) -> list[str]:
    """The entries of a folder-grant list that name a credential store — empty for a clean
    list. Takes the raw strings a routine.yaml or a PATCH carries, `~` and all.
    """
    if not isinstance(paths, (list, tuple)):
        return []
    return [str(p) for p in paths if isinstance(p, (str, Path)) and never_grantable_fs(p)]


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
