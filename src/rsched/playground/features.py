"""The EXPERIMENTAL FEATURES a playground conversation runs with — the live half of the lab.

A playground conversation is an ordinary conversation (same engine, same workflow, same
budgets) with `playground: true` in its `routine.yaml` and a set of experiment slugs in
`playground_features:`. The engine asks this module once per observation, from
`EngineLoop._tails`, and gets back either "" (nothing to add — exactly production behaviour) or
the tail text the experiment wants the run to read.

Two invariants make this safe to ship inside the live process:

1. **A conversation without the flag never reaches this code**, and a flagged one with no
   features enabled gets "" — so production runs are byte-identical to today.
2. **Every firing is logged** to the conversation's own `playground/firings.jsonl` before it is
   handed back, so the page can show what the run was actually shown and the operator can judge
   precision from real turns rather than from a bench.

The features, each one a claim from the design thread made testable:

* `util-hint` — the push channel: query the util store with the turn's own components and name
  at most one util the run may be re-implementing by hand. Gated to the three action kinds that
  ARE manual re-implementation (`shell`, `script`, `write_util`), which is the proposal's own
  prediction about where the harm lives.
* `memory-hint` — the same for memory notes, the store with a genuine push gap: notes are
  reachable only when a run thinks to call `memory_read`.
* `intent-field` — asks the run for a separate `intention` line, so the query signal can be the
  clean clause instead of mixed reflection+intention prose (the centroid problem).

Each feature declares its own query recipe (which components it reads) because the thread's
conclusion was that the right recipe differs per helper: evidential `A+O` for catching work done
by hand, interpretive `R+I` for prospective selection.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

#: Action kinds that ARE manual re-implementation — where a util the library already has is
#: most likely being written by hand. The util hint fires only after one of these.
MANUAL_KINDS = frozenset({"shell", "script", "write_util"})

#: A hit must clear this to be worth a line. A wrong pointer teaches the run to ignore the
#: layer — the same reasoning `engine/recall.py` states for its own floor.
#:
#: MEASURED, and the finding is unflattering to the lexical baseline: over 6,731 real
#: shell/script/write_util turns the top util score has a median of 1.21 and a p90 of 2.09,
#: while the HIGH scorers are coincidences (a util matched by a filename in a `cp`, another by
#: the word "search" inside a heredoc). A true positive measured on a hand-rolled SMTP call
#: scored 1.26 — i.e. BELOW the median of the noise. No single floor separates the two, which
#: is the first real result the playground produced and the reason the floor is tunable per
#: conversation: the page exists to find out whether any value works, not to assume one does.
MIN_SCORE = 1.4


def min_score(cfg) -> float:
    """This conversation's floor — `playground: {min_score: 1.1}`, else `MIN_SCORE`."""
    block = _block(getattr(cfg, "dir", None))
    try:
        return float(block.get("min_score", MIN_SCORE))
    except (TypeError, ValueError):
        return MIN_SCORE

#: Turns between firings of one feature. A run working one subject should be told once.
COOLDOWN = 6


@dataclass(frozen=True)
class Feature:
    slug: str
    title: str
    about: str
    components: str      # the query recipe, e.g. "A+O"
    stores: tuple[str, ...]


FEATURES: dict[str, Feature] = {
    "util-hint": Feature(
        slug="util-hint", title="Util hint on the observation tail",
        about="After a shell/script/write_util turn, name one util whose subject matches what "
              "the run just did by hand. Queries the evidential components (what was actually "
              "done and what came back), not the intention — a run re-implementing a util "
              "does not intend to.",
        components="A+O", stores=("utils",)),
    "memory-hint": Feature(
        slug="memory-hint", title="Memory-note pointer on the observation tail",
        about="Name a memory note whose `about` line matches the situation. Memory is "
              "pull-only today: a note the run never thinks to look for is a note it does not "
              "have. Queries the observation and the run's reading of it, which is where a "
              "surprise or a dead end shows up.",
        components="O+R", stores=("memory",)),
    "intent-field": Feature(
        slug="intent-field", title="Ask for a separate `intention` line",
        about="Append a standing instruction that the run state its next intention as one "
              "clause. Makes the query signal a clean clause rather than mixed "
              "reflection+intention prose, which a dense embedding would average into a "
              "centroid near neither subject.",
        components="I", stores=()),
}


def enabled_features(cfg) -> list[Feature]:
    """The features this conversation runs with — [] for anything not flagged playground."""
    wanted = _block(getattr(cfg, "dir", None)).get("features")
    slugs = [str(s) for s in wanted] if isinstance(wanted, list) else []
    return [FEATURES[s] for s in slugs if s in FEATURES]


def _block(conv_dir) -> dict:
    """The `playground: {on: bool, features: [...], min_score: float}` block, or {}.

    Empty for every routine and ordinary conversation — and for anything unreadable or not
    marked `on`: a malformed experiment block must leave the run exactly as production would be.
    Read from the conversation's OWN routine.yaml rather than from `RoutineConfig`, so the
    production config model stays untouched by the experiment.
    """
    if not conv_dir:
        return {}
    import yaml

    path = Path(conv_dir) / "routine.yaml"
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, ValueError, yaml.YAMLError):
        return {}
    block = raw.get("playground") if isinstance(raw, dict) else None
    if not isinstance(block, dict) or not block.get("on"):
        return {}
    return block


def firings_path(run_dir: Path) -> Path:
    """Where a playground run records what it surfaced — beside the run, not in a shared store."""
    return Path(run_dir) / "playground-firings.jsonl"


def log_firing(run_dir: Path, payload: dict) -> None:
    """Append one firing record. Never raises: a logging failure must not change a run."""
    try:
        path = firings_path(run_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"at": time.time(), **payload}, ensure_ascii=False) + "\n")
    except (OSError, TypeError, ValueError):
        pass


def query_text(components: str, action: dict, obs: dict) -> str:
    """The query for a recipe, built from what a turn actually carries today.

    The current composition has no separate `intention`, so `R` and `I` both read `say` — and
    that is exactly the conflation `intent-field` exists to remove. Said here rather than
    hidden, because a reader comparing recipes must know that `R` and `I` are the same text
    until that feature is on.
    """
    from ..engine.actionschema import canon

    parts: list[str] = []
    for comp in components.split("+"):
        if comp == "A":
            parts.append(canon(action))
        elif comp == "O":
            parts.append(_obs_text(obs))
        elif comp in ("R", "I"):
            parts.append(str(action.get("say") or ""))
    return " ".join(p for p in parts if p)


def _obs_text(obs: dict) -> str:
    """The observation as query material: its own text fields, never its bytes."""
    keys = ("name", "error", "stdout", "stderr", "listing", "summary", "brief")
    out: list[str] = [str(obs.get("kind") or ""),
                      *(str(obs[k])[:2000] for k in keys
                        if obs.get(k) and isinstance(obs.get(k), str))]
    return " ".join(out)
