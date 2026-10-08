"""The SIGNALS a change is judged on — read per run from the durable usage record, compared
between the runs before a change and the runs after it (readmodels/change_effects.py).

A routine's input differs from run to run, so nothing here scores a run's OUTPUT. What stays
comparable is the PROCESS the same routine runs for the same Done when, and each signal reads one
facet of it from the record's top-level fields and its `quality` (engine/runrecord.py):

- CORRECTNESS — did what the run claimed hold? failed or aborted runs, claims the verifier
  challenged and the ones that stood disputed, actions a caution held, util calls that failed.
- COMPLETENESS — did it do all it owed? the share of owed Done-when lines and goals it met,
  the lines left unmet, partial finishes, declared stages it never entered.
- EFFECTIVENESS — what did it cost to get there? tokens, turns and wall time (medians: one
  heavy run must not decide), the person's mid-run interventions, schema retries, the share of
  input re-read from the prompt cache.

A signal compares only runs that COUNTED it the same way (`COUNTED_SINCE`): a release that changes
the ruler is not a change in the routine. Runs of different routines are compared after
`normalized` divides each cost signal by its routine's own `baseline`, on the `POOLED` signals.

Each signal has a direction and a threshold below which a difference is noise. They are plain
constants — no statistics library — chosen so ONE odd run in a five-run window does not flip a
verdict; the windows themselves are small, so an `effect` is a pointer for a person to read,
never an automatic act.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from statistics import mean, median

from .change_keys import version_key


@dataclass(frozen=True)
class Signal:
    name: str
    dimension: str      # correctness · completeness · effectiveness
    better: str         # "lower" or "higher"
    how: str            # "mean" of per-run values, or "median" (heavy-tailed: tokens, turns, time)
    threshold: float    # mean: the absolute difference that counts; median: the ratio that does
    floor: float = 0.0  # median only: the absolute difference below which a ratio is noise
    label: str = ""


SIGNALS: tuple[Signal, ...] = (
    Signal("failed", "correctness", "lower", "mean", 0.2, label="failed or aborted runs"),
    Signal("challenged", "correctness", "lower", "mean", 0.5, label="claims challenged / run"),
    Signal("disputed", "correctness", "lower", "mean", 0.34, label="claims disputed / run"),
    Signal("holds", "correctness", "lower", "mean", 1.0, label="actions held / run"),
    Signal("util_failures", "correctness", "lower", "mean", 0.1, label="failed util calls"),
    Signal("met_rate", "completeness", "higher", "mean", 0.15, label="owed lines met"),
    Signal("unmet", "completeness", "lower", "mean", 0.5, label="lines left unmet / run"),
    Signal("partial", "completeness", "lower", "mean", 0.2, label="partial finishes"),
    Signal("stages_skipped", "completeness", "lower", "mean", 0.5, label="stages skipped / run"),
    Signal("tokens", "effectiveness", "lower", "median", 1.3, 20_000, label="tokens / run"),
    Signal("turns", "effectiveness", "lower", "median", 1.3, 5, label="turns / run"),
    Signal("elapsed_s", "effectiveness", "lower", "median", 1.3, 120, label="minutes / run"),
    Signal("interventions", "effectiveness", "lower", "mean", 0.5,
           label="interventions / run"),
    Signal("schema_retries", "effectiveness", "lower", "mean", 1.0, label="schema retries / run"),
    Signal("cache_share", "effectiveness", "higher", "mean", 0.15, label="cache read share"),
)
DIMENSIONS = ("correctness", "completeness", "effectiveness")
#: The release from which each signal is COUNTED the way it is counted now. A release that
#: changes what a signal counts — the ruler, not the routine — moves its entry, and a run before
#: it reads None for that signal, since a changed ruler measures no change. 0.369.0 had the claim
#: verifier check every Done-when `met` (before, only stopping conditions), 0.370.2 on every
#: endpoint, and 0.371.1 stopped a resumed run repeating a hold, an assist or a challenge it had
#: spent: on 2026-09-30 the fleet's challenged claims went from 0.03 a run to 1.2 overnight.
COUNTED_SINCE = {"challenged": "0.371.1", "disputed": "0.371.1", "holds": "0.371.1"}
#: The signals for runs POOLED across routines (readmodels/change_fleet.py, change_timeline.py):
#: a cost signal is first divided by its routine's own median (`normalized`), which leaves its
#: absolute floor (20 000 tokens) meaning nothing — the ratio threshold alone decides.
POOLED: tuple[Signal, ...] = tuple(replace(s, floor=0.0) if s.how == "median" else s
                                   for s in SIGNALS)


def _num(value: object) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


def _util_failure_share(utils: object) -> float | None:
    calls = ok = 0.0
    for cell in (utils.values() if isinstance(utils, dict) else ()):
        if isinstance(cell, dict):
            calls += sum(_num(v) for v in cell.values())
            ok += _num(cell.get("ok"))
    return (calls - ok) / calls if calls else None


def _count(q: dict, key: str) -> float | None:
    """A quality count, or None when the record does not carry it — a run rebuilt from history
    without its transcript knows its status and spend but not what its verifier challenged, and
    an unknown read as zero would be a measurement nobody made.
    """
    return _num(q[key]) if key in q else None


def run_signals(rec: dict) -> dict[str, float | None]:
    """One run's value per signal; None where the run has no reading (no lines owed, no util
    call, no cache traffic, or a quality count its record does not carry).
    """
    raw = rec.get("quality")
    q: dict = raw if isinstance(raw, dict) else {}
    status = str(rec.get("status") or "")
    owed = (_num(q["owed"]) - _num(q.get("not_due"))) if "owed" in q else 0.0
    read, write = _count(q, "cache_read"), _count(q, "cache_write")
    cache = (read or 0.0) + (write or 0.0)
    elapsed = _count(q, "elapsed_s")
    release = version_key(str((rec.get("fingerprint") or {}).get("engine") or "0"))
    q = {k: v for k, v in q.items()
         if k not in COUNTED_SINCE or release >= version_key(COUNTED_SINCE[k])}
    return {
        "failed": 1.0 if status in ("failed", "aborted") else 0.0,
        "challenged": _count(q, "challenged"),
        "disputed": _count(q, "disputed"),
        "holds": _count(q, "holds"),
        "util_failures": _util_failure_share(rec.get("utils")),
        "met_rate": _num(q.get("met")) / owed if owed > 0 else None,
        "unmet": _count(q, "unmet"),
        "partial": 1.0 if status == "partial" else 0.0,
        "stages_skipped": _count(q, "stages_skipped"),
        "tokens": _num(rec.get("tokens")),
        "turns": _num(rec.get("turns")),
        "elapsed_s": elapsed,
        "interventions": _count(q, "interventions"),
        "schema_retries": _count(q, "schema_retries"),
        "cache_share": (read or 0.0) / cache if cache else None,
    }


def aggregate(runs: list[dict[str, float | None]], sig: Signal) -> float | None:
    values = [v for r in runs if (v := r.get(sig.name)) is not None]
    if not values:
        return None
    return mean(values) if sig.how == "mean" else median(values)


def compare(before: float | None, after: float | None, sig: Signal) -> int:
    """+1 better, -1 worse, 0 the same or not comparable — after against before."""
    if before is None or after is None:
        return 0
    if sig.how == "mean":
        delta = after - before
        if abs(delta) < sig.threshold:
            return 0
    else:
        if abs(after - before) < sig.floor:
            return 0
        ratio = after / before if before else (sig.threshold if after else 1.0)
        if 1 / sig.threshold < ratio < sig.threshold:
            return 0
        delta = ratio - 1
    return 1 if (delta < 0) == (sig.better == "lower") else -1


def baseline(runs: list[dict]) -> dict[str, float | None]:
    """A routine's own median of each cost signal over the runs given — what `normalized`
    divides by.
    """
    values = [run_signals(r) for r in runs]
    return {s.name: aggregate(values, s) for s in SIGNALS if s.how == "median"}


def normalized(values: dict[str, float | None],
               base: dict[str, float | None]) -> dict[str, float | None]:
    """One run's signals with each cost signal as a ratio to its routine's `baseline` — so the
    runs of routines a hundred times apart in size can be pooled. No baseline, no reading.
    """
    out = dict(values)
    for name, b in base.items():
        v = out.get(name)
        out[name] = v / b if v is not None and b else None
    return out


def judge(before: list[dict], after: list[dict],
          signals: tuple[Signal, ...] = SIGNALS) -> dict:
    """Every signal's two sides and its verdict, then one verdict per dimension: `better`,
    `worse`, `mixed` (both inside it) or `same`. `signals` is `POOLED` for normalized runs.
    """
    out: list[dict] = []
    dims: dict[str, set[int]] = {d: set() for d in DIMENSIONS}
    for sig in signals:
        b, a = aggregate(before, sig), aggregate(after, sig)
        verdict = compare(b, a, sig)
        out.append({"name": sig.name, "label": sig.label, "dimension": sig.dimension,
                    "before": b, "after": a, "verdict": verdict})
        if verdict:
            dims[sig.dimension].add(verdict)
    word = {frozenset(): "same", frozenset({1}): "better", frozenset({-1}): "worse",
            frozenset({1, -1}): "mixed"}
    return {"signals": out, "dimensions": {d: word[frozenset(v)] for d, v in dims.items()}}


def overall(dimensions: dict[str, str]) -> str:
    """One verdict from the three dimensions': `improved` / `regressed` when they moved one way
    only, `mixed` when both, `no effect` when none moved.
    """
    moved = set(dimensions.values()) - {"same"}
    if not moved:
        return "no effect"
    if moved == {"better"}:
        return "improved"
    if moved == {"worse"}:
        return "regressed"
    return "mixed"
