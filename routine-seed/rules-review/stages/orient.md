# orient — what changed since the last pass

Build the map before choosing anything: which rules exist, who holds each, and which holders
have produced new evidence since that rule was last reviewed.

1. Read `state/reviewed.json` — `{<rule-slug>: {"last_pass": <iso>, "last_change": <iso|null>,
   "verdict": "<revised|left-alone>"}}`. Missing or empty means nothing has been reviewed yet;
   every rule is a candidate.
2. Read your own LEDGER before exploring: a revision the user already declined is not a finding
   to re-derive, and the reason they declined it is the most useful thing you have.
3. List the library's rules — the catalog is one read (`read_rule` with name `list`). Read the
   full text only for rules you end up selecting; the catalog's summaries are enough to plan.
4. For each rule, find its HOLDERS: the routines and conversations whose config binds it. Read
   each candidate's config rather than assuming — the set changes without telling you.
5. For each holder, note whether it has finished runs newer than that rule's `last_pass`. A rule
   whose holders have all been idle carries no new evidence.

Write `state/candidates.json`: `[{"rule": <slug>, "holders": [<slug>…], "fresh_runs": <n>,
"last_pass": <iso|null>}]`, then advance to **select-rules**
(`state/phase.json` = `{"phase": "select-rules", "cursor": {}}`).
