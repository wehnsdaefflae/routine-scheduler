"""Playwright UI harness — the REAL console (FastAPI + static frontend) served by uvicorn
on an ephemeral port, backed by fixture homes and a stub runner: no scheduler, no engine
subprocess, no LLM. Tests drive the browser against the same JS the daemon serves.

The browser signs in by pre-seeding localStorage with the fixture token (api.js reads
`rsched_token`); the `.setup-complete` marker next to the fixture config suppresses the
first-launch redirect to Settings. JS runtime errors fail the test via the `ui` fixture's
collector — a page that renders but throws is a broken page.
"""

from __future__ import annotations

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


class StubRunner:
    """Records fire/resume calls and answers like an idle daemon — no process is ever
    spawned. Only the surface the web layer touches is implemented.
    """

    def __init__(self):
        self.fired: list[tuple[str, str]] = []
        self.resumed: list[tuple[str, str]] = []
        self.active: dict[str, object] = {}
        self.draining = False

    async def fire(self, cfg, reason: str = "") -> str:
        self.fired.append((cfg.slug, reason))
        return f"{cfg.slug}:20260715-120000"

    async def resume_terminal(self, cfg, reason: str = "") -> str:
        self.resumed.append((cfg.slug, reason))
        return f"{cfg.slug}:20260715-120001"

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
                      expires: str = "", asked: str = "20260714-070000") -> Path:
        """Drop a durable decision record the way the engine files one."""
        pending = self.routines / slug / "questions" / "pending"
        pending.mkdir(parents=True, exist_ok=True)
        record = {"qid": qid, "question": question, "mode": mode, "type": "text",
                  "options": options or [], "default": default, "asked": asked}
        if expires:
            record["expires"] = expires
        path = pending / f"{qid}.json"
        atomic_write_json(path, record)
        return path

    def seed_run(self, slug: str, ts: str, state: str, *, summary: str = "",
                 home: Path | None = None, question: dict | None = None,
                 usage: dict | None = None) -> Path:
        run_dir = (home or self.routines) / slug / "runs" / ts
        run_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_json(run_dir / "status.json", {
            "run_id": f"{slug}:{ts}", "state": state, "pid": 4242, "turn": 2,
            "usage": usage or {"in": 10, "out": 4}, "elapsed_s": 60,
            "question": question, "started": ts, "updated": "2026-07-15T12:00:00+00:00"})
        if summary:
            (run_dir / "result.md").write_text(summary, encoding="utf-8")
        (run_dir / "transcript.jsonl").write_text(
            f'{{"type": "header", "run_id": "{slug}:{ts}"}}\n', encoding="utf-8")
        return run_dir


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def ui(tmp_path, make_routine) -> UiHarness:
    """A live console over fixture state: one routine ('uir'), the seed library
    (so conversations can materialize `converse`), a stub runner, uvicorn on an
    ephemeral port. Tears the server down after the test.
    """
    make_routine(slug="uir")
    library = tmp_path / "library"
    seed_libraries(library)
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.safe_dump({
        "token": TOKEN,
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

    port = _free_port()
    uv_server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                              log_level="warning"))
    thread = threading.Thread(target=uv_server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not uv_server.started:
        if time.monotonic() > deadline:
            pytest.fail("uvicorn did not start within 15s")
        time.sleep(0.05)

    yield UiHarness(url=f"http://127.0.0.1:{port}", tmp=tmp_path,
                    routines=tmp_path / "routines",
                    conversations=tmp_path / "conversations",
                    runner=runner, server_cfg=server_cfg)

    uv_server.should_exit = True
    thread.join(timeout=10)


@pytest.fixture
def ui_page(ui, page):
    """A signed-in page: token pre-seeded, JS errors collected. Asserts NO uncaught JS
    error happened during the test — a page that throws is broken even if it renders.
    """
    page.add_init_script(f"localStorage.setItem('rsched_token', {TOKEN!r})")
    page.on("pageerror", lambda exc: ui.js_errors.append(str(exc)))

    yield page

    assert ui.js_errors == [], f"uncaught JS errors: {ui.js_errors}"
