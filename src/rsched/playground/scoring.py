"""The retrieval BACKEND the in-run layer scores with — lexical, the engine's own baseline.

It is deliberately the engine's own arithmetic, not a better lexical scorer: it reuses
`engine/recall.py`'s stopwords-and-overlap shape, because what the in-run layer surfaces must be
WHAT IS RUNNING TODAY rather than a variant of it.

An embeddings backend lived here beside it, for the console subpage's retrieval bench — the
head-to-head comparison that page existed to run. The operator removed that subpage (F959),
and with it went its only caller, so the embeddings half and the vector store it filled were
removed in the same change: nothing in a run ever reached them. The in-run layer
never could — `runtime._rank` ranks lexically on purpose, because a live turn must not wait on a
provider call.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from .stores import Condition

#: Tokens too common to carry signal — `engine/recall.py`'s list, kept in step deliberately so
#: the baseline is the engine's own behaviour rather than a variant of it. The engine already
#: imports this package (`engine/tails.py` → `playground.runtime`), so the copy avoids an import
#: cycle; what makes it safe is the pin:
#: tests/test_playground.py::test_the_lexical_baseline_is_the_engines_own_tokenizer reds if the
#: two drift, because a drifted copy would silently stop being the baseline it claims to be.
STOPWORDS = frozenset({
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with", "from", "at", "by",
    "is", "it", "this", "that", "what", "when", "read", "file", "files", "run", "turn", "md",
    "state", "util", "write", "json", "py", "self", "new", "get", "set", "how", "was", "were",
})
MIN_TOKEN = 3


@dataclass(frozen=True)
class Hit:
    """One ranked item, as the firing log records it."""

    ident: str
    store: str
    score: float
    condition: str
    owner: str = ""
    detail: str = ""

    def as_dict(self) -> dict:
        return {"ident": self.ident, "store": self.store, "score": round(self.score, 4),
                "condition": self.condition, "owner": self.owner, "detail": self.detail}


def tokens(text: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", str(text).lower())
            if len(t) >= MIN_TOKEN and t not in STOPWORDS}


class LexicalBackend:
    """Token overlap, normalized by query size — the engine's baseline.

    Normalizing matters: `recall.py` works on raw overlap counts because it ranks a fixed
    archive against one action, but here query lengths vary wildly between components (`I` is a
    clause, `O` can be a page), and an un-normalized score would make a long query score higher
    on every item.
    """

    def rank(self, query: str, items: list[Condition], limit: int) -> list[Hit]:
        q = tokens(query)
        if not q:
            return []
        out: list[Hit] = []
        for item in items:
            shared = q & tokens(item.condition)
            if not shared:
                continue
            score = len(shared) / math.sqrt(len(q))
            out.append(Hit(ident=item.ident, store=item.store, score=score,
                           condition=item.condition, owner=item.owner, detail=item.detail))
        out.sort(key=lambda h: (-h.score, h.ident))
        return out[:limit]
