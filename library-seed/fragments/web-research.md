---
tags: [tool-use, research]
---
# fragment: web-research — verify external facts by searching, don't guess from memory

When a step turns on a fact about the outside world you are not certain of, **look it up
instead of recalling it**. Your training is stale and lossy; a wrong fact quietly poisons
everything downstream of it. The web is one `util` action away — the `websearch` util
(`util` name `websearch`, args `["<query>", "--json"]`) returns ranked title/url/snippet
results; deeper retrieval utils exist too (`util name=list` — e.g. page fetch, scrapers).

**When to search (default to yes for these):**
- Anything time-sensitive or that changes: prices, availability, versions, schedules, who
  currently holds a role, "latest"/"current"/"today".
- Specifics you'd otherwise approximate: exact names, dates, figures, identifiers, URLs.
- A domain the instruction cares about but you only half-know — confirm before you build on it.
- Any claim you're about to write into a deliverable as if it were established fact.

**When not to:** settled general knowledge, this routine's own state, or arithmetic — searching
those is just latency.

**How to use it well:**
- Make the query specific (add the year, the place, the exact term). Read snippets; open the
  page only when the snippet isn't enough.
- Corroborate anything load-bearing with a second independent result before you rely on it;
  prefer primary/official sources over aggregators.
- **Record provenance** — put the source URL next to the fact in your output or LEDGER so the
  claim is traceable and the next run needn't re-verify it.
- Keep verified facts distinct from your own inferences; never present an inference as a lookup.

Searching costs a turn and some tokens — cheap next to shipping a confident wrong answer.
