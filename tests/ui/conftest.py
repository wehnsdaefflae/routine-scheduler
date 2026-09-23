"""Playwright UI harness — the REAL console (FastAPI + static frontend) served by uvicorn
on an ephemeral port, backed by fixture homes and a stub runner: no scheduler, no engine
subprocess, no LLM. Tests drive the browser against the same JS the daemon serves.

The browser signs in by pre-seeding localStorage with the fixture token (api.js reads
`rsched_token`); the `.setup-complete` marker next to the fixture config suppresses the
first-launch redirect to Settings. JS runtime errors fail the test via the `ui` fixture's
collector — a page that renders but throws is a broken page.
"""

from __future__ import annotations

import os
import shutil
import socket
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import pytest
import uvicorn
import yaml
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import expect

from rsched.bootstrap import seed_libraries
from rsched.config import load_server_config
from rsched.paths import atomic_write_json
from rsched.web.app import create_app

TOKEN = "ui-test-token"
ROUTINE_TOKEN = "ui-test-routine-token"      # the read-only tier, for the token-gate tier test

_UI_DIR = Path(__file__).parent

# The compose `browser` network (docker-compose.yml) pins both addresses — DevTools rejects a
# Host header that is not an IP literal, and a fixture console the sidecar must reach cannot
# bind a loopback the sidecar does not share. They are named in the refusals below rather than
# defaulted, because they exist ONLY inside that network: a default would turn one legible
# one-line refusal on any other box into a CDP connect timeout followed by a bind error on an
# address the machine does not have.
SIDECAR_CDP = "http://172.30.7.10:9222"
SIDECAR_BIND = "172.30.7.2"
HOW_TO_RUN = (f"Run the browser suite inside the engine container, where the compose `browser` "
              f"network exists:\n"
              f"    docker compose exec -u 1000:1000 rsched \\\n"
              f"      env RSCHED_TEST_CDP={SIDECAR_CDP} RSCHED_TEST_BIND={SIDECAR_BIND} \\\n"
              f"      uv run pytest -q -m ui\n"
              f"No browser is installed locally: the suite attaches over CDP to the `chrome` "
              f"sidecar and serves its fixture console back at the engine's own address on that "
              f"network.")

# One xdist work unit for the whole directory. Three workers rendering into ONE headful Chrome
# is what the rerun shield below was absorbing: measured over the release gate's own ledger,
# ~530 serial browser-test executions produced 3 reruns while every 3-worker invocation produced
# dozens, and the full 3-worker browser gate timed out at 1500 s with 38 reruns. `loadgroup`
# (switched on in tests/conftest.py whenever browser tests are selected) sends every item
# carrying this group to the same worker, so the browser suite runs serially while the fast
# suite keeps spreading across the rest.
BROWSER_GROUP = "browser"


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(items):
    """Mark every item under this directory: `ui`, `xdist_group`, and the rerun shield.

    Applied here rather than written on each test because a marker anyone can forget is a
    marker that stops meaning anything: every test in this directory needs a browser, and
    that is a fact of the directory rather than of the test.

    `ui` is what the default `-m "not ui"` deselects, so the fast gate stays fast.

    `xdist_group` puts the whole directory in ONE work unit, which `--dist loadgroup`
    (tests/conftest.py switches it on whenever browser tests are selected) sends to a single
    worker. Since the suite moved onto the shared Chrome sidecar, parallel workers do not
    each get a browser — they share one, and contending for it is what the rerun shield had
    been absorbing.

    `flaky` reruns ONLY on failure, so an intermittent blip passes on retry while a real
    regression still fails all attempts (F120; reruns=4/2s since F261, where a 2-rerun shield
    was pierced twice in one night under cron load). `rerun_except` exempts a wedged sidecar:
    that failure is the same on every attempt, and spending four reruns per test on it turns
    a one-line diagnosis into a gate-length one.

    tryfirst, because xdist's own worker-side hook reads `xdist_group` to build the nodeid
    suffix the scheduler groups on. For an explicit path argument this conftest is an INITIAL
    conftest and therefore registered BEFORE that hook, which pluggy would then run first —
    the marker would not exist yet and the grouping would silently degrade to one unit per
    test across every worker.
    """
    for item in items:
        item_path = getattr(item, "path", None)
        in_ui = bool(item_path) and (item_path == _UI_DIR or _UI_DIR in item_path.parents)
        if not in_ui:  # fall back to fspath for any item lacking a pathlib .path
            in_ui = str(_UI_DIR) in str(getattr(item, "fspath", ""))
        if in_ui:
            item.add_marker(pytest.mark.ui)
            item.add_marker(pytest.mark.xdist_group(BROWSER_GROUP))
            item.add_marker(pytest.mark.flaky(reruns=4, reruns_delay=2,
                                              rerun_except=r"sidecar .* is wedged"))


def _cdp_endpoint() -> str:
    endpoint = os.environ.get("RSCHED_TEST_CDP")
    if not endpoint:
        pytest.fail(f"RSCHED_TEST_CDP is required; a local browser launch is forbidden.\n"
                    f"{HOW_TO_RUN}")
    return endpoint


# The sidecar attach happens ONCE per gate (the whole directory is one work unit), so it can
# afford to be patient where a page action cannot: a real wedge still surfaces inside a minute,
# and a transient hiccup does not discard a half-hour browser suite.
SIDECAR_CONNECT_TIMEOUT_MS = 60_000


@pytest.fixture(scope="session")
def browser(playwright):
    """Attach to the shared Chrome sidecar; never launch or close a browser.

    A wedged sidecar answers /json/version normally while CDP never attaches, so nothing
    outside this connect can see it — name it here, with the command that clears it.

    The `rerun_except` on the directory's rerun shield is what keeps a down sidecar cheap, and
    the mechanism is worth stating: pytest CACHES a session-fixture failure, so the tests after
    the first get the cached error instantly — unless a rerun is scheduled, which clears that
    cache and re-executes the fixture. One recorded partition paid 30.6 + 15.0 + 27.7 + 20.8 +
    15.1 s for a single test that never ran a line, and then again for every other test in the
    partition. Exempting this failure from reruns turns 276 connects back into one, which is
    also why it can afford to be patient.
    """
    endpoint = _cdp_endpoint()
    try:
        return playwright.chromium.connect_over_cdp(
            endpoint, timeout=SIDECAR_CONNECT_TIMEOUT_MS)
    except PlaywrightError as exc:
        pytest.fail(f"browser sidecar {endpoint} is wedged — it accepts TCP but never attaches "
                    f"over CDP. Restart the compose `chrome` service "
                    f"(docker compose restart chrome). {type(exc).__name__}: {exc}")


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args):
    # Trust the ephemeral certificate only in disposable test contexts.
    return {**browser_context_args, "ignore_https_errors": True}


def _test_bind_host() -> str:
    host = os.environ.get("RSCHED_TEST_BIND")
    if not host:
        pytest.fail(f"RSCHED_TEST_BIND must name the interface the sidecar can reach back on.\n"
                    f"{HOW_TO_RUN}")
    return host


class StubRunner:
    """Records fire/resume calls and answers like an idle daemon — no process is ever
    spawned. Only the surface the web layer touches is implemented.
    """

    def __init__(self):
        self.fired: list[tuple[str, str]] = []
        self.active: dict[str, object] = {}
        self.draining = False

    async def fire(self, cfg, reason: str = "") -> str:
        self.fired.append((cfg.slug, reason))
        return f"{cfg.slug}:20260715-120000"

    async def resume_terminal(self, cfg, ts: str | None = None, *, reason: str = "") -> str:
        # Signature mirrors the real Runner.resume_terminal(cfg, ts=None, *, reason=...) — the
        # run page's converse path passes the run ts positionally (api_run_control.converse).
        return f"{cfg.slug}:20260715-120001"   # the wake itself is enough - nothing asserts on it

    def is_active(self, slug: str) -> bool:
        return False


@dataclass
class UiHarness:
    """One live console: base URL, the fixture homes, the stub runner, and the JS-error
    collector every test asserts empty (directly or via `ui_page` teardown).
    """

    url: str
    tmp: Path
    routines: Path
    conversations: Path
    docs: Path
    runner: StubRunner
    server_cfg: object
    boot_s: float = 0.0          # how long this test's uvicorn took to accept — see the report hook
    js_errors: list[str] = field(default_factory=list)

    def routine_dir(self, slug: str) -> Path:
        return self.routines / slug

    def seed_question(self, slug: str, qid: str, question: str, *, mode: str = "deferred",
                      options: list[str] | None = None, default: str = "",
                      expires: str = "", asked: str = "20260714-070000",
                      request: list[str] | None = None,
                      extra: dict | None = None) -> Path:
        """Drop a durable decision record the way the engine files one.

        `extra` carries the optional keys the engine writes alongside the question —
        config_patch / config_target / config_home for the Decisions page's apply bridge.
        """
        pending = self.routines / slug / "questions" / "pending"
        pending.mkdir(parents=True, exist_ok=True)
        record = {"qid": qid, "question": question, "mode": mode,
                  "type": "request" if request else "text",
                  "options": options or [], "default": default, "asked": asked}
        if request:
            record["request"] = list(request)
        if expires:
            record["expires"] = expires
        record.update(extra or {})
        path = pending / f"{qid}.json"
        atomic_write_json(path, record)
        return path

    def seed_run(self, slug: str, ts: str, state: str, *, summary: str = "",
                 home: Path | None = None, question: dict | None = None,
                 usage: dict | None = None, phase: str = "", elapsed_s: int = 60) -> Path:
        run_dir = (home or self.routines) / slug / "runs" / ts
        run_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_json(run_dir / "status.json", {
            "run_id": f"{slug}:{ts}", "state": state, "pid": 4242, "turn": 2,
            "usage": usage or {"in": 10, "out": 4}, "elapsed_s": elapsed_s, "phase": phase,
            "question": question, "started": ts, "updated": "2026-07-15T12:00:00+00:00"})
        if summary:
            (run_dir / "result.md").write_text(summary, encoding="utf-8")
        (run_dir / "transcript.jsonl").write_text(
            f'{{"type": "header", "run_id": "{slug}:{ts}"}}\n', encoding="utf-8")
        return run_dir


def _listening_socket() -> socket.socket:
    """A bound ephemeral-port socket handed straight to uvicorn (run(sockets=[...])) -
    no close-then-rebind race like the old free-port probe had under xdist."""
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((_test_bind_host(), 0))
    return s


@pytest.fixture(scope="session")
def library_template(tmp_path_factory) -> Path:
    """The seeded library, built ONCE per xdist worker and copied per test. seed_libraries
    git-inits and commits the repo, and paying those subprocess spawns per UI test fed the
    4-core contention the flaky shield (F261) exists to absorb; a tree copy carries the
    same files AND the same .git, so per-test library commits keep working.
    """
    template = tmp_path_factory.mktemp("library-template") / "library"
    seed_libraries(template)
    return template


@pytest.fixture(scope="session")
def test_tls(tmp_path_factory):
    """Ephemeral TLS for secure-context APIs on the remote fixture origin."""
    from datetime import UTC, datetime, timedelta
    from ipaddress import ip_address

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    directory = tmp_path_factory.mktemp("ui-tls")
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test fixture")])
    now = datetime.now(UTC)
    certificate = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
                   .public_key(key.public_key()).serial_number(x509.random_serial_number())
                   .not_valid_before(now - timedelta(minutes=1))
                   .not_valid_after(now + timedelta(days=1))
                   .add_extension(x509.SubjectAlternativeName([
                       x509.IPAddress(ip_address(_test_bind_host()))]), critical=False)
                   .sign(key, hashes.SHA256()))
    cert_path, key_path = directory / "cert.pem", directory / "key.pem"
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                         serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return cert_path, key_path


@pytest.fixture
def ui(tmp_path, monkeypatch, make_routine, library_template, test_tls) -> UiHarness:
    """A live console over fixture state: one routine ('uir'), the seed library
    (so conversations can materialize `converse`), a stub runner, uvicorn on an
    ephemeral port. Tears the server down after the test.
    """
    make_routine(slug="uir")
    # The generated-docs tree the Help tab reads. It is the one home that does NOT follow the
    # config file, so without this the suite reads ~/.cache and a Help assertion depends on
    # whether the machine happens to have run a build. `ui.docs` seeds it where a test wants
    # the built state; empty is what a fresh instance shows.
    docs = tmp_path / "docs-out"
    docs.mkdir()
    monkeypatch.setenv("RSCHED_DOCS_DIR", str(docs))
    library = tmp_path / "library"
    shutil.copytree(library_template, library)
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.safe_dump({
        "token": TOKEN,
        "routine_token": ROUTINE_TOKEN,
        "routines_home": str(tmp_path / "routines"),
        "conversations_home": str(tmp_path / "conversations"),
        "background_home": str(tmp_path / "background"),
        "libraries_home": str(library),
        "endpoints": {"dummy": {"kind": "openai", "base_url": "http://127.0.0.1:1/v1"}},
        "models": {"m": {"endpoint": "dummy", "model": "m"}},
        "system_model": "m",
    }), encoding="utf-8")
    (tmp_path / ".setup-complete").write_text("done\n", encoding="utf-8")
    server_cfg, problems = load_server_config(cfg_path)
    assert not problems
    app = create_app(server_cfg, with_scheduler=False)
    runner = StubRunner()
    app.state.runner = runner

    sock = _listening_socket()
    port = sock.getsockname()[1]
    cert_path, key_path = test_tls
    uv_server = uvicorn.Server(uvicorn.Config(app, host=_test_bind_host(), port=port,
                                              ssl_certfile=str(cert_path),
                                              ssl_keyfile=str(key_path), log_level="warning"))
    thread = threading.Thread(target=lambda: uv_server.run(sockets=[sock]), daemon=True)
    thread.start()
    started_at = time.monotonic()
    deadline = started_at + 15
    while not uv_server.started:
        if time.monotonic() > deadline:
            pytest.fail("uvicorn did not start within 15s")
        time.sleep(0.05)

    yield UiHarness(url=f"https://{_test_bind_host()}:{port}", tmp=tmp_path,
                    routines=tmp_path / "routines",
                    conversations=tmp_path / "conversations",
                    docs=docs, runner=runner, server_cfg=server_cfg,
                    boot_s=time.monotonic() - started_at)

    uv_server.should_exit = True
    thread.join(timeout=10)


# ONE waiting ceiling for this suite — page actions, navigations and `expect()` alike.
#
# Playwright's defaults are 30 s for an action and 5 s for an `expect()`, and that split is
# backwards for a console: nothing here takes 30 s (a page renders in under two, the slowest
# whole test in a clean run is under 17), while the assertions that wait on a poll or a PATCH
# are exactly the ones that need room. A test that raised its own `expect()` to 10 s passed
# beside a neighbouring un-raised one that pierced at 5 — under the same load, on the same
# page — and a reader could not tell a regression from an author who forgot the override.
#
# 15 s is ~7x the normal render, so a contended-but-fine page has ample room while a genuinely
# broken locator is still reported in half of Playwright's default — reruns included. Raise it
# for a gate running beside live load with RSCHED_UI_TIMEOUT_MS; do not raise it per call.
ACTION_TIMEOUT_MS = int(os.environ.get("RSCHED_UI_TIMEOUT_MS") or 15_000)


@pytest.fixture(scope="session", autouse=True)
def _expect_timeout():
    """`expect()` reads a process-global default, so the ceiling is set once for the suite."""
    expect.set_options(timeout=ACTION_TIMEOUT_MS)


def until(cond: Callable[[], object], *, what: str = "condition", page: object = None,
          timeout_s: float = 10, every: float = 0.05) -> None:
    """Poll `cond` until it is truthy, or fail naming what never happened.

    The one waiter for anything this suite observes OUTSIDE the DOM — a routine.yaml the
    console just PATCHed, a question file an answer just filed. `expect()` covers the DOM;
    nothing covers the disk, and a fixed sleep before a disk assert is precisely what flakes
    under load: a PATCH that lands in 1.2 s reds a test that slept 800 ms and then read it.

    Pass `page` when the condition can only be advanced by PLAYWRIGHT — a `page.route`
    handler's bookkeeping, say. The sync API dispatches its events on a greenlet loop that
    runs only inside a Playwright call, so a plain `time.sleep` would spin while nothing
    happened; `wait_for_timeout` pumps that loop. A condition the SERVER advances needs no
    page: the fixture's uvicorn is a real thread and moves on its own.
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if cond():
            return
        if page is None:
            time.sleep(every)
        else:
            page.wait_for_timeout(every * 1000)
    pytest.fail(f"{what} did not land within {timeout_s}s")


@dataclass
class _Probe:
    """What a failing test's page can still be asked, collected while it is alive."""

    page: object
    harness: UiHarness
    inflight: dict = field(default_factory=dict)   # Request -> monotonic start


# A list rather than a single slot: a test may take a second page (a second context).
_LIVE_PROBES: list[_Probe] = []


@pytest.fixture
def ui_page(ui, page):
    """A signed-in page: token pre-seeded, JS errors collected. Asserts NO uncaught JS
    error happened during the test — a page that throws is broken even if it renders.
    """
    page.set_default_timeout(ACTION_TIMEOUT_MS)
    page.set_default_navigation_timeout(ACTION_TIMEOUT_MS)
    page.add_init_script(f"localStorage.setItem('rsched_token', {TOKEN!r})")
    page.on("pageerror", lambda exc: ui.js_errors.append(str(exc)))

    probe = _Probe(page=page, harness=ui)
    page.on("request", lambda r: probe.inflight.__setitem__(r, time.monotonic()))
    page.on("requestfinished", lambda r: probe.inflight.pop(r, None))
    page.on("requestfailed", lambda r: probe.inflight.pop(r, None))
    _LIVE_PROBES.append(probe)

    yield page

    _LIVE_PROBES.remove(probe)
    assert ui.js_errors == [], f"uncaught JS errors: {ui.js_errors}"


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item, call):
    """Attach what the browser was doing to any browser-test failure.

    Without it a red reads `Locator expected to be visible … resolved to visible <h2>` and
    nothing else, and telling a real regression from a load flake costs a hand-run gate cycle
    (measured: ~6 per incident). Three facts answer it and all three are gone the moment the
    context closes, so they are collected here rather than by whichever runner happens to be
    used: the URL with the rendered text, the REQUESTS STILL IN FLIGHT with their age, and how
    long this test's own uvicorn took to accept. The last two are aimed at the largest recorded
    failure class by far — 70 of 232 rows are a `Page.goto` timeout, which a body dump alone
    cannot tell apart from a slow fixture boot.
    """
    report = yield
    if report.when == "call" and report.failed and _LIVE_PROBES:
        probe = _LIVE_PROBES[-1]
        lines = [f"fixture uvicorn accepted after {probe.harness.boot_s:.2f}s"]
        now = time.monotonic()
        waiting = sorted(((now - at, req.url) for req, at in probe.inflight.items()),
                         reverse=True)[:8]
        for age, url in waiting:
            lines.append(f"  in flight {age:6.2f}s  {url}")
        try:
            body = (probe.page.inner_text("body", timeout=2_000) or "").strip()
            lines.append(f"url: {probe.page.url}")
            lines.append(f"body[:800]:\n{body[:800]}")
        except PlaywrightError as exc:   # a closed, crashed or stuck page has nothing to tell
            lines.append(f"page unreadable: {type(exc).__name__}: {exc}")
        report.sections.append(("browser at failure", "\n".join(lines)))
    return report
