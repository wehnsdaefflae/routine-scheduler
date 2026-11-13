"""A test session WAITS for the host to have the memory it needs before it starts (2026-11-05).

This box has 14.7 GB of RAM, a swap that was full, and services whose peaks add up to more than
it holds. On 2026-11-05 a full browser gate started while the host had ~2 GB available: the
kernel's GLOBAL OOM killed a live routine run (grant-hunter:20261105-080003, rc=-9), two of the
gate's own ssh sessions, and an xdist worker mid-test. A gate is the one heavy load on this host
that can wait — nobody is watching it run, and a gate that starts ten minutes later costs ten
minutes, while one that starts now can cost a live run. So the controller process blocks in
`pytest_configure` until `MemAvailable` covers the selection, and gives up after
`RSCHED_TEST_MEM_WAIT_S` (default 20 min) with a non-zero exit naming both numbers: a refused
gate is retried, an OOM is not undone.

`/proc/meminfo` is the HOST's inside a container (no lxcfs here), which is exactly the number
that decides a global OOM — and the CONTAINER's own ceiling is a second, independent one: the
gate could be satisfied by a quiet host while its own cgroup sat near `memory.max` (accepted
proposal q-20261107-020004-53, 2026-11-09). So it waits for BOTH, the smaller deciding, and
says which one refused. The container figure is `memory.max` minus what the cgroup HOLDS
(`anon` + swapped pages) — never `memory.current`, which counts page cache the kernel drops at
the limit, and which read 96-100% on gates that killed nothing. Where it does not exist (not
Linux), nothing waits. The needs are the
measured HOLD of the selections, read off the gate's own `held_peak_bytes` rows rather than
estimated: the fast suite at FAST_WORKERS workers (~600 MB each, plus a ~900 MB controller),
the browser suite at BROWSER_WORKERS in its own container (a Chromium, a uvicorn and a test
each). `NEED_MB`'s comment holds the table and the dates.
`RSCHED_TEST_MEM_WAIT=0` turns the wait off for a run someone is watching.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

#: MB each selection needs free — of the host's MemAvailable AND of this cgroup's own headroom
#: — before it may start.
#:
#: MEASURED, not estimated (2026-11-10, goal b8). Both figures used to rest on the docstring's
#: "~250 MB each", which understates a fast worker about 2.4-fold. The gate's own sampler now
#: records `held_peak_bytes` — anon + swapped pages, the part dropping page cache cannot free
#: and the part an OOM is decided on — so the cost of a selection is readable instead of
#: guessed. From `.gate-evidence/memory-peaks.jsonl`, every stage carrying that field:
#:
#:     stage       n   held of the 5 GiB cgroup        seconds
#:     tests      27   0.79-1.20 GB (median 0.91)      1-93      controller alone, -n 0
#:     fast        7   3.08-3.29 GB (median 3.16)      205-264   controller + 4 workers
#:     ui          5   0.80-1.03 GB (median 0.99)      640-883   controller + 2 browsers
#:
#: So the fast stage's own increment over a bare controller is 2.25 GB median and **2.38 GB at
#: its worst** — about **600 MB per worker**, against the 250 MB the old figure assumed — and
#: the whole stage HOLDS at most 3,367 MB of the 5,120 MB cgroup, leaving 1,753 MB. `fast` is
#: therefore 3584 MB: that worst measured hold rounded up to the next 256 MB. It is deliberately
#: the WHOLE stage's hold rather than the increment, because the controller is part of what must
#: fit — and it is rounded on the MB figure, not on the GB one (3.29 GB rounds to 3328 MB, which
#: is BELOW the hold it has to cover; the new measured-hold test below caught exactly that).
#:
#: `browser` stays 3584 MB. It runs in the `uitest` container's own 4 GiB cgroup, not this one
#: (the `ui` rows above are this cgroup's idle hold while that container works), so its budget
#: is not comparable to the figures above and nothing measured here bears on it.
#:
#: WHY THE WORKER COUNT DID NOT MOVE. `peak_pct_of_limit` reads `memory.current`, which counts
#: reclaimable page cache: 51 fast stages since 2026-11-01 read a median 96.6 % of the limit —
#: 14 of them exactly 100.0 % — with `oom_kill` **0** throughout, and the live counter still at
#: 0 with 197 reclaim events. On the figure that decides an OOM the same stages sit at 61.6-65.8
#: %. Duration does not track it either: the highest hold (65.8 %) took 241 s and the highest
#: peak (100.0 %) took 205 s, while the one 1,710 s stage was the `should_wait` hang below, not
#: memory pressure. Lowering `FAST_WORKERS` would have cost every release gate wall clock
#: against a ceiling the stage was never near on the deciding figure.
#: `serial` is the THIRD entry, added 2026-11-13 (R3376 from `scheduler-builder`). Until then
#: every non-ui selection was charged the four-worker `fast` figure, whatever it selected: a
#: two-file `-n 0` call was gated on 3,584 MB, and on 2026-11-12 that refused ~12 gate calls —
#: his own release gate's FAST stage (exit 3 at 1,201 s with `real_failures: []`) and three of
#: his child runs, which handed back three built items with ZERO test evidence between them.
#: The 2.25 GB the fast figure covers above a bare controller is the four xdist WORKERS, and a
#: serial selection starts none of them, so it was being charged for processes it never spawns.
#:
#: MEASURED the same way as the others, off `held_peak_bytes` in the gate's own
#: `.gate-evidence/memory-peaks.jsonl` — 119 `tests` rows carry the field, and the 23 whose
#: stage log is still on disk can be split by the worker count pytest printed:
#:
#:     selection                       n    held of the 5 GiB cgroup      seconds
#:     -n 0, under 1000 items         21    876-2700 MB (median 1965)     1-442
#:     fast-serial (the gate's own)    5    851-1317 MB (median  934)
#:     2 workers, 6927 items           2    3979-4393 MB                  441-442
#:
#: So `serial` is 2816 MB: the worst measured serial hold (2,700) rounded up to the next 256 MB.
#: It is NOT the 1280 MB R3376 proposed from this module's older n=27 table — that table read
#: 0.79-1.20 GB, and with four times the rows the serial worst case is more than twice it, so
#: the cheaper figure would have admitted a selection it had not checked for. The two-worker
#: rows are the reason the whole-suite `-n 2` workaround is not priced here: it is not serial.
#:
#: Like every figure here this is the whole CGROUP's hold while the selection ran, a live
#: routine run included — deliberately conservative, since what must fit is everything at once.
NEED_MB = {"fast": 3584, "browser": 3584, "serial": 2816}

#: What each label is called in the message a refusal prints. "the fast suite" was the only
#: phrase before, and it was printed for a two-file `-n 0` call too — which read as a memory
#: leak in the candidate and sent R3376's author looking for one (his point 2).
SELECTION_PROSE = {
    "fast": "the fast suite at 4 xdist workers",
    "browser": "the browser suite",
    "serial": "this serial selection (no xdist workers)",
}


def selection_for(config, markexpr: str) -> str:
    """Which NEED_MB entry this session should be charged — by what it will really start.

    The browser suite is priced by its marker expression, because its cost is the Chromiums it
    drives rather than the workers pytest starts. Everything else is priced by whether xdist
    workers will exist: `-n 0` leaves `dist == "no"` and `numprocesses == 0` (measured on this
    box with pytest 9.1.1 / xdist 3.8.0), while the default `addopts` selection resolves to
    `worksteal` with 4. A worker process never reaches here at all — `should_wait` sends it
    straight through, since the controller already waited for the whole selection.
    """
    if "not ui" not in markexpr:
        return "browser"
    serial = (getattr(config.option, "dist", "no") == "no"
              or getattr(config.option, "numprocesses", None) in (0, None))
    return "serial" if serial else "fast"

MEMINFO = Path("/proc/meminfo")
CGROUP = Path("/sys/fs/cgroup")


def available_mb(meminfo: Path = MEMINFO) -> int | None:
    """The host's MemAvailable in MB, or None where it cannot be read."""
    try:
        for line in meminfo.read_text(encoding="ascii").splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


def cgroup_headroom_mb(cgroup: Path = CGROUP) -> int | None:
    """MB this cgroup can still HOLD before its `memory.max`, or None where there is no ceiling
    to read (the host itself, cgroup v1, `max`)."""
    try:
        limit = (cgroup / "memory.max").read_text(encoding="ascii").strip()
        stat = (cgroup / "memory.stat").read_text(encoding="ascii")
    except OSError:
        return None
    anon = next((int(line.split()[1]) for line in stat.splitlines()
                 if line.startswith("anon ")), None)
    if not limit.isdigit() or anon is None:
        return None
    try:
        swap = int((cgroup / "memory.swap.current").read_text(encoding="ascii"))
    except (OSError, ValueError):
        swap = 0
    return (int(limit) - anon - swap) // (1024 * 1024)


def should_wait(config) -> bool:
    """Is this the CONTROLLER of a real gate session — the one process that may block?

    Two things must be true, and the second was missing until 0.525.4. A worker is admitted
    without waiting (it carries `workerinput`; the controller already waited for the whole
    selection). And the config must be a REAL pytest one: a unit test that calls the
    `pytest_configure` hook directly to pin its distribution logic passes a stub with an
    `option` namespace and nothing else, and a stub has no `workerinput` either — so it fell
    into the controller branch and entered a twenty-minute memory wait inside a test.

    MEASURED, because it cost a whole release gate (2026-11-10, gate
    `1794268634309389266-release`): `tests/test_ui_harness.py` calls the hook three times with
    such a stub. Two of those selections ask for the BROWSER need, and on a box already running
    a 4-worker fast stage the cgroup headroom is below it — so both nodes blocked for exactly
    the 300 s pytest timeout, were marked `rerun`, restarted, and the stage never reported. The
    gate holds `/tmp/rsched-gates.lock` while that happens, so the hang is not local to one
    gate: every other routine's gate on this box waits behind it, for up to
    `RSCHED_TEST_MEM_WAIT_S` (20 min by default).
    It was invisible until then because the same three nodes pass in 0.0 s on a quiet box: the
    wait returns at once when the memory is there. A gate hang that appears only under load
    appears only when the gate matters.

    `pluginmanager` is the discriminator rather than a type check: every real `Config` has one
    from its constructor, no stub in this suite does, and it stays true for a config a future
    plugin wraps.
    """
    return not hasattr(config, "workerinput") and hasattr(config, "pluginmanager")


def wait_for_memory(selection: str, *, read=available_mb, read_cgroup=cgroup_headroom_mb,
                    sleep=time.sleep, clock=time.monotonic) -> None:
    """Block until the host can take `selection` ("fast" | "browser"), or exit the session."""
    if os.environ.get("RSCHED_TEST_MEM_WAIT", "") == "0":
        return
    need = NEED_MB[selection]
    deadline = clock() + float(os.environ.get("RSCHED_TEST_MEM_WAIT_S") or 1200)
    told = False
    while True:
        short = [(name, have) for name, have in (("host memory", read()),
                                                 ("this container's cgroup", read_cgroup()))
                 if have is not None and have < need]
        if not short:
            return
        name, have = min(short, key=lambda s: s[1])
        if clock() >= deadline:
            what = SELECTION_PROSE.get(selection, f"the {selection} suite")
            pytest.exit(f"{name}: {have} MB available, {what} needs {need} MB "
                        "— not started, so it cannot push it into an OOM that kills a live "
                        "run (tests/memgate.py). Retry when it is quieter.", returncode=3)
        if not told:
            what = SELECTION_PROSE.get(selection, f"the {selection} suite")
            sys.stderr.write(f"memgate: {name} has {have} MB, {what} needs {need} "
                             "MB — waiting for it to free memory\n")
            told = True
        sleep(15)
