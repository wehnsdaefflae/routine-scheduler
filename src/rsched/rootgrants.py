"""What a FOLDER GRANT may be — the one judgement every edge that makes or proposes one asks.

A folder-grant list (`fs_read_roots` / `fs_write_roots`, or a conversation's `workdir` — write
root #1) is judged here and nowhere else. Two kinds of caller reach it, and they used to hold
two answers:

- the GRANT edges — the routine PATCH, the conversation PATCH and the conversation create form —
  through `web/config_fields.validate_roots`, which turns the refusal into the 400 the page shows;
- the PROPOSAL judge (`patterns/validate.problem`), which every writer of a pending change calls.
  Until 0.562.0 it judged roots by the patch model's `list[str]` alone, so a creation draft
  proposing `fs_write_roots: ["work"]` was stored and refused on the operator's accept click —
  and the accept is whole-draft, so every other kept change fell with it.

Pure: it raises `RootGrantError` (a `ValueError`) carrying the sentence the person reads, and
returns the value to write. No HTTP, no file read — which is what lets both callers share it.
"""

from __future__ import annotations

from . import entities
from .paths import expand


class RootGrantError(ValueError):
    """A folder-grant list the accept would refuse; `str()` is the sentence naming why."""


def normalize_roots(key: str, values: object, *, current: list | None = None) -> list[str]:
    """One folder-grant list, REPLACED wholesale.

    Each entry is stripped of whitespace and a trailing `/`, deduplicated, and returned as the
    raw string the file holds (`~` kept, never expanded here). Refused, naming it:

    - a path that is not ABSOLUTE once `~`/`$VARS` expand — a relative root would resolve
      against the daemon's working directory, which no operator means;
    - a credential store (SEC-1, `entities.guarded_roots`): the credential FILES in the config
      dir, `~/.credentials` and `~/.ssh` are never grantable, and the never-grantable promise
      lived only on the runtime ask path until a typed root mounted the instance's credential
      dir on a routine. Since D169 the guard names those files rather than the directory, so
      `config.yaml` — ordinary settings, no credential — is clean on its own and needs no
      allowance; the directory is still refused for CONTAINING them.
      A store ALREADY in a file is the loader's to report, never dropped (config/routine.py).
      `current` is what the routine holds for this key RIGHT NOW (the two PATCH edges have the
      file in hand): a guarded value already there, or strictly inside one that is, does not widen
      what the routine reaches — and narrowing is exactly what that advisory asks for — so it is
      accepted; see `already_held_or_narrower`. An edge that is MAKING or PROPOSING the grant
      passes no `current`, so nothing new is ever grantable — and no proposal ever re-asserts a
      store the loader is asking the operator to narrow.

    The edges used to hold subsets of this (the routine PATCH no absolute check, the
    conversation create form its own, the proposal judge none), and a check that exists on one
    edge is a check a grant can route around.
    """
    vals = values or []
    if not isinstance(vals, list) or any(not isinstance(p, str) or not p.strip() for p in vals):
        raise RootGrantError(f"{key}: must be a list of non-empty path strings")
    roots: list[str] = []
    for raw in vals:
        root = raw.strip().rstrip("/") or "/"
        if not is_absolute(root):
            raise RootGrantError(
                f"{key}: {raw!r} is not an absolute path (use /abs/path or ~/path)")
        if root not in roots:
            roots.append(root)
    if guarded := [g for g in entities.guarded_roots(roots)
                   if not already_held_or_narrower(g, current)]:
        raise RootGrantError(f"{key}: {', '.join(guarded)} {entities.GUARDED_ROOT_REASON}")
    return roots


def is_absolute(root: str) -> bool:
    """Is `root` absolute once `~` and `$VARS` expand? `~name` for an account that does not
    exist is not — it names nothing a run could reach.
    """
    try:
        return expand(root).is_absolute()
    except RuntimeError:
        return False


def already_held_or_narrower(value: str, current: list | None) -> bool:
    """Is `value` a root the routine ALREADY holds, or one strictly inside it (F582)?

    The guard's own advisory asks an operator to narrow a credential store a routine already
    reads — and this edge refused the narrower path, so the only fix it offered could not be
    applied (D159 sat at its resolution for exactly this reason). A value already granted, or
    under one already granted, does not increase what the run reaches; every other direction is
    unchanged, so this allows nothing a routine does not already have:

    - **equal to a held root** → allowed, because nothing changes. Without this an unrelated edit
      to the same list (adding `~/routines` beside it) is refused for a grant that is already in
      the file — the file the loader itself reports and keeps.
    - **strictly inside a held root** → allowed: this is the NARROWING the advisory asks for.
    - no `current` at all (the conversation CREATE form, the composer, a settings pattern, a
      pending change) → every guarded value is a NEW grant and stays refused.
    - a value CONTAINING a held root (a widening back to the store, or up to `~/.config`) → the
      held root is not among the value's ancestors and is not equal to it, so it is refused.

    Paths are compared RESOLVED (`~` and `$VARS` expanded), because a file may spell the same
    store either way — the loader's own comparison does the same.
    """
    try:
        target = expand(value)
    except RuntimeError:
        return False
    for raw in current or []:
        if not isinstance(raw, str) or not raw.strip():
            continue
        try:
            held = expand(raw.strip().rstrip("/") or "/")
        except RuntimeError:
            continue
        if target == held or held in target.parents:
            return True
    return False
