"""The PER-CALL cancel: one call stopped, the run carrying on (F586, decided as D160-C).

Lifted out of `engine/control.py` (F529) when that module reached the house 350-line bar and
the turn-boundary freight clock had nowhere to go. The seam is the one control.py's own
docstring already drew: everything there runs BETWEEN turns and mutates only the loop's
message list, while this is read MID-turn, by a util, script or shell command in flight —
`utils_run.run_jailed` polls it, and `engine/executor` hands it to every jailed call. A
cancel is not a message feed, and the module about message feeds is not where it belongs.

`control` re-exports the name, so `control.cancelled_for_turn` still resolves and no caller
moved.
"""

from __future__ import annotations

from collections.abc import Callable

from ..paths import read_json


def cancelled_for_turn(ctx) -> Callable[[], bool]:
    """The per-call cancel check a jailed command polls: control.json's `cancel_action` names
    the TURN whose call the operator stopped, and this returns True only while that turn is
    the one running.

    KEYED BY TURN, and that is the whole safety property. A bare `{"cancel": true}` flag is read
    by whatever call is in flight when the engine next looks — which, for a cancel clicked just
    as a long call finishes, is the NEXT call, one the operator never asked to stop. The turn is
    what the UI's red × already knows (it renders a specific action's row), so the intent
    travels as "stop the call of turn N" and expires by itself.

    It is a POLLED FILE READ, not the abort's mechanism: `control.request_abort`'s flag is
    process-wide and set by a signal handler, while a cancel arrives from the web with the
    engine blocked inside `run_jailed`'s wait — there is no signal and no turn boundary to read
    it at. So this mirrors `control.pause_gate`: `paths.read_json` on the run's own
    control.json, which is web-owned and engine-read. A missing or malformed file reads as "not
    cancelled" — the file is advisory, and a command must never be killed by a parse error. So
    does a value that is not a plain int, BOOLS INCLUDED: `True == 1` in Python, so a bare
    `{"cancel_action": true}` would otherwise stop turn 1's call. And a context with no run dir
    at all (a test, a CLI call) never cancels, the way `run_context._never_aborted` answers for
    the abort.
    """
    # A BACKGROUND call carries its own stop predicate, keyed by its handle (engine/background:
    # `_cancellable_ctx`). It takes precedence because the turn key cannot work there: a
    # background call outlives the turn that started it, so a check keyed on `ctx.turn` can
    # never fire again once the run moves on — which left `kill handle=…` unable to stop even a
    # `shell` blocked on a ten-minute subprocess. The file channel stays for the foreground.
    if (stop := getattr(ctx, "background_stop", None)) is not None:
        return stop
    run_dir = getattr(ctx, "root_run_dir", None)
    turn = getattr(ctx, "turn", 0)
    if run_dir is None:
        # A context no loop drives (a test, a CLI call, a selftest) has no run dir to read, and
        # the right answer there is `_never_aborted`'s: never cancelled. Raising instead would
        # make the cancel's presence break every caller that never had one.
        return lambda: False
    control = run_dir / "control.json"

    def cancelled() -> bool:
        wanted = read_json(control)
        if not isinstance(wanted, dict):
            return False
        value = wanted.get("cancel_action")
        # `isinstance(True, int)` is True in Python, so a bare `{"cancel_action": true}` —
        # exactly the shape this design rejects — would otherwise cancel turn 1's call.
        if isinstance(value, bool) or not isinstance(value, int):
            return False
        return value == turn

    return cancelled
