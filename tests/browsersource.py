"""WHICH BROWSER the UI suite drives — launch our own (the default) or attach to the sidecar.

Operator, 2026-10-22: *"Maybe we shouldn't use the sidecar for browser tests at all anymore"*,
after the measurement below. Attaching was once mandatory: no browser was installed locally, and
the shared headful Chrome was how a person WATCHED the tests run while the console was being
built. Both halves of that have changed — Playwright's Chromium is in the image, and the watching
is done.

WHAT SHARING ONE BROWSER COST, measured 2026-10-21 on this box (12 cores) from the release gate's
own per-test ledger: **599 items, 913 s of work** (setup 120 s, call 631 s, teardown 163 s) run
effectively SERIALLY — one worker took 509 calls, a second 90 after the first crashed. The 15
slowest items sum to ~110 s, so there was no hot spot to optimise: the cost was the serialisation
itself, forced by `xdist_group` because three workers rendering into one headful Chrome contend
(38 reruns and a 1500 s timeout, which is why the group was introduced).

AND THE BROWSER WAS NEVER THE SLOW PART: the same 22 items cost **28.3 s** against a locally
launched browser and **29.8 s** attached. Four concurrent processes, each with its own browser,
did 42 s of work in **18 s** of wall clock. So the suite becomes bounded by cores instead of by
item count, and the sidecar stays one environment variable away for the day somebody wants to
watch again.

The sidecar itself is unchanged and keeps its real job: holding the operator's logins for the
`job-*` and `browser-session` utils at runtime (docs/browser-sessions.md), where being signed in
IS the point. Nothing in this suite needs those sessions — the only test that mentions CDP drives
the console's own dock against a deliberately dead address.
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlsplit

import pytest

#: The sidecar attach happens once per gate, so it can afford to be patient where a page action
#: cannot: a real wedge still surfaces inside a minute, and a transient hiccup does not discard
#: a half-hour browser suite.
SIDECAR_CONNECT_TIMEOUT_MS = 60_000

#: Hosts whose CDP port has no bearer proxy in front of it.
_LOOPBACK = ("127.0.0.1", "::1", "localhost")


#: The FAST suite's worker ceiling. `-n auto` meant cpu_count-1 = 11 workers on this 12-core
#: box, ~250 MB each, inside the daemon's own 5 GiB container cgroup (self-audit's and the
#: builder's gates run there) on a host with no memory to spare. Six workers (2026-11-05) ran
#: the suite in 110 s against 72 s at eleven, for ~1.3 GB less at peak; tests/memgate.py is the
#: other half (the session waits for the host to have the memory at all).
#:
#: FOUR, not six (2026-11-07). Six ran the cgroup to its ceiling on nearly every gate. From the
#: gate's own sampler (`.gate-evidence/memory-peaks.jsonl`, 27 covering fast stages since
#: 2026-11-01): the peak was 89-100 % of `memory.max` EVERY time, exactly 100.0 % on 14 of them,
#: and the `memory.events max` count DURING one 95-340 s stage ran 238, 306, 385, 700, 803,
#: 902, 1203, 1254, 1918, 2696, 2889, 3458 — and once 6,399. That matters because this cgroup is
#: the ENGINE container's, shared with the daemon and every live run, so a stage that sits at the
#: ceiling for three minutes is the whole box's allocation failure, not the gate's own.
#:
#: Measured on the same tree and selection:
#:
#:     -n 6   141-173 s   96-100 % of 5 GiB   287-1254 ceiling events (release gates)
#:     -n 4   213 s        78.0 %             attached `tests` run
#:     -n 4   200 s        88.5 %             the RELEASE gate that shipped this change
#:
#: Honest about what that proves: one production gate, no ceiling events recorded in it, peak
#: down from a hard 100.0 % to 88.5 % — and two SIX-worker gates also recorded none (96.2 %
#: with none, 98.9 % with 8), so zero events is not yet a property of four workers alone. The
#: peak is the sounder signal and it moved; the next gates decide whether the bound holds.
#:
#: It buys headroom no other knob was providing: `memgate` guards the HOST's MemAvailable and
#: `FAST_WORKERS` had been chosen for wall clock, so neither bounded the container the stage
#: actually runs in. Same reasoning memgate states — a gate is the one heavy load on this host
#: that can wait, and nobody is watching it run.
FAST_WORKERS = 4

#: How many browsers may run at once when each worker launches its own. A browser worker costs
#: a Chromium AND a uvicorn AND the test itself, so it is not the arithmetic the fast suite uses.
#:
#: TWO, not four (D238, operator 2026-11-05). Four peaked at ~4.8 GB anon, and the `uitest`
#: container that runs this suite is capped at 4 GiB (docker-compose.override.yml) on a 14 GB
#: host with ~2 GB free — so the kernel OOM-killed chrome-headless inside it 153 times in the
#: week to 2026-11-05 (`journalctl -k`), each kill a test parked 120 s on a browser that never
#: answered, and the browser stage ran 95-113 min or timed out: no release shipped from 11-02.
#: Raising the ceiling was declined — the host has no memory to give — so each worker gets
#: twice the headroom instead, and the stage is slower but finishes.
BROWSER_WORKERS = 2


#: The fewest workers a fast selection ever runs with. `-n auto` has always guaranteed two
#: (`max(2, …)` in conftest), a one-worker xdist run is a serial run paying xdist's overhead,
#: and a box with no room for even two is what the memory WAIT is for — it waits, which is the
#: behaviour a refusal was only pretending to have.
MIN_FAST_WORKERS = 2


def fast_workers(cores: int, *, headroom=None) -> int:
    """How many fast workers fit the cgroup headroom there IS right now — D291.

    The operator, asked which side gives when a release gate's FAST stage (3,584 MB of this
    container's 5,120 MB cgroup) and a live routine run (1,086-1,717 MB) cannot both fit:
    *"Reduce FAST_WORKERS while a routine run is active, trading gate wall-clock for
    coexistence"*. He did NOT choose raising the cgroup, so the conditional count is the work.

    Before this, both the count and the charge were constants, so a live run holding the
    difference meant the gate was REFUSED rather than run smaller — and since the two fit
    SOMETIMES, the refusal read as a flaky failure instead of as a rule.

    Bounded by cores as it always was, by `FAST_WORKERS` above, and by `MIN_FAST_WORKERS` below.
    An IDLE box is untouched by construction: its headroom exceeds the four-worker charge, so
    no release gate pays wall clock for a coexistence nobody needs. Where there is no cgroup
    ceiling to read (not Linux, cgroup v1, `memory.max` reading `max`) the headroom is None and
    the ceiling applies, exactly as this module behaved before.

    The headroom reader is `memgate.cgroup_headroom_mb` — `memory.max` minus what the cgroup
    HOLDS (anon + swapped pages), never `memory.current`, which counts page cache the kernel
    drops at the limit and read 96-100 % on gates that killed nothing. Injected rather than
    imported at module scope because `memgate` imports this module.
    """
    import memgate

    ceiling = max(MIN_FAST_WORKERS, min(FAST_WORKERS, max(1, cores)))
    have = (headroom or memgate.cgroup_headroom_mb)()
    if have is None:
        return ceiling
    for count in range(ceiling, MIN_FAST_WORKERS, -1):
        if memgate.fast_need_mb(count) <= have:
            return count
    return min(ceiling, MIN_FAST_WORKERS)


def auto_workers(cores: int, markexpr: str) -> int:
    """What `-n auto` means for THIS suite: one worker fewer than the machine has cores, capped
    for memory by the headroom there IS, and fewer still for the browser selection.

    Moved here from `tests/conftest.py` (D291): that file is over the house size bar and may
    only shrink, and this is the module that owns both worker counts — so the whole decision
    lives in one place instead of half a decision in a hook.

    xdist's own `auto` is `os.cpu_count()`, which assumes one busy process per worker. A worker
    here serves a uvicorn in-process on top of the test itself, so `-n 4` on a 4-core box runs
    more runnable processes than the box has and it thrashes instead of working. Measured on the
    4-core deployment (2026-08-30, whole suite):

        -n 4 (auto)   574 s   149 % cpu     ← 4 cores available, under 1.5 used
        -n 3          445 s   179 % cpu     ← 22 % faster AND better utilised

    Fewer workers is faster: the loss is contention, not idleness.
    """
    return worker_cap(markexpr, fast_workers(cores - 1))


def cdp_endpoint() -> str:
    """The browser to ATTACH to, or "" meaning "launch your own"."""
    return os.environ.get("RSCHED_TEST_CDP", "").strip()


def worker_cap(markexpr: str, reserved: int) -> int:
    """`-n auto` for this selection: `reserved` normally, `BROWSER_WORKERS` when the browser
    directory is in it (the default `-m "not ui"` excludes it).

    Measured 2026-10-22, the first gate after the sidecar stopped being the default: at `auto`
    on a 12-core box the suite spread to FOURTEEN workers, each launching a browser, and one
    died — xdist aborted the whole stage with `INTERNALERROR KeyError: <WorkerController gw12>`
    after 97 s, with 13 nodes left unresolved. That is not a verdict about the candidate, it is
    an oversubscribed box, and the gate correctly called it `timeout` rather than red.

    Four was the shape first measured (before the 4 GiB `uitest` ceiling, see BROWSER_WORKERS): four concurrent processes, each with its own browser,
    did 42 s of work in 18 s of wall clock. It is also consistent with the old contention
    evidence from the shared-browser era (three workers into ONE Chrome produced dozens of
    reruns) — the browsers being separate is what makes four survivable where three were not.
    """
    if "not ui" in (markexpr or "").replace("  ", " "):
        return reserved
    return min(BROWSER_WORKERS, reserved)


def installed_chromium() -> str:
    """The newest Chromium Playwright has downloaded, or "".

    The fallback when the BUNDLED revision is absent: measured here 2026-10-21, Playwright
    1.64 pins `chromium-1248` while the image carried 1228 and 1243, and the launch died with a
    SIGTRAP that named no cause. Picking the newest installed build turns that into a working
    browser instead of a mystery crash.
    """
    root = Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
                or Path.home() / ".cache" / "ms-playwright")
    builds = sorted(root.glob("chromium*-[0-9]*"), reverse=True) if root.is_dir() else []
    for build in builds:
        for exe in ("chrome-linux64/chrome", "chrome-linux/chrome",
                    "chrome-headless-shell-linux64/chrome-headless-shell"):
            if (candidate := build / exe).exists():
                return str(candidate)
    return ""


def cdp_headers(endpoint: str) -> dict[str, str]:
    """The bearer the SIDECAR's auth proxy requires (deploy/browser-auth-proxy.py).

    CDP authenticates nothing of its own, so that port sits behind a token door and this suite
    is one of its callers. Taken from the environment rather than the secrets store: a test run
    is not a routine and has no grant to read from.

    A LOOPBACK endpoint has no such door (F743). Demanding the token for every endpoint made a
    locally launched browser unreachable — and because that refusal is deterministic, identical
    on every attempt, while NOT being in the directory's `rerun_except`, the rerun shield spent
    four attempts per test on it: 22 tests became 88 reruns and 183 s before one test ran a line.
    """
    if (urlsplit(endpoint).hostname or "") in _LOOPBACK:
        return {}
    token = os.environ.get("BROWSER_CDP_TOKEN", "").strip()
    if not token:
        pytest.fail("BROWSER_CDP_TOKEN is required to reach the browser sidecar — its CDP "
                    "port is behind a bearer proxy. Export the same value the compose "
                    "`chrome` service gets (see deploy/DOCKER.md). This refusal is "
                    "configuration, not a flake: it is the same on every attempt.")
    return {"Authorization": f"Bearer {token}"}


def acquire(playwright, how_to_run: str):
    """The suite's browser: launched when no endpoint is named, attached when one is."""
    from playwright.sync_api import Error as PlaywrightError

    endpoint = cdp_endpoint()
    if not endpoint:
        try:
            return playwright.chromium.launch(headless=True)
        except PlaywrightError:
            exe = installed_chromium()
            if not exe:
                pytest.fail("no local browser: `uv run playwright install chromium` — or set "
                            f"RSCHED_TEST_CDP to attach to one instead.\n{how_to_run}")
            return playwright.chromium.launch(headless=True, executable_path=exe)
    try:
        # Pass `headers` only when there ARE some: Playwright serialises the mapping into its
        # wire format and an EMPTY dict fails its own validation with "headers: expected array,
        # got object" — which this function would then report as a wedged sidecar, i.e. a
        # perfectly healthy browser described as broken (measured here 2026-10-22).
        extra = {"headers": headers} if (headers := cdp_headers(endpoint)) else {}
        return playwright.chromium.connect_over_cdp(
            endpoint, timeout=SIDECAR_CONNECT_TIMEOUT_MS, **extra)
    except PlaywrightError as exc:
        pytest.fail(f"browser sidecar {endpoint} is wedged — it accepts TCP but never attaches "
                    f"over CDP. Restart the compose `chrome` service "
                    f"(docker compose restart chrome). {type(exc).__name__}: {exc}")
