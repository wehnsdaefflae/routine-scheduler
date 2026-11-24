"""A util's MODEL SPEND, booked against the run that called it.

D288 step 1. The operator's resolution was *"Convert fully"*: `llm` and `decide` stop being
action kinds and one util carries both capabilities. Removing them is cheap; removing them
WITHOUT this is a regression dressed as a cleanup, because `llmaction.do_llm` and
`decideaction.do_decide` each call `ctx.add_usage(...)` and a util call books NOTHING. Convert
the actions away as things stand and every token a run spends on inference disappears from its
own meter, from the usage stream, and from the per-routine cost the operator reads.

HOW IT TRAVELS: the engine hands the util a SINK PATH in the environment — through the same
`extra_secrets` channel `RSCHED_API_TOKEN` and `RSCHED_ROUTINE` already ride
(`exec_env._extra_secrets`), so the util RUNNER needs no parameter of its own; it is at its size
ceiling and `_child_env` moved out of it (R2622) for that reason. A util that made model calls
appends one JSON object per call to that file. The engine reads it after the call and folds what
it finds through `ctx.add_usage`, which is the single writer of a run's usage and already handles
the background-thread parking D167 requires. A util that makes no model call writes nothing and
the file never exists, which is the overwhelming majority of calls.

WHY A FILE AND NOT STDOUT: stdout is the util's ANSWER, and only some invocations are `--json`.
Parsing spend out of prose would make a util's output format part of the accounting contract —
so a `--json`-less call would silently book nothing, which is exactly today's bug with extra
steps. The sink is out of band, so every call form reports the same way.

IT NEVER FAILS A CALL. A missing, empty, unparseable or hostile sink books nothing and raises
nothing: a util's accounting must not be able to take down the util's work. The cost of a
dropped reading is one unbooked call; the cost of a raise here is the run.
"""

from __future__ import annotations

import json
import tempfile
from collections.abc import Callable
from pathlib import Path

#: env var naming the file a util appends its model-spend readings to. The NAME is the contract:
#: utils read it env-first, like every other engine-injected value.
SINK_VAR = "RSCHED_USAGE_SINK"

#: the keys a reading may carry, mapped to the usage shape the run's meter folds (`fold_usage`).
#: Both spellings are accepted because both are already in the house: the engine's own
#: `Completion.usage` uses `in`/`out`, and an OpenAI-compatible provider answers
#: `prompt_tokens`/`completion_tokens`, which is what a util has in hand.
_FIELDS: dict[str, str] = {
    "in": "in", "out": "out",
    "prompt_tokens": "in", "completion_tokens": "out",
    "cache_read": "cache_read", "cache_write": "cache_write",
    "reasoning": "reasoning",
}


def _reading(row: object) -> dict[str, int]:
    """One sink row as a usage reading — `{}` for anything that is not one.

    A row is a JSON object of token counts. A negative count is DROPPED rather than clamped: it
    cannot be a real reading, and booking it would let a util reduce a run's measured spend.
    """
    if not isinstance(row, dict):
        return {}
    out: dict[str, int] = {}
    for key, target in _FIELDS.items():
        val = row.get(key)
        if isinstance(val, bool) or not isinstance(val, int | float):
            continue
        if val < 0:
            continue
        out[target] = out.get(target, 0) + int(val)
    return out


def run_and_book(ctx: object, run: Callable[..., tuple[int, str, str]], /,
                 extra_secrets: dict[str, str] | None = None,
                 **kwargs: object) -> tuple[int, str, str]:
    """Run one util through `run` with a spend sink, and BOOK what it bought against `ctx`.

    One responsibility — the call and its accounting are inseparable: a sink nobody collects is
    worse than none, because it reads like the spend is handled. `do_util` holds it rather than
    inlining it because that function is already at its branch ceiling, and a caller cannot
    forget the booking half if the two cannot be called apart.

    THE SINK TRAVELS AS AN ENGINE-INJECTED ENV VALUE, through `extra_secrets` — the same channel
    `RSCHED_API_TOKEN` and `RSCHED_ROUTINE` already ride (`exec_env._extra_secrets`), which
    reaches every util unconditionally and under the declared-only gate's own merge. So the util
    RUNNER needs no parameter of its own: it is at its size ceiling, and `_child_env` moved out of
    it (R2622) for exactly that reason.

    `ctx.add_usage` is read with `getattr` and FAILS OPEN, for the reason `_extra_secrets` itself
    does: this path runs for every util call and some callers hand in a CONTEXT STAND-IN carrying
    only what their own subject needs. An unbooked reading costs one call's accounting; an
    AttributeError here would kill the util call itself.
    """
    with tempfile.TemporaryDirectory(prefix="rsched-spend-") as spend_dir:
        sink = Path(spend_dir) / "usage.jsonl"
        result = run(extra_secrets={**(extra_secrets or {}), SINK_VAR: str(sink)}, **kwargs)
        spent = collect(sink)
    if spent and callable(book := getattr(ctx, "add_usage", None)):
        book(spent)
    return result


def collect(path: Path | str | None) -> dict[str, int]:
    """Every reading in the sink at `path`, folded into one usage dict.

    One JSON object per line (a util may make several model calls in one invocation, and each
    appends its own); a line that is not an object contributes nothing. Never raises.
    """
    if not path:
        return {}
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, ValueError):
        return {}
    total: dict[str, int] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        for key, val in _reading(row).items():
            total[key] = total.get(key, 0) + val
    return total
