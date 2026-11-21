"""What `max_turns` actually bounds: ONE resume WINDOW — said out loud, in one place.

A resume re-bases the turn budget at the resume point (`engine/boot.py` sets
`ctx.budget_base_turn`), deliberately: a resumed leg gets a working budget rather than an
already-spent one. So `max_turns` is a WINDOW ceiling while status.json's `turn` is the run's
CUMULATIVE count; `turn 348` beside `179 left of 200` must not read as a failed ceiling
(F564/D155). Hence:

* `status_budgets` — the status.json budget block: `base_turn`, the window's own count and both
  ceilings beside `turns_left`, so `window_turns + turns_left == max_turns` on its face.
* `run_row` — the window a read surface puts beside a run's turn count (`None` if never resumed).
* `total_turn_cap` — the run-spanning cap (`max_total_turns`), DERIVED from `max_turns` when the
  config does not pin it, so an unattended resume chain is bounded at all
  (`Budgets.from_config`).
* `exhausted` / `left` — refusal wording that names WHICH of the two caps stopped a run.
* `budget_block` — that same block for the LIVE run: the ONE reading status.json records and
  the per-turn tail is rendered from.
* `budget_left` / `budget_tail` / `converge` — what a run is TOLD about its budget (D227):
  what is left as a per-turn FACT, and a warning said once at a line. Both ride
  `engine/loop._tails`, never the harness contract, which is composed ONCE at run start and
  would freeze a per-turn number at turn 1.
"""

from __future__ import annotations

#: `max_total_turns: 0` — "derive me from max_turns". Positive is an explicit cap and -1 a
#: deliberate unlimited; zero total turns is no usable cap, so it was free to mean this.
DERIVE = 0

#: The derived run-spanning cap: this many windows' worth of turns — the original leg plus two
#: legitimate resumes (e.g. OOM, D99); a chain past that is a runaway.
TOTAL_TURN_MULTIPLE = 3


def total_turn_cap(max_turns: int, max_total_turns: int) -> int:
    """The effective `max_total_turns`: the configured value, or 3x the window when it says
    DERIVE.

    An EXPLICIT value always wins, including `-1` (deliberately unlimited). An unlimited
    WINDOW derives an unlimited cap too.
    """
    if max_total_turns != DERIVE:
        return max_total_turns
    return -1 if max_turns < 0 else max_turns * TOTAL_TURN_MULTIPLE


def exhausted(limit: float, window_limit: float) -> str:
    """The refusal for the RUN-SPANNING cap — naming the cap it crossed and the one it did not."""
    window = "unlimited" if window_limit < 0 else f"{int(window_limit)}"
    return (f"max_total_turns exhausted ({int(limit)} turns across all resume windows) — "
            f"the run-spanning cap, not this window's max_turns ({window})")


def left(amount: float, limit: float, window_limit: float) -> str:
    """The wind-down warning for the run-spanning cap, in the same vocabulary."""
    window = "unlimited" if window_limit < 0 else f"{int(window_limit)}"
    return (f"~{int(amount)} turns left of max_total_turns ({int(limit)} across all resume "
            f"windows; this window's max_turns is {window})")


def status_budgets(*, turn: int, base_turn: int, max_turns: int, max_total_turns: int,
                   turns_left: float | None, wall_left_min: float | None,
                   spent: dict | None, legs: list[dict] | None = None) -> dict:
    """status.json's `budgets` block — the window stated explicitly beside what is left.

    `turns_left` is this WINDOW's remainder; `base_turn`, the window's own count and both
    ceilings are what a reader needs to make it agree with the cumulative `turn`.

    `legs` carries the windows of the legs BEFORE this one (`history.prior_legs`), because this
    block REPLACES the file's previous one (F895).
    """
    return {
        # the prior legs' windows, oldest first. "What ceiling did the work run under?" reads
        # legs[0], never this block.
        **({"legs": [dict(leg) for leg in legs]} if legs else {}),
        "turns_left": None if turns_left is None else int(turns_left),
        # the cumulative count status.json's own `turn` carries, named here as what it is
        "total_turns": int(turn),
        # 0 for a run that never resumed; otherwise the turn the resume re-based the window at
        "base_turn": int(base_turn),
        "window_turns": int(turn) - int(base_turn),
        "max_turns": int(max_turns),
        "max_total_turns": int(max_total_turns),
        "wall_clock_left_s": None if wall_left_min is None else int(wall_left_min * 60),
        # WHICH budget stopped the run (a token or cost cap is not reconstructable otherwise);
        # the same snapshot as the counts above.
        "spent": spent,
    }


def budget_block(ctx) -> dict:
    """THIS run's budget block — the one reading every surface shares.

    status.json records it (`run_context.write_status`) and the per-turn BUDGET LEFT tail is
    rendered from it (D227) — one reading, so the prompt cannot contradict status.json.
    """
    ledger, meter = ctx.budgets.ledger(), ctx.meter()
    return status_budgets(
        turn=ctx.turn, base_turn=ctx.budget_base_turn, max_turns=ctx.budgets.max_turns,
        max_total_turns=ctx.budgets.max_total_turns,
        turns_left=ledger.remaining("turns", meter),
        wall_left_min=ledger.remaining("wall_clock", meter), spent=ledger.spent(meter),
        # the prior legs' windows, reseeded onto the ctx at boot (history.prior_legs); getattr
        # because a fresh run never sets it.
        legs=getattr(ctx, "prior_legs", None))


def budget_left(block: dict, *, wall_ceiling_min: int, token_ceiling: int,
                tokens_left: int | None) -> str:
    """What is LEFT, as a FACT, for the observation tail — or "" when nothing is limited.

    D227: remainders and nothing else — no cost figure, no usage breakdown. Without it a run
    paced by the wall clock, which is wrong after a resume.

    An unlimited axis prints NOTHING, because this text is paid on every turn of every run.
    Each remainder sits beside its ceiling, which keeps `window_turns + turns_left ==
    max_turns` legible; a resumed leg says its window was RE-BASED.

    It states numbers and asks for nothing — a budget is a backstop, never a pace; the
    warning that does ask (`converge`) is an EVENT said at a line.
    """
    parts: list[str] = []
    if block["turns_left"] is not None and block["max_turns"] >= 0:
        turns = f"{block['turns_left']} of {block['max_turns']} turns"
        if block["base_turn"]:
            turns += f" (this resume window, re-based at turn {block['base_turn']})"
        parts.append(turns)
    if block["wall_clock_left_s"] is not None and wall_ceiling_min >= 0:
        parts.append(f"{block['wall_clock_left_s'] // 60} of {wall_ceiling_min} min")
    if tokens_left is not None and token_ceiling >= 0:
        parts.append(f"{tokens_left:,} of {token_ceiling:,} tokens")
    return f"\n[BUDGET LEFT: {' · '.join(parts)}]" if parts else ""


def budget_tail(ctx) -> str:
    """The BUDGET LEFT tail for this turn, read off `budget_block` (engine/loop._tails)."""
    b = ctx.budgets
    return budget_left(budget_block(ctx), wall_ceiling_min=b.max_wall_clock_min,
                       token_ceiling=b.max_total_tokens, tokens_left=ctx.tokens_remaining())


def converge(warning: str) -> str:
    """The wind-down tail for a budget that crossed a warning LINE — an event, said once."""
    return (f"\n[BUDGET: {warning} — converge DELIBERATELY now: reach a point worth handing "
            "over, then finish with an authored summary — the engine records it in "
            "LEDGER.md. Once the budget is spent you get exactly ONE turn, and it can only be "
            "a finish.]")


def run_row(info) -> dict:
    """One run as the read surfaces take it, with the WINDOW its turn count belongs to.

    `turn` is the cumulative count. `window` is `None` unless the run actually resumed, so an
    ordinary run's row stays short.
    """
    base, ceiling = int(info.base_turn or 0), int(info.max_turns or 0)
    window = None
    if base > 0 and ceiling > 0:
        window = {"base_turn": base, "turns": int(info.turn) - base, "max_turns": ceiling}
    return {"run_id": info.run_id, "ts": info.ts, "state": info.state,
            "summary": info.summary[:200], "turn": info.turn, "usage": info.usage,
            "elapsed_s": info.elapsed_s, "window": window}
