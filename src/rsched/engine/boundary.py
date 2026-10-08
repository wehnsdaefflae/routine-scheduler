"""STAGE-BOUNDARY COMPACTION — whether archiving the middle at a boundary PAYS.

The size gate (`window._archive_if_needed`) compacts when the prompt approaches the window, and
on a 1M-token window that is almost never: 14 passes in 160 fleet runs (2026-09-17..10-08), while
the median request carried 148k tokens of context and re-read all of it, every turn, at the cache
price. A run that has finished a stage carries the whole of it into every turn it has left.

So at a stage boundary the gate asks a second question: does archiving the middle NOW cost less
than carrying it to the end of the run? The test is NVIDIA SoL-Pi's Online Context Compact
(arXiv 2609.20519) — compact at a completed plan step when the projected input savings repay the
cache rewrite — with two differences. The stages are the recipe's own (`stages/*.md`, which the
engine already tracks), so no plan tool is needed; and the archival call is priced too, because
here it is a real model call over the whole middle where Pi's native compaction is cheap.

Every figure is in units of ONE UNCACHED INPUT TOKEN of the run's model, at Anthropic's list
ratios (the provider this fleet runs on): a cache read 0.1, a cache write 1.25, an output token
5. Which of them apply is what the run's provider has SHOWN, never configured: no cache read yet
means every turn re-reads at full price, and no cache write means the provider charges none.

    saving     = before − after               tokens each later turn no longer carries
    per turn   = saving × read
    cost       = (after − system) × (write − read)      the kept prefix written once more
               + middle × write                         the archival call reads the middle…
               + middle × ARCHIVAL_OUTPUT_SHARE × OUT   …and writes the history files
    compact   ⇔ cost / per turn × MARGIN  ≤  horizon

The HORIZON is the turns the run still has: turns per stage reached so far × stages still ahead
(the one just entered counts), capped by the turn budget. A stage revisited reads as no new
boundary and a loop is undercounted, which errs toward NOT compacting. A pass taken here does not
stop paying where the size gate would have tripped — it moves that pass later rather than adding
one — so the horizon is not capped at the gate.
"""

from __future__ import annotations

from .compaction import KEEP_HEAD_MSGS, KEEP_TAIL_MSGS, estimate_input_tokens, maybe_compact

#: Price of a token the provider serves from its cache, per uncached input token.
CACHE_READ = 0.1
#: Price of a token written into the cache. A provider that reports no cache writes charges none.
CACHE_WRITE = 1.25
#: Price of an output token.
OUTPUT = 5.0
#: How many times over a pass must repay itself within the horizon. The horizon is a guess
#: (turns per stage vary several-fold within one recipe) and a pass has a cost no price captures —
#: the middle leaves the prompt verbatim — so a pass that only just pays is not taken. Replayed
#: over 525 real stage boundaries (166 staged runs, to 2026-10-08), 2 kept 98% of the first
#: passes' net saving with 77 passes instead of 120, and halved the passes that lost money.
MARGIN = 2.0
#: What the archival call writes per token of middle it reads: 3.1-5.4% across the three archives
#: that landed on this instance (llmsectest-weekday, 2026-10-04/05: 30,115 out of 554,173 in,
#: 24,619 of 798,605, 31,904 of 902,594).
ARCHIVAL_OUTPUT_SHARE = 0.05


def assess(loop, size: int) -> tuple[str, dict]:
    """`(stage, economics)` when this turn is a stage boundary, else `("", {})`.

    A BOUNDARY is the run reaching a stage it had not reached: the executor-stamped phase moving
    (a `stages/<name>.md` read) or a stage joining the visited set (which also counts the phases
    the run wrote to its own `state/phase.json` — the stronger signal, and the only one a run
    that never re-reads its modules gives: `llmsectest-weekday` routes by its cursor alone,
    F563). The first call only learns where the run stands, so a fresh run's first turn and a
    resumed leg's first turn are not boundaries.
    """
    ctx = loop.ctx
    cov = ctx.stage_coverage()
    mark = (ctx.phase, tuple(cov["entered"]))
    last, loop._stage_mark = loop._stage_mark, mark
    if last is None or mark == last or not cov["declared"]:
        return "", {}
    moved = bool(ctx.phase) and ctx.phase != last[0]
    fresh = [name for name in mark[1] if name not in last[1]]
    if not (moved or fresh):  # the recipe lost a stage mid-run: nothing was reached
        return "", {}
    stage = ctx.phase if moved else fresh[0]
    if getattr(loop, "_archival", None) is not None:
        # One archive at a time (`archival.start` drops a second): a pass taken now would elide
        # a middle that never reaches history/. Boundaries come every few turns and an archive
        # takes minutes, so this one is let go rather than taken without its archive.
        return stage, {}
    turns_left = ctx.turns_remaining()
    ahead = len(cov["declared"]) - len(cov["entered"]) + 1
    horizon = ctx.turn / max(1, len(cov["entered"])) * ahead
    if turns_left is not None:
        horizon = min(horizon, turns_left)
    messages = loop.messages
    _, info = maybe_compact(messages, loop.turn_records, 0)
    if info is None:          # no middle to archive: nothing to weigh
        return stage, {}
    return stage, decide(
        before=size, after=info["after_estimated_tokens"],
        middle=estimate_input_tokens(messages[KEEP_HEAD_MSGS:len(messages) - KEEP_TAIL_MSGS]),
        system=estimate_input_tokens(messages[:1]), horizon=horizon, usage=ctx.usage)


def decide(*, before: int, after: int, middle: int, system: int, horizon: float,
           usage: dict) -> dict:
    """The breakeven test, every figure it used returned beside the verdict so the transcript
    can say why a boundary compacted — and the constants above can be tuned from that record.
    """
    read = CACHE_READ if usage.get("cached_in") else 1.0
    write = CACHE_WRITE if usage.get("cache_write") else 1.0
    saving = before - after
    cost = (max(0, after - system) * max(0.0, write - read)
            + middle * write + middle * ARCHIVAL_OUTPUT_SHARE * OUTPUT)
    per_turn = saving * read
    out = {"saving_tokens": saving, "cost": round(cost), "per_turn": round(per_turn),
           "horizon_turns": round(horizon, 1)}
    if per_turn <= 0:
        return {**out, "breakeven_turns": None, "compact": False}
    breakeven = cost / per_turn
    return {**out, "breakeven_turns": round(breakeven, 1),
            "compact": breakeven * MARGIN <= horizon}
