"""The runner's ACTIVE MAP: how a live run is keyed, and how a run id finds it (D247).

A live run is identified by its HOME and its slug, never by the slug alone. The three homes
(`registry.HOME_KINDS`) are independent namespaces — a routine, a conversation and a detached
background task may all be called `tidy-up` — so a slug-keyed map made all three ONE entry:
the second to fire was refused as an "overrun" of the first (`Runner.fire_blocker`), and an
abort aimed at one stopped whichever of them the map happened to hold.

The run id is not a replacement key, which is why it is not the key. It is `slug:run-ts`
(`ids.run_id`) and each home claims its own run dirs (`runner._claim_run_dir`), so one id can
name a run in two homes at once. `abort` therefore takes the home its caller has already
resolved — the web route reads the run dir out of one home (`web/api_runs._run_dir`), the
daemon's own callers know which home they are servicing — and `find` refuses a contested id
rather than guess, because aborting the wrong run is worse than refusing to abort.

Split out of `runner.py`, which is at its size ceiling: the keying is also read by the reap,
the web abort and the lane/trigger/detached schedulers, so it is leaf knowledge none of them
should re-derive.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..paths import directly_under

if TYPE_CHECKING:
    from pathlib import Path

    from ..config import ServerConfig
    from .runner_state import ActiveRun

#: The home a dir under none of the three belongs to (a hand-built config in a test, a CLI
#: run): a routine, which is what it is. Also the `ActiveRun.home` default.
DEFAULT_HOME = "routines"


def home_of(server: ServerConfig, routine_dir: Path | str) -> str:
    """Which run home this run lives in — "routines", "conversations" or "background".

    Told by the DIR, never by `cfg.kind`: a detached task's routine.yaml carries no kind at
    all, and the home is where the run actually lives (`paths.directly_under`).
    """
    for kind in ("conversations", "background"):
        if directly_under(routine_dir, getattr(server, f"{kind}_home", None)):
            return kind
    return DEFAULT_HOME


def find(active: dict[tuple[str, str], ActiveRun], run_id: str,
         home: str = "") -> ActiveRun | None:
    """The live run `run_id` names, or None — for no match, or for an id two homes answer to.

    With `home` the lookup is exact. Without it the id is resolved across the homes, and a
    contested one returns None: every caller that can name the home does (see the module
    docstring), so the ambiguity is a caller that did not, and it must not be resolved by
    coin-toss.
    """
    hits = [run for run in active.values()
            if run.run_id == run_id and (not home or run.home == home)]
    return hits[0] if len(hits) == 1 else None
