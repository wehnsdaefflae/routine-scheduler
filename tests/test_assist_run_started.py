"""`_run_started()` — the lower bound for "did this happen during this run".

Pinned as OFFSET ARITHMETIC, never against a named zone. On a host whose /usr/share/zoneinfo
files are not the ones their names promise (and without the `tzdata` package, which is the
state of this build host: every zone file there is byte-identical to Europe/Berlin, so
ZoneInfo("UTC").utcoffset() is +02:00), a test that asserts a named zone's behaviour passes
for the wrong reason and proves nothing.

So the facts below are arithmetic: a run-ts is ALWAYS UTC (`ids.run_ts`), therefore a stamp N
seconds in the past reads as N seconds ago, and a stamp of a known instant reads as that
instant's epoch second. Read as the HOST's local wall-clock time instead, each one fails by
exactly the host's UTC offset — which is the defect these pin (F727): `_run_started()` is the
bound for the missing-reasoning assist's `ledger.st_mtime >= _run_started(ctx)` test, so an
offset host counted a ledger written hours BEFORE the run as written during it, and the
assist silently never fired.
"""

import datetime as dt
from types import SimpleNamespace

from rsched.engine.assist_predicates import _run_started
from rsched.ids import run_ts
from rsched.registry import parse_run_ts


def _ctx(stamp: str):
    """The only field `_run_started` looks at."""
    return SimpleNamespace(run_ts=stamp)


def test_a_stamp_n_seconds_old_reads_as_n_seconds_ago():
    age = 600
    now = dt.datetime.now(dt.UTC)
    stamp = run_ts(now - dt.timedelta(seconds=age))

    elapsed = now.timestamp() - _run_started(_ctx(stamp))

    # The stamp truncates to the second, so the true answer is age + [0, 1).  A local reading
    # is off by the host's whole UTC offset (3600 s per hour), which no tolerance absorbs.
    assert abs(elapsed - age) <= 2, (
        f"a run-ts {age} s old read as {elapsed:.0f} s ago — off by {elapsed - age:.0f} s, "
        f"the host's UTC offset: the stamp was read as local time, not UTC")


def test_a_known_utc_instant_reads_as_its_own_epoch_second():
    # 2026-01-01T00:00:00Z — arithmetic, no zone database consulted on either side.
    assert _run_started(_ctx("20260101-000000")) == 1767225600.0


def test_it_agrees_with_the_other_run_ts_readers():
    # registry.parse_run_ts states the same contract (as do runevidence and gate_prepare);
    # a divergence between two readers of one stamp IS the defect.
    stamp = run_ts(dt.datetime(2026, 7, 1, 12, 0, tzinfo=dt.UTC))
    other = parse_run_ts(stamp)
    assert other is not None
    assert _run_started(_ctx(stamp)) == other.timestamp()
