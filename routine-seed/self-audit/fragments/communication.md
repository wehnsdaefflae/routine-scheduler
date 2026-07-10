# fragment: communication — Discord for blocking questions only

This fragment authorizes ONE channel beside the web UI: **Discord** (the `discord` util), and
only for **blocking questions** — a decision without which this run cannot sensibly continue.
Everything else — progress, status, FYI, results, non-blocking decisions — stays in the UI
(deferred `ask_user`, the LEDGER, the finish summary). Never send noise to Discord.

The one round, when a genuinely blocking question comes up:

1. **Check LEDGER.md first** — if a past run already got this answer (or an equivalent one),
   reuse it instead of asking again.
2. **Batch** every blocking question of this run into ONE minimal, self-contained message —
   readable with zero run context, answerable from a phone in one line. State the concrete
   options and the default you will take if there is no reply:
   `util discord send "<question + options + default>" --title "<routine slug>: decision"`.
3. **Wait, bounded**: `util discord wait --timeout 300 --cursor <your routine slug>` (the cursor
   keys replies to THIS routine; set the action's `timeout_s` a little above, e.g. 320). One
   wait of ~5 minutes — never longer, never a second round in the same run.
4. **Reply arrived** → act on it and record question + answer in LEDGER.md so no future run
   re-asks. **No reply** → fall back to the SAME question as a deferred `ask_user` (it lands on
   the Decisions page), note the unanswered Discord attempt in the LEDGER, and proceed on your
   stated default — never block the run any further.

**If the `discord` util is missing** (`util name=list` to check), skip straight to the deferred
`ask_user`. Routines WITHOUT this fragment must not touch Discord at all — for them the UI is
the only channel (plain `ask_user`). Keep total user interaction minimal: batch, remember
answers via the LEDGER, and respect the ask cap in the ask-policy fragment.
