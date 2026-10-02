"""`rsched daemon` — the boot sequence systemd actually runs.

Split out of `cli.py` (F393). This is not one command among many: it is the ordered boot of a
live instance — config bootstrap, the one-shot migrations, library creation and sync, then the
web app and scheduler. The ORDER is load-bearing and commented as such, which is exactly why it does
not belong inside a dispatcher that otherwise just parses argv.
"""

from __future__ import annotations

from .config import load_server_config

#: The ONE log line shape: the daemon's own log, and an engine subprocess's stderr, which the
#: daemon scans for `WARNING`/`ERROR` lines to re-emit after a clean finish
#: (`daemon/runner_state._notable_stderr`) — so the level name has to be IN the line.
LOG_FORMAT = "%(asctime)s %(name)s %(levelname)s %(message)s"


def cmd_daemon(_args) -> int:
    import logging
    import os

    import uvicorn

    from .web.app import create_app

    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
    from .bootstrap import (
        adopt_library_edits,
        ensure_config,
        sync_seed_library_docs,
        sync_seed_utils,
    )
    from .utils_lib import ensure_library
    ensure_config()   # fresh deploy: generate config+token so the API isn't open
    server, problems = load_server_config()
    # MIGRATION(expires=2026-10-20): routines onto settings patterns, BEFORE the seed sync and
    # before anything loads a routine.yaml that still names a domain
    from .migrate_settings_patterns import run_migration
    run_migration(server)
    # MIGRATION(expires=2026-11-15): `schedule.disabled` folded into the one off switch,
    # `enabled` — also before anything loads a routine.yaml, which no longer reads it
    from .migrate_enabled import run_migration as fold_off_switch
    fold_off_switch(server)
    # The library repo exists BEFORE the syncs fill it. A container has no install step and its
    # library is an empty bind mount at first boot; the util sync installs only into an
    # existing utils/, and the repo used to be created by the web lifespan after these ran — so
    # a new deploy started without a single util until its second boot. Idempotent, and
    # never fatal, exactly as in the lifespan.
    try:
        ensure_library(server.libraries_home, remote=server.libraries_remote)
    except Exception as exc:
        logging.getLogger("rsched").warning("library bootstrap %s: %s",
                                            server.libraries_home, exc)
    # utils added to util-seed since bootstrap, then workflows/rules/permissions added since too,
    # then out-of-band writes (user/conversation) get history
    sync_seed_utils(server.libraries_home, routines_home=server.routines_home)
    # MIGRATION(expires=2026-11-15): this release's fixes to four utils the live library
    # already has — the sync above only adds missing ones
    from .migrate_seed_utils import run_migration as carry_seed_util_fixes
    carry_seed_util_fixes(server)
    sync_seed_library_docs(server.libraries_home, routines_home=server.routines_home)
    adopt_library_edits(server.libraries_home, routines_home=server.routines_home)
    for pr in problems:
        logging.getLogger("rsched").warning("config: %s", pr)
    app = create_app(server)
    # env overrides so a container can bind the LAN (RSCHED_BIND=0.0.0.0) and remap the port
    # without editing the mounted config; unset → the config's bind/port as before.
    host = os.environ.get("RSCHED_BIND") or server.bind
    port = int(os.environ.get("RSCHED_PORT") or server.port)
    # Bound graceful shutdown: the web UI holds long-lived WebSocket streams that never close on
    # their own, so an unbounded graceful shutdown hangs (a manual `systemctl restart` waited
    # the full TimeoutStopSec; the self-update restart, which SIGTERMs itself, would hang with
    # no systemd timeout at all). 10s force-closes idle streams while letting real requests finish.
    uvicorn.run(app, host=host, port=port, log_level="warning",
                timeout_graceful_shutdown=10)
    return 0
