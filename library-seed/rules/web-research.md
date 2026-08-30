---
effect:
  with: looks a fact about the world up before it uses it
  without: answers from what the model already knows, which is stale and sometimes silently wrong
  when: the task turns on outside facts — prices, availability, news, anything dated
tags: [tool-use, research, web]
---
# rule: web-research — verify external facts by searching, don't guess from memory

When a step turns on a fact about the outside world you are not certain of, **look it up
instead of recalling it**. Your training is stale and lossy; a wrong fact quietly poisons
everything downstream of it. Your CAPABILITIES catalog lists a web-search tool returning ranked
title/url/snippet results, and deeper retrieval ones (page fetch, scrapers).

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
- **Record provenance** — put the source URL next to the fact in your output or record so the
  claim is traceable and the next run needn't re-verify it.
- Keep verified facts distinct from your own inferences; never present an inference as a lookup.

Searching costs a turn and some tokens — cheap next to shipping a confident wrong answer.
