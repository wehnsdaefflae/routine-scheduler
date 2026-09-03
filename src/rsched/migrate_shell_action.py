"""MIGRATION(expires=2026-10-03): `shell` becomes an action kind, not a reserved util (0.287.0).

Until 0.287.0 the escape hatch was a reserved global util at `<library>/utils/shell/`, unlocked
by the `shell` permission's `requires: utils: [shell]`. It is now the `shell` ACTION KIND, gated
like every other gated kind — which is the whole point of the move: `capabilities.utils` is an
EXCEPTION list (only a handful of the library's utils are gated at all), while a gated kind is
projected out of the schema a run is sent, so a routine without the capability cannot even
GENERATE the call.

Four things on a live instance still say the old thing, and none of them converge on their own:

1. Every holder's `routine.yaml` names `shell` under `capabilities.utils`. Left alone the entry
   would gate nothing (no util by that name exists any more) and the ACTION would stay off — 14
   routines silently losing the hatch their permission says they hold.
2. A GROUP's shared config block says the same thing for its members (D82 — a group's config is
   a LIVE layer, unlike a settings template's one-shot copy), and it lives in `groups.json`, not
   in any routine dir. The live instance has one such group (FAU, 4 members): converting only
   the routine files left all four holding the `shell` permission with no capability behind it,
   which `rsched validate` reports as a fail-closed permission — found by running it against the
   real instance after the first deploy, which is why that check is not optional.
3. The live library's `permissions/shell.md` still declares `requires: utils: [shell]` AND still
   tells its holders, in prose the prompt inlines, to call `util` with name `shell`. The seed
   sync only ADDS missing docs, so a seed edit never reaches a live library; without this the doc
   reserves a util that does not exist, asks for no action (so the save-time floor would strip
   `shell` back out of every mapping the moment anyone touched a permissions panel), and teaches
   14 routines a call the engine now rejects. The whole doc is replaced by the seed's, because
   the BODY is what a run reads.
4. The util itself is still installed. `sync_seed_utils` will not re-add it (it is gone from
   util-seed, and the library's git history then records the deletion), but the copy already on
   disk would keep answering for anyone holding a stale grant.

Runs once at daemon boot, then gets deleted (delete-after-convergence — CLAUDE.md).
"""

from __future__ import annotations

import logging
import shutil

from . import libgit
from .library_docs import parse_lenient
from .paths import (
    atomic_write,
    atomic_write_json,
    atomic_write_yaml,
    read_json,
    read_yaml,
    repo_root,
)

log = logging.getLogger("rsched.migrate_shell_action")

NAME = "shell"


def _convert_caps(caps: dict) -> bool:
    """Move `shell` from `utils:` to `actions:` in one capabilities mapping. True on change.

    Verb-scoped forms (`shell:something`) convert too: an action kind has no verbs, so the
    scoping simply falls away — and leaving such an entry behind would leave a routine holding
    the permission with the hatch switched off.
    """
    utils = list(caps.get("utils") or [])
    keep = [u for u in utils if str(u).split(":")[0] != NAME]
    if len(keep) == len(utils):
        return False
    caps["utils"] = keep
    actions = list(caps.get("actions") or [])
    if NAME not in actions:
        actions.append(NAME)
    caps["actions"] = actions
    return True


def _migrate_routines(server) -> int:
    """Rewrite every routine/conversation/background config that grants the reserved util."""
    changed = 0
    for home in (server.routines_home, server.conversations_home, server.background_home):
        if not home or not home.is_dir():
            continue
        for d in sorted(home.iterdir()):
            path = d / "routine.yaml"
            if not d.is_dir() or d.name.startswith(".") or not path.is_file():
                continue
            raw = read_yaml(path)
            if not isinstance(raw, dict) or not isinstance(raw.get("capabilities"), dict):
                continue
            caps = dict(raw["capabilities"])
            if not _convert_caps(caps):
                continue
            raw["capabilities"] = caps
            atomic_write_yaml(path, raw)
            log.warning("shell migration: %s holds shell as an ACTION now", d.name)
            changed += 1
    return changed


def _migrate_groups(server) -> int:
    """Convert every GROUP config block that grants the reserved util (D82: a group's config is
    a live layer its members inherit, so an unconverted block re-supplies the dead entry to
    every member at load time).

    The file is patched as RAW json rather than through `groups.load` + `_save`: that pair
    normalizes on the way in, so a round-trip here would rewrite fields this migration has no
    business touching.
    """
    from .groups import groups_file

    path = groups_file(server.routines_home)
    raw = read_json(path)
    if not isinstance(raw, dict):
        return 0
    changed = 0
    for group in raw.get("groups") or []:
        if not isinstance(group, dict):
            continue
        caps = ((group.get("config") or {}).get("capabilities")
                if isinstance(group.get("config"), dict) else None)
        if not isinstance(caps, dict) or not _convert_caps(caps):
            continue
        log.warning("shell migration: group %r supplies shell as an ACTION now",
                    group.get("name") or group.get("id"))
        changed += 1
    if changed:
        atomic_write_json(path, raw)
    return changed


def _migrate_library(server) -> list[str]:
    """Replace the permission doc with the seed's and delete the retired util. Returns the
    library-relative paths that changed, so one commit can name exactly them.
    """
    touched: list[str] = []
    home = server.libraries_home
    doc = home / "permissions" / f"{NAME}.md"
    seed = repo_root() / "library-seed" / "permissions" / f"{NAME}.md"
    if doc.is_file() and seed.is_file():
        meta, _ = parse_lenient(doc.read_text(encoding="utf-8"))
        if NAME in ((meta.get("requires") or {}).get("utils") or []):
            atomic_write(doc, seed.read_text(encoding="utf-8"))
            touched.append(f"permissions/{NAME}.md")
    util_dir = home / "utils" / NAME
    if util_dir.is_dir():
        shutil.rmtree(util_dir)
        touched.append(f"utils/{NAME}")
    return touched


def migrate_shell_action(server) -> bool:
    """Convert the live instance to the shell ACTION. True when anything changed."""
    changed = _migrate_routines(server) + _migrate_groups(server)
    touched = _migrate_library(server)
    if touched:
        log.warning("shell migration: library updated (%s)", ", ".join(touched))
        libgit.commit(server.libraries_home,
                      "migration: shell becomes an action kind — retire the reserved util",
                      paths=touched)
    return bool(changed or touched)
