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
