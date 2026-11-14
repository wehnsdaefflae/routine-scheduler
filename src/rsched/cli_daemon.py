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
    # MIGRATION(expires=2026-12-31): F564/D155's run-spanning cap shipped INERT on 28 of 33
    # routines — the scaffold had written an explicit `max_total_turns: -1` and explicit wins.
    # The operator chose the migration (R2782/R2790); it rewrites only a line git shows was
    # written once by the scaffold and never touched, so a deliberate uncapping stands.
    from .migrate_total_turns import run_migration as derive_total_turns
    derive_total_turns(server)
    # MIGRATION(expires=2026-12-31): the fau-mail/gmail -> mail consolidation left two dead
    # reserved-util names in six installed `capabilities.utils` lists, FAILing twice at the start
    # of every one of their runs — and routine.yaml is the operator's config, which no run may
    # write (D228, his answer (a)). Here with the other routine.yaml migration: before
    # anything loads one.
    from .migrate_dead_reserved_utils import run_migration as drop_dead_reserved_utils
    drop_dead_reserved_utils(server)
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
    # MIGRATION(expires=2027-01-31): the `gu` dispatcher became a tracked repo file (util-seed/gu,
    # D225). `ensure_library` above installs the MINIMAL stub and never overwrites an existing
    # dispatcher, so only this carries the tracked one across — and only onto that stub, which it
    # keeps beside it
    from .migrate_dispatcher_seed import run_migration as carry_tracked_dispatcher
    carry_tracked_dispatcher(server)
    sync_seed_library_docs(server.libraries_home, routines_home=server.routines_home)
    # (The `converse` pattern's goals revision was carried by a one-shot here until its
    # MIGRATION(expires=2026-11-30) was honoured: it ran on 2026-10-08 and the record in
    # `.control/migrations/converse-goals.json` names the copy it replaced. A fresh install
    # gets the revision from the seed sync above, so nothing replaced it.)
    # MIGRATION(expires=2026-12-31): the fourteen INSTALLED patterns gain what F883 fixed in the
    # seed — `max_total_turns: 0` (every one still read the inert `-1` the other migration above
    # exists to remove, so each new routine re-introduced it), the three ladder keys and
    # `outcome-first`. Here because the sync above is ADD-ONLY and cannot touch a doc the live
    # library already has, and BEFORE anything loads a pattern; a list the operator narrowed is
    # left alone (D283).
    from .migrate_pattern_drift import run_migration as converge_patterns
    converge_patterns(server)
    # The seed rules' `assists:` DECLARATIONS, per entry: the add-only file sync above cannot
    # carry a block into a rule the live library already has, so every hook point the campaign
    # wired was firing against no candidates at all. Recurring and add-only per declaration id
    # (rsched/seed_assists.py) — this replaces 0.477.0's one-shot `migrate_rule_assists`, which
    # by construction could only ever close the gap once: it refused a rule that already
    # declared assists, which is every rule it had already fixed.
    from .seed_assists import sync_seed_rule_assists

    sync_seed_rule_assists(server.libraries_home, routines_home=server.routines_home)
    # MIGRATION(expires=2026-12-31): the same add-only gap, for a permission's `requires:` — the
    # fau-mail/gmail -> mail consolidation left two dead util names there, FAILing twice at the
    # start of every run of every holder, and a routine cannot edit the library (R3081)
    from .migrate_permission_requires import run_migration as carry_permission_requires
    carry_permission_requires(server)
    adopt_library_edits(server.libraries_home, routines_home=server.routines_home)
    # (Every past run's record was rebuilt from git and the runs' own files by a one-shot here
    # until its MIGRATION(expires=2026-11-30) was honoured: it ran on 2026-10-08 over 1,331 runs,
    # 967 of them with quality recovered, and `.control/migrations/run-records.json` holds what
    # it could not recover. The records it wrote stand; nothing re-derives them, which is why the
    # three modules that read the instance's own history went with it.)
    # MIGRATION(expires=2026-12-15): every past usage record gains its leg's cache traffic and
    # context volume, which every cost ranking now reads — after the rebuild above, same window
    from .migrate_usage_volume import run_migration as backfill_usage_volume
    backfill_usage_volume(server)
    # MIGRATION(expires=2026-12-31): the runs retention kept gain the friction signatures the
    # moment test reads, counted from their transcripts — after the volume backfill, same window
    from .migrate_frictions import run_migration as backfill_frictions
    backfill_frictions(server)
    # MIGRATION(expires=2026-12-31): the runs retention kept gain the task/process/friction split
    # the process-overhead invariant reads — after the friction backfill, same window
    from .migrate_overhead import run_migration as backfill_overhead
    backfill_overhead(server)
    # MIGRATION(expires=2026-12-31): the pre-stream report history becomes hooks, so the first
    # decision taken from the stream (the open-thread cap) answers from the whole ledger and not
    # from the three weeks since step 4 — before the app starts, so nothing appends meanwhile
    from .migrate_report_hooks import run_migration as carry_report_hooks
    carry_report_hooks(server)
    # MIGRATION(expires=2027-01-31): the 8 mirror-era report hooks (2026-10-19, between step 2
    # and step 4) regain the disposal step 2's mirror dropped — AFTER the backfill above and
    # BEFORE the app starts, because step 9 makes the stream what every report surface reads
    # and without this six settled exchanges would read as open
    from .migrate_mirror_hooks import run_migration as repair_mirror_hooks
    repair_mirror_hooks(server)
    # MIGRATION(expires=2027-02-28): the REMINDER half of the same step. 95 definitions across
    # 21 of 31 routines exist in a store with no hook at all, every one written before the
    # mirror landed (newest `rem-20261018-…`, mirror 2026-10-19) — so the class is closed and a
    # backfill empties it. The 23 stream-only ids are deletions, not gaps, and are deliberately
    # left: resurrecting a reminder its routine removed would change what HOLDS a run.
    from .migrate_premirror_reminders import run_migration as backfill_premirror_reminders
    backfill_premirror_reminders(server)
    # MIGRATION(expires=2026-12-31): every routine's assist tally grows from an int per key to
    # the stat fields a reminder's keeps, so the four outcome labels D239 wired have somewhere
    # to go and the fires already counted stay the denominator they are read against
    from .migrate_assist_stats import run_migration as grow_assist_tallies
    grow_assist_tallies(server)
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
