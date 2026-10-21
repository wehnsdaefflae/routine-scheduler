"""WHOSE regression is it — this routine's, or the host's? (F568)

`run_health.recent_trend` answers "are this routine's last runs worse than the ones before
them". It cannot answer the next question, and the alarm built on it asserted an answer
anyway: every flagged window was reported as THAT ROUTINE's regression. On 2026-09-27/28 a
host-wide condition degraded everything that ran, and the stream carried three findings
naming three routines (`llmsectest-weekday:20260927-060004`,
`library-sync:20260928-000003`, F574) for one problem with the box. An operator reading them
had three accusations and no mention of the host.

So before a finding names a routine, the SAME window is read across the fleet: every other
routine that had comparable runs inside the subject's window is judged by `regression_flag`
against its own runs before it — one heuristic, the same thresholds, two subjects. D181 tuned
the SENSITIVITY of the alarm (`BALLOON_RATIO`, pinned by tests/test_balloon_ratio.py); this is
the ATTRIBUTION half, and it changes no threshold.

Three verdicts, never two, because the third is what honesty costs: `host` (shared), `routine`
(isolated) and `unattributable` — too few comparable routines ran the window to tell the cases
apart. The 2026-09-27/28 firings are themselves that third case: exactly ONE other routine had
comparable runs in those windows, so the degradation was real and its cause was not
establishable from the stream. "Cannot attribute, 1 other routine in the window" is a true
sentence; "library-sync regressed" was not.

MEASURED on this instance's own stream (2026-10-21: 1,738 folded depth-0 runs, 217 actors,
134 moments where the time-keyed trend flagged). For each flagging moment, the share of
comparable routines that degraded over the same calendar span:

    median 0.11 · p75 0.29 · p90 0.36 · p95 0.40 · max 0.50

Half the comparable fleet has degraded together (once); MORE than half never has. That is why
HOST_SHARE is a STRICT majority: it reclassifies none of this instance's real history as a
host condition, so a `host` verdict means something the stream has never produced by chance.
7 of those 134 moments (5.2%) had fewer than HOST_MIN_PEERS comparable routines — the
unattributable case, the 09-27/28 night among them. The measurement script is
`scheduler-builder` run 20261021-130003/sub/6, `scripts/measure_fleet_share.py`.

Pure over usage records: no config, no filesystem, no clock. `engine/runtime._log_cost_trend`
is the one caller, and `event_payload` below is where the wording the operator reads is built,
because the complaint F568 records was a misleading SENTENCE as much as a wrong judgement.
"""

from __future__ import annotations

from collections.abc import Callable

from .run_health import MIN_RUNS, REGRESSION_WINDOW, regression_flag
from .stamps import instant
from .usage_stream import fold_legs

#: Comparable routines needed before either verdict is available. Below three, a "majority"
#: is one or two routines on a quiet night — which is how a two-routine coincidence would
#: become a host outage, and a single busy neighbour would acquit a real regression. Three is
#: the floor `MIN_RUNS` uses for the same reason ("below 3 a comparison is a coin flip"), and
#: it leaves 127 of this instance's 134 flagging moments attributable.
HOST_MIN_PEERS = 3
#: The share of comparable routines that must degrade WITH the subject for the finding to be
#: about the host. Compared STRICTLY (`share > HOST_SHARE`): the measured maximum over 134
#: real moments is exactly 0.50, so a strict majority rewrites none of them (the docstring
#: above carries the distribution).
HOST_SHARE = 0.5

HOST = "host"
ROUTINE = "routine"
UNATTRIBUTABLE = "unattributable"


def _span(runs: list[dict], window: int) -> tuple[object, object]:
    """The calendar span of the subject's flagged window — the first and last stamp of its
    last `window` runs, read as INSTANTS (`ids.now_iso` writes local time with an offset, so
    string compares move the edge by that offset). A window whose rows carry no readable
    stamp places nothing, and places it honestly: (None, None).
    """
    stamps = [instant(r.get("ts")) for r in runs[-window:]]
    known = sorted(s for s in stamps if s is not None)
    return (known[0], known[-1]) if known else (None, None)


def attribute(fleet: list[dict], own: list[dict], slug: str, *,
              name_of: Callable[[dict], str] | None = None,
              window: int = REGRESSION_WINDOW) -> dict:
    """Is the subject's flagged window ITS regression or the host's?

    `fleet` is every depth-0 usage record on the instance (legs are folded here), `own` the
    subject's, `slug` its name. `name_of` is `incarnations.resolver(home)` where the caller
    has one: a slug that was archived and created again is two routines, and the later one's
    runs must not vote as the earlier one's. Grouping falls back to the record's own
    `routine` — a peer's vote is "did the routines that ran beside it degrade", so an
    unresolved boundary costs at most one noisy vote, never the subject's own history.

    Returns {scope, peers_compared, peers_degraded, share, compared_names, degraded_names,
    sentence}: `scope` is host · routine · unattributable, and `sentence` is the operator's
    wording (see `event_payload`).
    """
    own_runs = fold_legs(own)
    start, end = _span(own_runs, window)
    if start is None:
        return _verdict(UNATTRIBUTABLE, [], [], slug,
                        "the window carries no readable stamp, so no comparable window "
                        "could be cut for the other routines")

    compared: list[str] = []
    degraded: list[str] = []
    for peer, runs in _by_routine(fleet, slug, name_of).items():
        inside, before = [], []
        for rec in runs:
            when = instant(rec.get("ts"))
            if when is None:
                continue                       # an unstamped run places nothing
            (inside if start <= when <= end else before if when < start else []).append(rec)
        # The same floor the subject is judged by, on both sides: a peer with one run in the
        # window, or with no history before it, is not evidence either way.
        if len(inside) < MIN_RUNS or len(before) < MIN_RUNS:
            continue
        compared.append(peer)
        verdict = regression_flag(before[-window:], inside, window=window)
        if verdict["flagged"]:
            degraded.append(peer)

    if len(compared) < HOST_MIN_PEERS:
        return _verdict(UNATTRIBUTABLE, compared, degraded, slug)
    share = len(degraded) / len(compared)
    return _verdict(HOST if share > HOST_SHARE else ROUTINE, compared, degraded, slug)


def _by_routine(fleet: list[dict], slug: str,
                name_of: Callable[[dict], str] | None) -> dict[str, list[dict]]:
    """Every OTHER routine's folded runs, keyed by the routine they belonged to."""
    out: dict[str, list[dict]] = {}
    for rec in fold_legs(fleet):
        peer = name_of(rec) if name_of else str(rec.get("routine") or "")
        if not peer or peer == slug:
            continue                           # the subject is never its own peer
        out.setdefault(peer, []).append(rec)
    return out


def _verdict(scope: str, compared: list[str], degraded: list[str], slug: str,
             why: str = "") -> dict:
    n, k = len(compared), len(degraded)
    share = round(k / n, 3) if n else 0.0
    if scope == HOST:
        sentence = (f"host-wide: {k + 1} of the {n + 1} routines that ran this window "
                    f"degraded together ({k} of {n} others, {share:.0%}) — a condition of "
                    "the host, not one routine's regression")
    elif scope == ROUTINE:
        sentence = (f"{k} of the {n} other routines that ran the same window degraded "
                    f"({share:.0%}), so this degradation is this routine's own")
    else:
        had = (why or (f"only {n} other routine{'s' if n != 1 else ''} had comparable runs "
                       f"in this window ({HOST_MIN_PEERS} needed)" if n else
                       "no other routine had comparable runs in this window"))
        sentence = (f"cannot attribute: {had} — the degradation is reported as measured, "
                    "its cause is not established")
    return {"scope": scope, "peers_compared": n, "peers_degraded": k, "share": share,
            "compared_names": sorted(compared),
            "degraded_names": sorted([*degraded, slug]), "sentence": sentence}


def event_payload(slug: str, trend: dict, att: dict) -> tuple[str, str, dict]:
    """(routine, detail, fields) for one `cost_trend_degraded` filing.

    The WORDING is the fix as much as the logic is: what F568 records is an operator reading
    an accusation. So a `host` row carries NO routine — `backup_stale`'s idiom for an instance
    fact — and leads with what it is about. That empty key is also what collapses the three
    rows into one: `health_cooldown` keys its 12-hour window on (event, routine), and every
    host row shares the empty key whichever routine's run detected it, so one condition of the
    box files one finding. `detected_on` keeps where it was read, as a field.

    A `routine` or `unattributable` row keeps the routine and the reading's original wording,
    with the fleet's own numbers appended: the degradation is reported either way — what an
    unattributable row withholds is only the claim about its cause.
    """
    reading = (f"the last {trend['window']} runs against the {trend['window']} before them: "
               + "; ".join(trend["reasons"]))
    before, after = trend["before"], trend["after"]
    host = att["scope"] == HOST
    fields: dict = {
        "scope": att["scope"], "peers_compared": att["peers_compared"],
        "peers_degraded": att["peers_degraded"], "fleet_share": att["share"],
        "detected_on": slug if host else None,
        "window": trend["window"],
        "turns_median_before": before["turns_median"],
        "turns_median_after": after["turns_median"],
        "volume_median_before": before["volume_median"],
        "volume_median_after": after["volume_median"],
        "fail_rate_before": before["fail_rate"], "fail_rate_after": after["fail_rate"]}
    detail = (f"{att['sentence']} · {reading}" if host
              else f"{reading} · {att['sentence']}")
    # Clipped where the writer clips, so nothing is silently cut off there instead.
    return ("" if host else slug), detail[:500], fields
