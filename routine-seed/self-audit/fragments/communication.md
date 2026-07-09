---
tags: [communication, policy, tool-use]
---
# fragment: communication — reach the user for a blocking decision, then escalate

When you hit a **minimal but blocking** decision — a small choice only the user can make, without
which this run can't sensibly continue — don't just file it and move on. Reach the user directly,
wait briefly, and escalate only if they're away.

**Channel: Discord (a phone notification).** If a Discord util exists (`util name=list` — e.g.
`discord`), use it:

1. **Send** the decision as ONE self-contained message with the concrete options:
   `util discord send "<the decision + its 2–4 options, one per line + the default you'll take if
   there's no reply>" --title "<routine>: decision"`. Make it answerable from a phone in one line.
2. **Wait** up to five minutes for a live reply, keyed to THIS routine so you never consume another
   routine's messages: `util discord wait --timeout 300 --cursor <your routine slug>` (set the
   action's `timeout_s` a little above 300, e.g. 320).
3. **Reply arrived** → treat it as the decision, act on it, and record it in the LEDGER.
4. **No reply within 5 min** → **escalate**: file the SAME decision as a deferred `ask_user` (it
   lands in the Decisions inbox), note in the LEDGER that Discord went unanswered, and take your
   stated default or defer that thread to the next run — never block the whole run waiting longer.

**No Discord util available** → skip straight to the deferred `ask_user` (the Decisions inbox); the
escalation path is the fallback, so the routine still works without the channel.

**Restraint.** This is for genuinely blocking, minimal decisions — not progress updates or anything
you can decide yourself (respect the ask cap in the ask-policy fragment). A 5-minute wait spends
wall-clock budget: batch every blocking decision into ONE message and send at most one blocking
Discord round per run.
