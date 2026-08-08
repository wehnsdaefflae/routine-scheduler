# record — say plainly whether the off-box copy is current

1. Append one LEDGER entry: routines staged, files changed and pruned, whether the config was
   redacted, and the sync outcome. Keep the numbers — a run of this routine has no other
   artefact, so the entry IS the evidence that it worked, and a bare "synced" records nothing.
2. Reset `state/phase.json` to `{"phase": "export"}` so the next run starts clean.
3. **Report anything you were told not to fix.** A conflict, a rejected push, a refused
   credential, an unexplained prune — each goes to whoever owns it, with the exact error text
   and how many runs it has now affected. This routine finds problems it is deliberately barred
   from solving; not passing them on is the only way it can actually fail at its job.
4. Finish with the honest status: `ok` only when the push succeeded. An export that staged
   perfectly and a push that did not land is `failed` — the instance has no off-box copy of this
   run's changes, and a summary that leads with the successful half buries exactly the fact
   somebody needs.
