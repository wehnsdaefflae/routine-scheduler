"""MIGRATION(expires=2026-09-30): force three seeded utils over their live copies (0.166.0).

`sync_seed_utils` only installs utils the library is MISSING — it never overwrites, so the
user's own revisions are safe. The cost is that a util fixed in `util-seed/` never reaches an
existing instance. Three have to this time, and each for a reason the sync cannot know:

- `git-sync` gained conflict HOLDING (the library-sync routine resolves divergence now).
- `instance-export`'s live copy still documents and selftests `steps/`, `fragments/` and
  `instruction.md` — terminology retired in 0.49.0 and 0.8.0.
- `remote`'s live copy lacks the host_key parse fix the seed records.

Deliberately a NAMED list, not "seed wins". The drift runs both ways: five other utils were
revised in production and are newer there, and a blanket seed→live would have reset
`net: outbound` to `net: none` on two of them — undeclared network means no TCP inside the
Landlock jail, so that is breakage, not churn.

Each install is selftest-gated with rollback, the same gate `write_util` applies: a seed copy
that cannot pass its own selftest on THIS machine is not an improvement, and reverting is
better than shipping it.
"""

from __future__ import annotations

import logging

from . import sandbox, utils_lib
from .bootstrap import repo_root
from .config import ServerConfig

log = logging.getLogger("rsched.migrate_seed_utils")

FORCE_FROM_SEED = ("git-sync", "instance-export", "remote")


def migrate_seed_utils(server: ServerConfig) -> int:
    """Install each named seed util over the live one. Returns how many actually changed."""
    home = server.libraries_home
    if not (home / "utils").is_dir():
        return 0
    policy = sandbox.base_policy(server)
    installed = 0
    for name in FORCE_FROM_SEED:
        src = repo_root() / "util-seed" / "utils" / name / "main.py"
        if not src.is_file():
            continue
        try:
            content = src.read_text(encoding="utf-8")
        except OSError as exc:
            log.warning("seed-util migration: cannot read %s: %s", name, exc)
            continue
        previous = utils_lib.read_util(home, name)
        if previous is not None and previous == content:
            continue
        utils_lib.write_util_file(home, name, content)
        ok, output = utils_lib.selftest(home, name, policy=policy)
        if not ok:
            if previous is None:
                utils_lib.remove_util_file(home, name)
            else:
                utils_lib.write_util_file(home, name, previous)
            log.warning("seed-util migration: %s FAILED its selftest — reverted:\n%s",
                        name, output[-2000:])
            continue
        log.warning("seed-util migration: installed the seed copy of %r over the live one",
                    name)
        installed += 1
    if installed:
        utils_lib.git_commit(home, f"migrate: install {installed} seed util(s) over live",
                             paths=[f"utils/{n}" for n in FORCE_FROM_SEED])
    return installed
