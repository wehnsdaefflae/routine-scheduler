"""Playwright UI harness — the REAL console (FastAPI + static frontend) served by uvicorn
on an ephemeral port, backed by fixture homes and a stub runner: no scheduler, no engine
subprocess, no LLM. Tests drive the browser against the same JS the daemon serves.

The browser signs in by pre-seeding localStorage with the fixture token (api.js reads
`rsched_token`); the `.setup-complete` marker next to the fixture config suppresses the
first-launch redirect to Settings. JS runtime errors fail the test via the `ui` fixture's
collector — a page that renders but throws is a broken page.
"""

from __future__ import annotations

import shutil
import socket
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import pytest
import uvicorn
import yaml

from rsched.bootstrap import seed_libraries
from rsched.config import load_server_config
from rsched.paths import atomic_write_json
from rsched.web.app import create_app

TOKEN = "ui-test-token"
ROUTINE_TOKEN = "ui-test-routine-token"      # the read-only tier, for the token-gate tier test

_UI_DIR = Path(__file__).parent


def pytest_collection_modifyitems(items):
    """Auto-retry the browser UI tests. They are flaky under pytest-xdist — browser/timing/
    shared-resource contention between parallel workers occasionally reds a genuinely-passing
    test on a full-suite run (F120). `flaky` reruns ONLY on failure: an intermittent
    contention blip passes on retry, while a real regression still fails all attempts. Scoped to
    this directory so the rest of the suite keeps failing fast (pytest-rerunfailures provides
    the marker; it is a project dev dependency). reruns=4 with a 2s backoff, up from 2/1s
    (F261, 2026-08-06): under ~02:00 cron machine load the 2-rerun shield was pierced twice
    in one night — every pierced test then passed in isolation — and each pierce costs a
    6-9 minute re-gate plus a hand arbitration, far more than a few extra seconds-long reruns."""
    for item in items:
        item_path = getattr(item, "path", None)
        in_ui = bool(item_path) and (item_path == _UI_DIR or _UI_DIR in item_path.parents)
        if not in_ui:  # fall back to fspath for any item lacking a pathlib .path
            in_ui = str(_UI_DIR) in str(getattr(item, "fspath", ""))
        if in_ui:
            item.add_marker(pytest.mark.flaky(reruns=4, reruns_delay=2))
            # …and `ui`, which the default `-m "not ui"` deselects. Applied here rather than
            # written on each test because a marker anyone can forget is a marker that stops
            # meaning anything: every test under this directory needs a browser, and that is
            # a fact of the directory rather than of the test.
            item.add_marker(pytest.mark.ui)


@pytest.fixture(scope="session")
def browser(playwright):
    """Connect only to the sidecar; never launch or close the shared browser."""
    import os

    endpoint = os.environ.get("RSCHED_TEST_CDP")
    if not endpoint:
        pytest.fail("RSCHED_TEST_CDP is required; local browser launch is forbidden")
    return playwright.chromium.connect_over_cdp(endpoint, timeout=15_000)


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args):
    # Trust the ephemeral certificate only in disposable test contexts.
    return {**browser_context_args, "ignore_https_errors": True}


def _test_bind_host() -> str:
    import os

    host = os.environ.get("RSCHED_TEST_BIND")
    if not host:
        pytest.fail("RSCHED_TEST_BIND must name the sidecar-reachable test interface")
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
    runner: StubRunner
    server_cfg: object
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
def ui(tmp_path, make_routine, library_template, test_tls) -> UiHarness:
    """A live console over fixture state: one routine ('uir'), the seed library
    (so conversations can materialize `converse`), a stub runner, uvicorn on an
    ephemeral port. Tears the server down after the test.
    """
    make_routine(slug="uir")
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
    deadline = time.monotonic() + 15
    while not uv_server.started:
        if time.monotonic() > deadline:
            pytest.fail("uvicorn did not start within 15s")
        time.sleep(0.05)

    yield UiHarness(url=f"https://{_test_bind_host()}:{port}", tmp=tmp_path,
                    routines=tmp_path / "routines",
                    conversations=tmp_path / "conversations",
                    runner=runner, server_cfg=server_cfg)

    uv_server.should_exit = True
    thread.join(timeout=10)


# Playwright's default ACTION timeout is 30 s. Nothing this console does takes 30 s — a page
# renders in under two; the slowest whole test in a clean parallel run is under 17. The 30 s
# ceiling therefore never rescued a passing test; it only set the price of a failing one —
# the flaky shield multiplies that price by five. A real regression cost 5 x 30 s before it was
# reported (baseline: one flake burned 31 s, then passed in 8 s on retry).
#
# 15 s is still ~7x the normal render, so a contended-but-fine page has ample room, while a
# genuinely broken locator is reported in half the time — reruns included. `expect()` keeps its
# own 5 s default, which tests already raise to 10 s where they mean to wait for a poll.
ACTION_TIMEOUT_MS = 15_000


@pytest.fixture
def ui_page(ui, page):
    """A signed-in page: token pre-seeded, JS errors collected. Asserts NO uncaught JS
    error happened during the test — a page that throws is broken even if it renders.
    """
    page.set_default_timeout(ACTION_TIMEOUT_MS)
    page.add_init_script(f"localStorage.setItem('rsched_token', {TOKEN!r})")
    page.on("pageerror", lambda exc: ui.js_errors.append(str(exc)))

    yield page

    assert ui.js_errors == [], f"uncaught JS errors: {ui.js_errors}"
