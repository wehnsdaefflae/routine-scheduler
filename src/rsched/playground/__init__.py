"""The PLAYGROUND — an isolated lab for the unified-retrieval proposal (the operator's order).

Why this package exists apart from `engine/`: the proposal under test is that the four helper
stores (utils, memory notes, reminders, rules) can be served by ONE retrieval layer queried with
a turn's own components, instead of the three hand-rolled scorers the engine carries today
(`engine/recall._score`, `utils_lib.search_utils`, and the reminder regexes). Nothing here is
wired into a live run: the engine does not import this package, so an experiment cannot change
what a production run is shown. Promotion to production is the operator's call, made on the page.

The vocabulary, fixed here because every module and the page share it:

* **component** — one part of a turn, the query material. `A` the action, `O` the observation,
  `R` the reflection (today's `say`), `I` the intention (which the current composition has no
  separate field for), `P` the person's words. A *recipe* is a set of components, e.g. `A+O`.
* **store** — one helper library: `utils`, `memory`, `reminders`, `rules`.
* **condition** — the text of a stored item that states WHEN it applies, which is what gets
  indexed (a util's summary+usage, a note's `about`, a reminder's description, a rule's
  `effect.when`). Deliberately not the item's whole body: a query is a situation, and what it
  should match is the item's trigger, not its payload.
* **backend** — how a query is scored against conditions: `lexical` (the engine's own token
  overlap, the baseline that must be beaten) or an embedding backend.

Modules: `stores.py` reads the four libraries into conditions; `scoring.py` holds the backends;
`replay.py` labels real transcripts; `labels.py` owns the scratch dir. The HTTP surface is
`web/api_playground.py` and the page `static/views/playground.js`.
"""

from __future__ import annotations

#: The query components, in the order the proposed composition emits them. `P` is not part of a
#: turn — it arrives at a boundary — but it is the only material that answers the one genuinely
#: semantic rule predicate (`outcome-questioned`), so the lab carries it.
COMPONENTS: tuple[str, ...] = ("A", "O", "R", "I", "P")

#: What each component is, shown beside its checkbox on the page so the vocabulary is learned
#: once, in one place.
COMPONENT_LABELS: dict[str, str] = {
    "A": "action — what was actually done (kind, target, args)",
    "O": "observation — what came back: output, errors, exit codes",
    "R": "reflection — what the result meant to the run (today's `say`)",
    "I": "intention — what the run is about to try (no field for this today)",
    "P": "the person's words — the operator's own framing, asks, complaints",
}

#: The four helper stores this lab can query.
STORES: tuple[str, ...] = ("utils", "memory", "reminders", "rules")

__all__ = ["COMPONENTS", "COMPONENT_LABELS", "STORES"]
