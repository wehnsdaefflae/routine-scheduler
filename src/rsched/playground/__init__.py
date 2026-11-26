"""The PLAYGROUND — the in-run experimental layer for the unified-retrieval proposal.

Why this package exists apart from `engine/`: the proposal under test is that the four helper
stores (utils, memory notes, reminders, rules) can be served by ONE retrieval layer queried with
a turn's own components, instead of the three hand-rolled scorers the engine carries today
(`engine/recall._score`, `utils_lib.search_utils`, and the reminder regexes). The engine reaches
this package through exactly ONE guarded entry point — `engine/tails.py` imports
`playground.runtime` and calls `at_observation` on every observation — and that call returns an
empty string unless the conversation itself carries `playground: true`, so an experiment cannot
change what a production run is shown.

There was a console subpage on top of this: a Conversations-shaped page listing the flagged
conversations with their firings, plus a retrieval bench comparing a lexical and an embeddings
backend over the same situation. The operator removed it (F959, 2026-11-25: "Remove the
playground subpage. We don't need it after all"), and everything only that page reached went
with it — the HTTP surface, the bench's embeddings backend and its vector store. What remains is
the half a RUN uses, which no page was ever needed for: a flagged conversation still runs its
experiments and still writes `playground-firings.jsonl` beside each of its runs.

The vocabulary, fixed here because every module shares it:

* **component** — one part of a turn, the query material. `A` the action, `O` the observation,
  `R` the reflection (today's `say`), `I` the intention (which the current composition has no
  separate field for), `P` the person's words. A *recipe* is a set of components, e.g. `A+O`
  (`features.Feature.components`).
* **store** — one helper library: `utils`, `memory`, `reminders`, `rules`
  (`features.Feature.stores`).
* **condition** — the text of a stored item that states WHEN it applies, which is what gets
  indexed (a util's summary+usage, a note's `about`, a reminder's description, a rule's
  `effect.when`). Deliberately not the item's whole body: a query is a situation, and what it
  should match is the item's trigger, not its payload.

Modules: `stores.py` reads the four libraries into conditions; `scoring.py` holds the lexical
backend; `features.py` and `runtime.py` are the guarded in-run surface.
"""

from __future__ import annotations
