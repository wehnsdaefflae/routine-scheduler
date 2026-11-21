"""The PER-CALL cancel: one call stopped, the run carrying on (F586, decided as D160-C).

Apart from `engine/control.py`, whose work runs BETWEEN turns: this is read MID-turn by a call
in flight (`utils_run.run_jailed` polls it; `engine/executor` hands it to every jailed call).
`control` re-exports the name.
"""

from __future__ import annotations

from collections.abc import Callable

from ..paths import read_json


def cancelled_for_turn(ctx) -> Callable[[], bool]:
    """The per-call cancel check a jailed command polls: control.json's `cancel_action` names
    the TURN whose call the operator stopped, and this returns True only while that turn is
    the one running.

    KEYED BY TURN, and that is the whole safety property: a bare flag would be read by whatever
    call is in flight next — possibly one the operator never asked to stop. "Stop the call of
    turn N" expires by itself.

    A POLLED FILE READ (like `control.pause_gate`), because the engine is blocked inside
    `run_jailed`'s wait with no signal or turn boundary to read it at. A missing or malformed
    file, or a value that is not a plain int (BOOLS INCLUDED — `True == 1`), reads as "not
    cancelled": a command must never be killed by a parse error. A context with no run dir never
    cancels.
    """
    # A BACKGROUND call carries its own stop predicate, keyed by its handle (engine/background:
    # `_cancellable_ctx`): it outlives the turn that started it, so a turn key could never fire
    # again. The file channel stays for the foreground.
    if (stop := getattr(ctx, "background_stop", None)) is not None:
        return stop
    run_dir = getattr(ctx, "root_run_dir", None)
    turn = getattr(ctx, "turn", 0)
    if run_dir is None:
        # no loop drives this context: never cancelled, as `_never_aborted` answers the abort
        return lambda: False
    control = run_dir / "control.json"

    def cancelled() -> bool:
        wanted = read_json(control)
        if not isinstance(wanted, dict):
            return False
        value = wanted.get("cancel_action")
        # `isinstance(True, int)` is True, so `{"cancel_action": true}` would cancel turn 1
        if isinstance(value, bool) or not isinstance(value, int):
            return False
        return value == turn

    return cancelled
