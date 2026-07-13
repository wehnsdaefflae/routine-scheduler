# Record and close

## Do — per touched target
1. Append ONE line to the TARGET's `LEDGER.md`:
   `### <date> — routine-improver: <changes applied; candidates rejected + why; questions filed>`.
2. Commit the target's dir: `util git-sync <target dir> -m "routine-improver: <one line>"`
   (routine dirs usually have no remote — the util then just commits; that is correct).
   Conversations are NOT git repos — skip the commit there and verify edits by reading
   them back instead.
3. Update `state/visits.json[slug]` = `{last_visit: <now>, last_run_seen: <newest run ts>}`.

## Do — once
4. Append your own LEDGER entry: targets visited, changes per lens, fresh-eyes prunings,
   skips (excluded/disabled), questions filed. Negative evidence matters — record what you
   considered and rejected so the next sweep doesn't re-try it.
5. Optionally `memory_write` one note per target if something cross-run matters that the
   LEDGERs don't carry (a hypothesis to test on the next visit, a user preference observed).
6. Reset `state/phase.json = {}` and `finish` with a one-paragraph summary: targets → what
   improved, in which lens; what fresh-eyes removed; open questions.

Your own dir is committed by the engine automatically — never run git on yourself.
