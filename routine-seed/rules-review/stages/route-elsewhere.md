# route-elsewhere — who owns what, on this instance

The `problem-routing` rule already binds you: owner not operator, artefact decides the owner,
work order not hint, close what you receive. Read it if you have not this run. What that rule
cannot carry is instance-specific — WHICH routines exist here and which class each one owns —
and you need that constantly, because reading everyone's runs surfaces far more that is not
about rules than that is.

## Who owns what

| the problem | its owner |
| --- | --- |
| a general rule: ambiguous, ignored, contradicted by another rule, or missing a field-tested reading | **you** — that is this routine |
| a routine's recipe: wrong instructions, drift, off-mission behaviour, stale guidance, dead weight, bloated state | **routine-improver** |
| the shared library's workflow patterns and playbooks | **routine-improver** |
| a routine's config: permissions it lacks, budgets, schedule, capability mappings, which rules it holds | **config-optimizer** |
| a util: broken, awkward CLI, missing, redundant, or a command pattern that should become one | **global-utils-review** |
| the scheduler itself: engine, web, console, contracts, cross-routine run health | **self-audit** |
| genuine judgment — taste, consent, money, identity, an irreversible outward act | **the operator** |

This table is a claim about the instance, not a fact: routines get created and retired. If a
target bounces something back as not theirs, the table is what was wrong — say so in the LEDGER
so the next pass does not repeat the misroute.

## The boundary you will stand on constantly

Yours and **routine-improver**'s remits touch at every finding, because the same observed
failure can be a recipe problem or a rule problem. The split is not the symptom, it is the
span:

- **One routine reads a rule wrongly** → that routine's problem. Its recipe misapplies a sound
  rule, or it holds a rule it should not. Route to routine-improver (recipe) or config-optimizer
  (which rules it holds).
- **Several routines read the same sentence differently** → the sentence. That is yours, and
  it is the strongest evidence you get.

When you genuinely cannot tell, prefer routing it out. A recipe fix reaches one routine and is
cheap to reverse; a rule revision reaches every holder at once. Being wrong in the smaller
direction costs less.
