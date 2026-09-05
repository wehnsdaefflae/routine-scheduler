"""`rsched daemon` — the boot sequence systemd actually runs.

Split out of `cli.py` (F393). This is not one command among many: it is the ordered boot of a
live instance — config bootstrap, permission adoption, library sync, then the web
app and scheduler. The ORDER is load-bearing and commented as such, which is exactly why it does
not belong inside a dispatcher that otherwise just parses argv.
"""

from __future__ import annotations

from .config import load_server_config


def cmd_daemon(_args) -> int:
    import logging
    import os

    import uvicorn

    from .web.app import create_app

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    from .bootstrap import (
        adopt_library_edits,
        adopt_permissions,
        ensure_config,
        sync_seed_library_docs,
        sync_seed_utils,
    )
    ensure_config()   # fresh deploy: generate config+token so the API isn't open
    server, problems = load_server_config()
    # new default permissions reach existing routines once, at boot
    adopt_permissions(server.routines_home, server.permissions_home)
    sync_seed_utils(server.libraries_home)    # utils added to util-seed since bootstrap
    sync_seed_library_docs(server.libraries_home)  # workflows/rules/permissions added since, too
    adopt_library_edits(server.libraries_home)  # out-of-band writes (user/conversation) get history
    from .migrate_shell_action import migrate_shell_action

    # MIGRATION(expires=2026-10-03): the shell escape hatch is an ACTION KIND now, so a holder
    # still naming it under capabilities.utils would lose it silently. Runs AFTER the seed
    # syncs, so the library it rewrites is the one this boot will actually serve.
    migrate_shell_action(server)
    from .migrate_stopping_scope import migrate_stopping_scope

    # MIGRATION(expires=2026-11-05): stopping conditions gain a SCOPE. Every existing condition
    # is a per-RUN bound and must stop being sticky — 22 of 31 routines were being told "the job
    # is DONE. Finish NOW" at the top of every run. Nothing here promotes a condition to `goal`:
    # which routines have a terminal state is the user's call, made in the panel.
    migrate_stopping_scope(server)
    from .migrate_rule_assists import run as migrate_rule_assists
    from .paths import repo_root

    # MIGRATION(expires=2026-12-01): the first rule ASSISTS. The seed sync is add-only, so a
    # frontmatter block added to a rule that already exists live reaches nobody — this carries
    # the three declared in 0.305.0 across, skipping any rule an operator has since edited.
    # Runs after the seed syncs, so it rewrites the library this boot will serve.
    migrate_rule_assists(server.rules_home, repo_root() / "library-seed" / "rules")
    from .migrate_reminders_rollout import run as migrate_reminders_rollout

    # MIGRATION(expires=2026-12-01): the adopt cascade carried a private copy of the capability
    # raise that knew four of nine keys, so a permission whose requires: names any other DIAL
    # was adopted with its capability left off — the doc held, the engine behaving as if it
    # were not. Re-raise every routine's capabilities from the permissions it holds, and give
    # the live settings templates the two dials the seed now names. After adopt_permissions,
    # so this boot's adoptions are converged too.
    migrate_reminders_rollout(server.routines_home, server.permissions_home,
                              server.libraries_home)
    for pr in problems:
        logging.getLogger("rsched").warning("config: %s", pr)
    app = create_app(server)
    # env overrides so a container can bind the LAN (RSCHED_BIND=0.0.0.0) and remap the port
    # without editing the mounted config; unset → the config's bind/port as before.
    host = os.environ.get("RSCHED_BIND") or server.bind
    port = int(os.environ.get("RSCHED_PORT") or server.port)
    # Bound graceful shutdown: the web UI holds long-lived SSE streams that never close on
    # their own, so an unbounded graceful shutdown hangs (a manual `systemctl restart` waited
    # the full TimeoutStopSec; the self-update restart, which SIGTERMs itself, would hang with
    # no systemd timeout at all). 10s force-closes idle streams while letting real requests finish.
    uvicorn.run(app, host=host, port=port, log_level="warning",
                timeout_graceful_shutdown=10)
    return 0
