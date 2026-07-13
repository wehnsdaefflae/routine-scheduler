# research — advance the backlog

`state/backlog.md` is the research program: one `## <method>` section per candidate, each
carrying **claim** (falsifiable, with an expected % saving), **sources** (links), **how to
test it here** (an experiment sketch runnable with the `llm` action), and **status**
(open / testing / supported / refuted / adopted-elsewhere).

1. Search the literature for methods new since the backlog's newest entry (websearch;
   consult `traits/web-research.md` first). Productive queries: prompt caching, context
   compression/editing, agentic memory (A-MEM, sleep-time compute), tool/catalog pruning,
   structured-output overhead, compaction policies, KV-cache economics.
2. Add genuinely new methods (2-3 per run at most — depth beats a link dump). Sharpen or
   retire stale entries; a refuted entry KEEPS its section with the numbers that killed it.
3. Confirm today's experiment candidate from `orient` is still the right pick given what
   you just learned; if not, say why and pick another.

First run only — seed the backlog with these starters (then treat them like any entry):
- catalog pruning: does a task-relevant subset of the util catalog change action-selection
  accuracy vs the full 50-entry catalog? (BFCL reports 43%→2% collapse at 51 tools)
- summary compression: what do the 8-20-line finish summaries cost per run vs a 5-line
  cap, and does the next run's orientation suffer?
- memory linking: do A-MEM-style `[[links]]` between .memory notes reduce re-discovery
  reads in later runs?
- compaction timing: archive at phase boundaries vs at size thresholds — which preserves
  more usable context per token?
- observation dedup: how often do identical util outputs recur within one run, and what
  would a "same as turn N" pointer save?

Next state: `experiment` — write `{"phase": "experiment"}` to `state/phase.json`.
