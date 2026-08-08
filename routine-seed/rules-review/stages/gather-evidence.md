# gather-evidence — what runs actually did with this rule

For the rule named in the cursor, read its full text first, then go and find out what holders
made of it. You are collecting INTERPRETATIONS — what a run took the rule to mean and what
followed — not opinions about the wording.

Read the rule, then for each holder read its recent finished runs: the narration a run leaves
as it works, the notes and records it wrote, the results it produced, and any user correction
that landed afterwards. A user correcting a run at a point the rule governs is the single most
valuable observation available: the rule was in force and still did not prevent the mistake.

Collect each observation as one row in `state/evidence/<rule>.json`:

```
{"routine": <slug>, "run": <run id>, "quote": "<the run's own words, verbatim>",
 "reading": "<what it took the rule to mean>", "outcome": "<what followed>",
 "class": "followed | misread | ignored | invented | not-a-rule-problem"}
```

- **followed** — the rule applied cleanly and the outcome was right. Keep these: they are what
  a revision must not break, and they are the only evidence that a sentence is load-bearing.
- **misread** — the run applied the rule and got it wrong, or applied it where it should not
  have. Record the exact sentence it was working from.
- **ignored** — the rule bound the run, the situation arose, and the run did not act on it.
  Distinguish "did not read it" from "read it and set it aside" — different fixes.
- **invented** — the run reached a good interpretation the text does not actually contain.
  This is the most under-collected class and the most valuable one: it is a rule improvement
  already field-tested by a run that needed it.
- **not-a-rule-problem** — the failure was a util, a config, a recipe, or the scheduler.
  Record it and route it (`stages/route-elsewhere.md`); do not bend it into a rule finding.

Quote, never paraphrase — a paraphrase is your reading of a run's reading, and by the time it
reaches an edit nobody can check it. If a rule's holders produced fewer than a handful of
real observations, say so and let the revise stage weigh it accordingly.

Advance to **diagnose** with the same cursor.
