"""WHICH BROWSER the UI suite drives — launch our own (the default) or attach to the sidecar."""

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
#: FOUR (operator, re-measured): each browser worker adds ~300 MB to the `uitest` container,
#: and the whole suite at four peaks well inside that container's 4 GiB ceiling
#: (docker-compose.override.yml), so the stage runs ~1.4-1.6x faster than at two for no
#: coverage. The ceiling is the bound that matters: four OOM-killed Chromium there once, when
#: the harness held far more per worker (D238 cut it to two then). `memgate.NEED_MB["browser"]`
#: is the admission; raise this only with a full-suite peak measured inside that cgroup.
BROWSER_WORKERS = 4


#: The fewest workers a fast selection ever runs with. `-n auto` has always guaranteed two
#: (`max(2, …)` in conftest), a one-worker xdist run is a serial run paying xdist's overhead,
#: and a box with no room for even two is what the memory WAIT is for — it waits, which is the
#: behaviour a refusal was only pretending to have.
MIN_FAST_WORKERS = 2


def fast_workers(cores: int, *, headroom=None) -> int:
    """How many fast workers fit the headroom there IS right now — D291."""
    import memgate

    ceiling = max(MIN_FAST_WORKERS, min(FAST_WORKERS, max(1, cores)))
    have = (headroom or memgate.headroom_mb)()
    if have is None:
        return ceiling
    for count in range(ceiling, MIN_FAST_WORKERS, -1):
        if memgate.fast_need_mb(count) <= have:
            return count
    return min(ceiling, MIN_FAST_WORKERS)


def auto_workers(cores: int, markexpr: str) -> int:
    """What `-n auto` means for THIS suite: one worker fewer than the machine has cores — for
    the fast selection also capped for memory by the headroom there IS; for the browser
    selection `BROWSER_WORKERS`, which the fast suite's per-worker price does not describe
    (its memory is admitted in `uitest`'s own cgroup, by `memgate.NEED_MB["browser"]`).
    """
    if is_browser_selection(markexpr):
        return max(1, min(BROWSER_WORKERS, cores - 1))
    return fast_workers(cores - 1)


def cdp_endpoint() -> str:
    """The browser to ATTACH to, or "" meaning "launch your own"."""
    return os.environ.get("RSCHED_TEST_CDP", "").strip()


def is_browser_selection(markexpr: str) -> bool:
    """Whether the browser directory is in this selection (the default `-m "not ui"` excludes it)."""
    return "not ui" not in (markexpr or "").replace("  ", " ")


def installed_chromium() -> str:
    """The newest Chromium Playwright has downloaded, or ""."""
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
    """The bearer the SIDECAR's auth proxy requires (deploy/browser-auth-proxy.py)."""
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
