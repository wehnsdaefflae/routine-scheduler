---
tags: [maintenance, escalation, routing]
---
# trait: maintenance routing — send a problem to whoever owns it, not upward

This instance has several maintenance routines, each owning one class of problem. A problem you
cannot fix goes FIRST to the routine that owns it, and only reaches the operator when no routine
can. Escalating straight to the operator looks diligent and is not: it parks the work behind a
human who has to re-route it by hand, and blocked items have sat for days that way.

## Who owns what

| the problem | its owner |
| --- | --- |
| a routine's recipe: wrong instructions, drift, off-mission behaviour, stale guidance, dead weight, bloated state | **routine-improver** |
| the shared library: workflow patterns, traits, playbooks | **routine-improver** |
| a routine's config: permissions it lacks, budgets, schedule, capability mappings (`routine.yaml`) | **config-optimizer** |
| a util: broken, awkward CLI, missing, redundant, or a command pattern that should become one | **global-utils-review** |
| the scheduler itself: engine, web, console, contracts, cross-routine run health | **self-audit** |
| genuine judgment — taste, consent, money, identity, an irreversible outward act | **the operator** |

If two owners could plausibly take it, send it to the one whose ARTEFACT must change. A recipe
that misuses a working util is the recipe's owner; a util that no reasonable recipe could use
correctly is the util's owner.

## The ladder

1. **Do it yourself** if it is inside your own remit and your permissions reach it. Check what you
   actually hold before concluding you cannot — "not in my write roots" is a claim to verify, not
   an excuse.
2. **Report it to the owner** with the `report` action: `target` (the owner's slug), `title` (one
   line), `detail`. It is filed under an `R<n>` id and lands in that routine's inbox, and that
   routine reads it at the top of its NEXT SCHEDULED RUN. Nothing is started.
   Write a WORK ORDER, not a hint. The reader has none of your context: name the exact file or
   artefact, say what is wrong, give the evidence (a run id, a `path:line`, the error text), and
   state what "done" looks like.
   When you cannot name the owner, leave `target` out. The report goes to triage and is routed
   for you — always better than guessing a target who will bounce it back.
3. **Route through self-audit** when the problem is the scheduler's own behaviour, or when the
   owner has already tried and been blocked by something structural.
4. **Ask the operator** only when no routine can act — and say what you already tried, and which
   routine you handed it to, so the answer is a decision and not an investigation.

## Both directions, and the honest report

Receiving is half of this. Reports addressed to you reach you in their own prompt section: read
them as a first-class input, not as noise — something else in the system found a problem inside
YOUR remit and did the diagnosis for you. Act on it or say plainly why you will not, and either
way CLOSE it by reporting back to the sender with `answers: "<the R id>"`. That reply is what
turns a hand-off from sent into settled.

Record every hand-off you make and every one you receive, so a problem that bounces between two
routines becomes visible instead of circulating. If you catch yourself receiving the same class of
problem repeatedly, that is not a queue to work through — it is evidence the ownership table above
is wrong, and saying so is the more valuable finding.
