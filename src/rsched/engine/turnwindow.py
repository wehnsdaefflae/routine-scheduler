"""What `max_turns` actually bounds: ONE resume WINDOW — said out loud, in one place.

A resume re-bases the turn budget at the resume point (`engine/boot.py` sets
`ctx.budget_base_turn`), deliberately: a resumed leg must get a working budget rather than
inheriting an already-spent one. So `max_turns` is a WINDOW ceiling while status.json's `turn`
is the run's CUMULATIVE count — and nothing carried the base turn that reconciles them. The
audit measured what that costs a reader (F564/D155):

    routine              max_turns  status.json turn  budgets.turns_left  finish actions
    freelance-radar            200               348                 179               0
    weightloss                 200               276                 175               0
    llmsectest-weekday         400               423                 338               0
    llmsectest-weekday         400               416                 382               0

`turn 348` beside `179 left of 200` reads as a ceiling that failed. It had not; the two
numbers simply mean different things, and nothing said which. Hence:

* `status_budgets` — the status.json budget block, which now carries `base_turn`, the
  window's own count and both ceilings beside `turns_left`, so the pair reconciles on its
  face: `window_turns + turns_left == max_turns`.
* `run_row` — the window a read surface puts beside a run's turn count. A run that never
  resumed (base 0) gets `None`, so the surfaces that show it stay exactly as short as before.
* `total_turn_cap` — the run-spanning cap (`max_total_turns`), DERIVED from `max_turns` when
  the config does not pin it, so an unattended resume chain is bounded at all. One
  derivation, called from `Budgets.from_config`, which every run resolves its ceilings
  through.
* `exhausted` / `left` — the wording a run is refused with. "turn budget exhausted" says
  nothing about WHICH of the two caps stopped it, which is the same illegibility above.
* `budget_block` — that same block for the LIVE run: the ONE reading status.json records and
  the per-turn tail is rendered from, so the prompt cannot tell a run what status.json denies.
* `budget_left` / `budget_tail` / `converge` — the two things a run is TOLD about its budget,
  worded in one place (D227). `budget_left` states what is left as a FACT on every turn;
  `converge` is the warning said once at a line. Both ride `engine/loop._tails` rather than
  the harness contract, which `engine/composer.py` composes ONCE at run start: a number that
  changes every turn would be frozen at turn 1 there and a lie on every later turn.
"""

from __future__ import annotations

#: `max_total_turns: 0` — "derive me from max_turns". Zero, because the two values an operator
#: can mean are already spoken for: a positive number is an explicit cap and -1 is a
#: deliberate unlimited, which stays honoured. Zero total turns is not a cap anyone could
#: want (a run with no turns cannot start), so it was free to mean this.
DERIVE = 0

#: The derived run-spanning cap: this many windows' worth of turns. Three because a resume is
#: the exception, not the rhythm — one window for the original leg and two more for the
#: OOM-resume (D99) and a stranded-message resume that can legitimately follow it. A chain
#: past that is a runaway, which is what the cap exists to stop.
TOTAL_TURN_MULTIPLE = 3


def total_turn_cap(max_turns: int, max_total_turns: int) -> int:
    """The effective `max_total_turns`: the configured value, or 3x the window when it says
    DERIVE.

    An EXPLICIT value always wins, including `-1` (deliberately unlimited). An unlimited
    WINDOW derives nothing — 3x unlimited is not a number — so the run-spanning cap stays
    unlimited too and `max_turns` remains, as before, the sole backstop.
    """
    if max_total_turns != DERIVE:
        return max_total_turns
    return -1 if max_turns < 0 else max_turns * TOTAL_TURN_MULTIPLE


def exhausted(limit: float, window_limit: float) -> str:
    """The refusal for the RUN-SPANNING cap — naming the cap it crossed and the one it did not.

    A run told "turn budget exhausted" cannot tell the window cap from the cumulative one;
    after a resume those two numbers differ by the base turn, which is exactly the confusion
    this module exists to end.
    """
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
                   spent: dict | None) -> dict:
    """status.json's `budgets` block — the window stated explicitly beside what is left.

    `turns_left` stays exactly what it was (this WINDOW's remainder, the ledger's own
    reading). What is new is everything needed to read it: the `base_turn` a resume re-based
    at, this window's own count, and both ceilings — so no reader has to find the routine's
    config to make 348 and 179 agree.
    """
    return {
        "turns_left": None if turns_left is None else int(turns_left),
        # the cumulative count status.json's own `turn` carries, named here as what it is
        "total_turns": int(turn),
        # 0 for a run that never resumed; otherwise the turn the resume re-based the window at
        "base_turn": int(base_turn),
        "window_turns": int(turn) - int(base_turn),
        "max_turns": int(max_turns),
        "max_total_turns": int(max_total_turns),
        "wall_clock_left_s": None if wall_left_min is None else int(wall_left_min * 60),
        # WHICH budget stopped the run, in fields: turns and wall clock were the only two
        # reconstructable from this block, so a token or cost cap — the majority — was
        # unknowable after the fact. The same snapshot as the counts above, so the block
        # never disagrees with itself.
        "spent": spent,
    }


def budget_block(ctx) -> dict:
    """THIS run's budget block — the one reading every surface shares.

    status.json records it (`run_context.write_status`) and the per-turn BUDGET LEFT tail is
    rendered from it (D227). One reading, deliberately: two readings of the same numbers can
    drift apart, and then the prompt tells a run something status.json denies.
    """
    ledger, meter = ctx.budgets.ledger(), ctx.meter()
    return status_budgets(
        turn=ctx.turn, base_turn=ctx.budget_base_turn, max_turns=ctx.budgets.max_turns,
        max_total_turns=ctx.budgets.max_total_turns,
        turns_left=ledger.remaining("turns", meter),
        wall_left_min=ledger.remaining("wall_clock", meter), spent=ledger.spent(meter))


def budget_left(block: dict, *, wall_ceiling_min: int, token_ceiling: int,
                tokens_left: int | None) -> str:
    """What is LEFT, as a FACT, for the observation tail — or "" when nothing is limited.

    D227 (operator, 2026-10-31): *"Why not just remaining turns, minutes, tokens (if
    limited)?"* — remainders and nothing else. No cost figure, no usage breakdown, no history.
    A run was told its ceilings once, at turn 1, and never its consumption, so it paced by the
    wall clock: on the run that raised this, 265 min since the run id against a 240-min ceiling
    while the engine had metered 144 (121 min dead in a container recreate) — "stop, you are 25
    over" with 96 min left. The alternative was hand-reading status.json: a turn spent, and a
    run reaching into engine internals for a fact about itself.

    `(if limited)` is taken literally: an unlimited axis prints NOTHING rather than the word
    "unlimited", because this text is paid on every turn of every run. Each remainder is given
    beside the ceiling it is a remainder of — the same reason `harness._subrun_allowance` does
    it ("a remaining count with no total is as unplannable as a total with no remainder") — and
    that is also what keeps `window_turns + turns_left == max_turns` legible (F564/D155). A
    resumed leg says its window was RE-BASED, so its fresh budget cannot read as a spent one.

    It states numbers and asks for nothing: the contract two screens up insists a budget is "a
    CEILING and a runaway BACKSTOP — never a pace, and never a ration", and the WARNING that
    does ask for something (`converge`) is an EVENT said at a line, not a per-turn countdown.
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
    """The wind-down tail for a budget that crossed a warning LINE — an event, said once.

    Lives beside `budget_left` so the two things a run is told about its budget are worded in
    one place: this one asks it to converge, that one only states what is left.
    """
    return (f"\n[BUDGET: {warning} — converge DELIBERATELY now: reach a point worth handing "
            "over, record what matters (LEDGER, state files), then finish with an authored "
            "summary. Once the budget is spent you get exactly ONE turn, and it can only be "
            "a finish.]")


def run_row(info) -> dict:
    """One run as the read surfaces take it, with the WINDOW its turn count belongs to.

    `turn` is unchanged — the cumulative count the runs table has always shown. `window` is
    `None` unless the run actually resumed, which is what keeps an ordinary run's row as
    short as it was: a parenthetical on every row would be noise on all but a handful.
    """
    base, ceiling = int(info.base_turn or 0), int(info.max_turns or 0)
    window = None
    if base > 0 and ceiling > 0:
        window = {"base_turn": base, "turns": int(info.turn) - base, "max_turns": ceiling}
    return {"run_id": info.run_id, "ts": info.ts, "state": info.state,
            "summary": info.summary[:200], "turn": info.turn, "usage": info.usage,
            "elapsed_s": info.elapsed_s, "window": window}
