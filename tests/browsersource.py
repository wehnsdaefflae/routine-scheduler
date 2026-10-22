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


#: How many browsers may run at once when each worker launches its own. A browser worker costs
#: a Chromium AND a uvicorn AND the test itself, so it is not the arithmetic the fast suite uses.
BROWSER_WORKERS = 4


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

    Four is the shape that WAS measured: four concurrent processes, each with its own browser,
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
