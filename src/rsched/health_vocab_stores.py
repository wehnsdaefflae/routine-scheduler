"""The STORE-SHORTFALL family of the health vocabulary — a read that served the old store.

Its own module because `health_vocab` says so in place: *"a family that outgrows this file moves
to a module of its own and is joined here"*. That file is a 300-line glossary docstring plus the
name tuple, sitting at the size bar, and a list in a docstring cannot be split — a tuple can.

These four events share one subject, which is why they are one family: **a read that should have
been served from the hook stream was served from the store the stream replaced.** D152 moves each
store, one at a time, from a file every writer could touch to a projection of the one write path;
until a migration is complete the read falls back, and this family is how a fallback is visible
instead of silent. They are deliberately NOT in `readmodels/health_stream.BLOCKED_EVENTS`: a
shortfall the read works around is not work that did not happen.

Each is also in `health_cooldown.REPEAT_COOLDOWN_HOURS` at 12 h, and that is load-bearing rather
than tidy: these reads run per REQUEST (every surface listing a report) and per RUN (the reminder
read happens while a run is being constructed), so an instance whose stream is behind would file
a row per request instead of reporting a condition that holds until someone repairs the stream.

hook_stream_diverged: the hook stream and the `reports.jsonl` it projects disagreed about a
report decision (`hooks/reportinv` — the write path's thread cap, the pre-finish hold to answer
a hand-off): `stream`/`ledger` are the counts, `detail` names the asker. The STREAM's answer
was used: since D152 step 9 it is what every report surface reads, and the ledger is the
projection. The event makes a drift findable while both stores are still written.

hook_stream_unreadable: the report read found NO report rows in the hook stream and fell back
to `reports.jsonl` (`hooks/reportread`, D152 step 9), `rows` how many reports the projection
holds. Either the stream was lost — restore it, or rebuild it from the ledger through
`migrate_report_hooks` — or this is a fresh instance. A standing condition, so one row per
window rather than one per read.

reminder_stream_diverged: the reminder read found the hook stream implies FEWER reminders than
`state/reminders.json` holds for that routine, and served the store (`hooks/reminderread`, D152
step 4): `missing`/`served`/`from_stream` are the counts, `detail` names the ids. Unlike the
report case the STORE's answer is used, because a lost definition changes what HOLDS a run — a
reminder that vanished would stop interrupting the action it was written for. Repair with
`migrate_premirror_reminders` / `migrate_store_wins` and the comparator
(`hooks/reminderproj.drift`), which must read empty.

reminder_stream_unreadable: the reminder read found the hook stream implies NO reminder at all
for that routine and served `state/reminders.json` (`hooks/reminderread`), `served` how many the
store holds. Either the stream was lost, the backfill never ran, or the routine is fresh and has
none. A standing condition, so one row per window rather than one per read.
"""

from __future__ import annotations

#: joined into `health_vocab.EVENTS`; every reader of the stream is keyed on that one tuple
EVENTS: tuple[str, ...] = (
    "hook_stream_diverged",
    "hook_stream_unreadable",
    "reminder_stream_diverged",
    "reminder_stream_unreadable",
)
