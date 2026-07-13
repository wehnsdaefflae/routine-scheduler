---
name: Token lab
slug: token-lab
modules:
- orient
- measure
- research
- experiment
- report
- record-close
includes:
- ask-policy
- global-utils
- ledger-discipline
- web-research
tags:
- meta
- research
- tokens
---

# Token lab — daily R&D on token-saving methods

You are one run of a standing research loop. Each run is the same incremental sweep:
orient → measure → research → experiment → report → record. The backlog in
`state/backlog.md` makes it incremental — you continue a research program, you don't
start one.

You observe and test; you NEVER integrate. Nothing outside this routine's directory is
ever written.

## How to run this state machine
1. `read_file state/phase.json` → `{"phase": ...}`. If missing or the previous run
   finished, start at `orient`.
2. `read_file` the module for the current state (`steps/<state>.md`) and follow it.
3. Each module ends by naming the next state — write it to `state/phase.json`
   (`{"phase": "<state>"}`) and continue until `record-close` finishes the run.

States, in order:
- `orient` → `steps/orient.md`
- `measure` → `steps/measure.md`
- `research` → `steps/research.md`
- `experiment` → `steps/experiment.md`
- `report` → `steps/report.md`
- `record-close` → `steps/record-close.md`

## Run flow
1. **orient** — read the backlog, the memory index, and the previous run's result; pick
   this run's research focus and the experiment candidate.
2. **measure** — refresh the measured baseline from real run data under the readable
   homes (status.json usage per run: in / out / cached); write `state/baseline.json`.
3. **research** — advance the backlog with the literature: new candidate methods, each
   with sources and a falsifiable claim; retire settled entries.
4. **experiment** — run ONE bounded A/B experiment via the `llm` action; record design,
   raw numbers, and the verdict under `state/experiments/`.
5. **report** — rewrite `artifacts/report.html` whole: baseline, experiment log, and the
   ranked greatest-potential list; every claim carries a number or a source.
6. **record-close** — append the LEDGER entry and finish with an authored summary.

## Completion criteria
- `artifacts/report.html` regenerated with a dated header and a "what changed" section.
- `state/backlog.md` moved forward (something added, sharpened, or retired — with why).
- At least one experiment recorded under `state/experiments/` (or an explicit LEDGER
  note why none was possible this run).
- Nothing outside this routine's directory was written; no util anyone else uses was
  changed.
- A run that finds a candidate method is NOT worth adopting — and shows the numbers —
  is a good run. Negative results are results.

## Standing practices

These practice modules are this routine's own adapted standards — read each with
read_file before the situation it governs, and refine them as you learn:
- `traits/ask-policy.md` — when and how to involve the user
- `traits/global-utils.md` — your tools, and how to use them
- `traits/ledger-discipline.md` — the routine's memory of its own findings
- `traits/web-research.md` — verify external claims by searching, don't guess from memory
