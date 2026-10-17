"""The routine's OUTCOME — what it exists to produce for its principal — measured each run.

Every judge the instance had read the routine's own account: a run finished `ok`, its Done-when
lines were met, its self-checks exited 0. freelance-radar sent 89 applications in four months,
won zero interviews, and every one of those judges said it was healthy; grants-radar found no
new programme in 14 runs on the same verdict. What none of them asked is whether the routine
produced what it exists for. This module is where that question gets a NUMBER, every run, that
the routine cannot talk its way around.

The contract has two halves, owned apart:

- **The declaration is config** — `outcome:` in routine.yaml (`config/outcome.py`): who the
  results are for (`principal`) and each measure (`id`, a `label` a person reads, its `kind` —
  a `count` is a running total, a `level` a current reading — and which way is `better`). The
  operator owns what the routine is FOR; no run can redefine its own success.
- **The computation is the routine's** — `scripts/outcome.py`, an ordinary routine script that
  reads the routine's OWN state and prints one JSON object `{<measure id>: <number>, …}` on its
  last line. The engine runs it after the run's last turn, in the run's jail, with no secret and
  no network beyond what its header declares (the util model, `rsched/scripts.py`).

What the engine records is exactly what the script printed, checked only for SHAPE: every
declared measure present as a number. A script that fails, prints something else or omits a
measure records `{"error": …}` — a run whose outcome could not be read, never a zero. The
reading lands in status.json and in the run's usage row (`outcome_measures`), so outcome and
cost form ONE time series (`readmodels/outcome_series.py`) — the evidence the routine-improver's
outcome review reads first, before anything the routine says about itself.
"""

from __future__ import annotations

import json

from .. import sandbox, scripts
from .run_context import RunContext

SCRIPT = "outcome"
#: A results script reads local state; a minute is generous, and a run's end must not hang on it.
TIMEOUT_S = 60


def declared(cfg) -> list[dict]:
    """The routine's declared measures (config/outcome.py), [] when it declares none."""
    contract = getattr(cfg, "outcome", None)
    return [m.model_dump() for m in contract.measures] if contract else []


def parse(stdout: str, measures: list[dict]) -> dict:
    """`{"values": {...}}` from the script's last JSON line, or `{"error": …}`."""
    lines = [ln for ln in stdout.strip().splitlines() if ln.strip()]
    try:
        obj = json.loads(lines[-1]) if lines else None
    except ValueError:
        obj = None
    if not isinstance(obj, dict):
        return {"error": "scripts/outcome.py printed no JSON object on its last line"}
    values: dict[str, float] = {}
    missing: list[str] = []
    for m in measures:
        v = obj.get(m["id"])
        if isinstance(v, int | float) and not isinstance(v, bool):
            values[m["id"]] = v
        else:
            missing.append(m["id"])
    if missing:
        return {"error": f"no number for {', '.join(missing)}", "values": values}
    return {"values": values}


def measure(ctx: RunContext) -> dict | None:
    """Run the routine's results script once; None when it declares no outcome."""
    measures = declared(ctx.routine)
    if not measures:
        return None
    home = ctx.routine.dir
    if not scripts.exists(home, SCRIPT):
        return {"error": "the routine declares an outcome but has no scripts/outcome.py"}
    code, out, err = scripts.run_script(
        home, SCRIPT, [], policy=sandbox.policy_for_ctx(ctx),
        libraries_home=ctx.server.libraries_home, env_secrets={}, timeout=TIMEOUT_S)
    if code != 0:
        return {"error": f"scripts/outcome.py exited {code}: {(err or out).strip()[-300:]}"}
    return parse(out, measures)
