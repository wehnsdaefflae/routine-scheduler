"""Shared fixtures: tmp routine dirs and the ScriptedEndpoint — the engine's main test
harness. It replays a queue of canned replies (dict = action, str = raw text, Exception =
raised, callable = side-effect hook returning any of those) for every completion call.
An entry may be routed: a ("marker", item) tuple is consumed only by conversations whose
SYSTEM prompt contains the marker — that makes parallel sub-workflow tests deterministic
(each child's system prompt embeds its own spawn prompt)."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

# The app lifespan launches a pdoc docs build in a thread that shutdown can only AWAIT —
# without this, every TestClient(app)/uvicorn test pays ~3s of teardown (and a test that
# points source_repo elsewhere pays a full ~19s rebuild). test_docs_build clears the var
# to exercise the real path.
os.environ.setdefault("RSCHED_SKIP_DOCS_BUILD", "1")
# Endpoint retries: keep the 3-try LOGIC, zero the 1s/2s backoff clock — every test that
# points a call at a dead endpoint (autolabel against dummy:127.0.0.1:1, refusal paths)
# otherwise pays ~3s of pure sleep. test_with_retries_backoff clears the var to pin the
# real production delays.
os.environ.setdefault("RSCHED_RETRY_BASE_DELAY", "0.01")

import pytest
import yaml

from rsched.config import ModelRef, ServerConfig
from rsched.endpoints import EndpointRegistry
from rsched.endpoints.base import Completion


@pytest.fixture(autouse=True)
def _hermetic_home(tmp_path, monkeypatch):
    """Tests must NEVER touch the real ~/routines: a bare ServerConfig() defaults its
    routines_home to expand("~/routines"), and engine/daemon code logs health events under
    routines_home/.control — so every pytest run used to append fixture noise (run_failed
    for 'aborted', 'testr', 'wubad', ...) into the LIVE health-events.jsonl. Redirect all
    "~" expansion in rsched.config (field defaults + HomePath validation, both of which
    resolve `expand` at call time) into this test's tmp dir. The SECRETS store is
    redirected too: the settings endpoint view reads it on every listing (credential-source
    labels), and assertions must not vary with whatever the host's real store contains."""
    from rsched import paths as _paths
    fake_home = tmp_path / "hermetic-home"
    real = _paths.expand
    def expand(v):
        s = str(v)
        if s == "~" or s.startswith("~/"):
            return fake_home / s[2:] if len(s) > 1 else fake_home
        return real(v)
    # the config package resolves `expand` at call time in TWO modules (field defaults
    # in server.py, the HomePath validator in base.py) — patch both
    monkeypatch.setattr("rsched.config.base.expand", expand)
    monkeypatch.setattr("rsched.config.server.expand", expand)
    monkeypatch.setattr("rsched.secrets.secrets_path",
                        lambda: fake_home / ".config/routine-scheduler/secrets.env")


@pytest.fixture(autouse=True)
def _failover_reset():
    """The failover cooldown registry is process-global by design (one engine subprocess =
    one run tree); in the one long-lived pytest process it must not leak between tests."""
    from rsched.endpoints import failover
    failover.reset()
    yield
    failover.reset()


WORKFLOW_MD = """---
materialized_from: {slug: test-flow, commit: abc123, version: 1}
adapted: 2026-07-08
---

## Run flow
1. Do what the instruction says, with as few actions as possible.
2. Record anything durable, then finish.

## Phases
- **only** — single phase.

## Completion criteria
- The instruction is fulfilled and a finish summary is written.
"""


class ScriptedEndpoint:
    def __init__(self, replies: list, multimodal: bool = False):
        self.replies = list(replies)
        self.calls: list[dict] = []
        self.lock = threading.Lock()
        self.name = "scripted"
        self.context_chars = 200_000
        # the resolved model's multimodal flag flows in via supports_media(multimodal=…); this
        # per-instance flag is what ScriptedRegistry.resolve puts on the ModelRef it carries.
        self.multimodal = multimodal

    def supports_media(self, media_type: str, *, multimodal: bool) -> bool:
        from rsched.endpoints.base import supports_media_type
        return supports_media_type(media_type, multimodal=multimodal, pdf=True)

    def complete(self, messages, *, model, schema=None, effort=None, max_tokens=None,
                 timeout=600, session=None, temperature=None):
        system = messages[0]["content"] if messages else ""
        with self.lock:
            self.calls.append({"messages": [dict(m) for m in messages], "model": model,
                               "schema": schema, "session": session,
                               "max_tokens": max_tokens, "effort": effort})
            item = None
            for i, entry in enumerate(self.replies):
                if isinstance(entry, tuple):
                    marker, candidate = entry
                    if marker in system:
                        item = candidate
                        self.replies.pop(i)
                        break
                else:
                    item = entry
                    self.replies.pop(i)
                    break
            if item is None:
                raise AssertionError("ScriptedEndpoint ran out of matching replies")
        if callable(item):
            item = item()
        if isinstance(item, Exception):
            raise item
        if isinstance(item, Completion):   # a fully-scripted reply (empty/stop_reason cases)
            return item
        usage = {"in": 10, "out": 5}
        if isinstance(item, dict):
            return Completion(text=json.dumps(item), parsed=item if schema else None, usage=usage)
        return Completion(text=str(item), usage=usage)


class ScriptedRegistry(EndpointRegistry):
    def __init__(self, endpoint: ScriptedEndpoint):
        super().__init__(ServerConfig())
        self.endpoint = endpoint

    def get(self, name: str):
        # Wrap like the real registry so tests exercise the instrumentation seam. With the
        # default sink (None) the wrapper is a pure passthrough — existing tests are unaffected.
        from rsched.endpoints.instrument import InstrumentedEndpoint
        return InstrumentedEndpoint(self.endpoint)

    def resolve(self, name):
        # Every catalog name resolves to the one scripted endpoint. Carry the endpoint's
        # multimodal flag + context window onto the ModelRef the engine reads (supports_media /
        # compaction), bypassing the real catalog lookup (tests configure no catalog).
        return self.get(name), ModelRef(endpoint="scripted", model="test-model",
                                        multimodal=self.endpoint.multimodal,
                                        context_chars=self.endpoint.context_chars,
                                        name=name or "system")

    def for_model(self, kind, models):
        return self.resolve((models or {}).get(kind) or "system")

    def for_model_chain(self, kind, models):
        # the engine's failover seam walks the chain; scripted tests have a chain of one
        return [self.for_model(kind, models)]

    def for_uncensored(self, models):
        name = (models or {}).get("uncensored")
        return self.resolve(name) if name else None

    def for_system(self):
        return self.resolve("system")


TEST_TOKEN = "test-token"


def make_test_server(tmp_path, **overrides):
    """Write the hermetic test config.yaml (dummy endpoint + one-model catalog as the
    system model, bearer TEST_TOKEN) merged with `overrides`, and load it. The one server
    builder behind api_client and every file that needs extra homes/keys on top."""
    from rsched.config import load_server_config

    cfg = {
        "token": TEST_TOKEN,
        "routines_home": str(tmp_path / "routines"),
        "libraries_home": str(tmp_path / "library"),
        "endpoints": {"dummy": {"kind": "openai", "base_url": "http://127.0.0.1:1/v1"}},
        "models": {"m": {"endpoint": "dummy", "model": "m"}},
        "system_model": "m",
        **overrides,
    }
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    server, problems = load_server_config(tmp_path / "config.yaml")
    assert not problems
    (tmp_path / "routines").mkdir(exist_ok=True)
    return server


@pytest.fixture
def api_client(tmp_path):
    """(TestClient, tmp_path) over a hermetic app: tmp homes, bearer auth TEST_TOKEN, a dummy
    endpoint + one-model catalog as the system model, no scheduler. The shared base for the
    web-API test files — each layers its own routines/monkeypatches on top."""
    from fastapi.testclient import TestClient

    from rsched.web.app import create_app

    server = make_test_server(tmp_path)
    app = create_app(server, with_scheduler=False)
    with TestClient(app) as c:
        c.headers["Authorization"] = f"Bearer {TEST_TOKEN}"
        yield c, tmp_path


@pytest.fixture
def make_routine(tmp_path):
    def _make(slug: str = "testr", *, budgets: dict | None = None,
              workflow_md: str = WORKFLOW_MD,
              instruction: str | None = None) -> Path:
        d = tmp_path / "routines" / slug
        (d / "state").mkdir(parents=True)
        (d / "inbox").mkdir()
        cfg = {
            "name": f"Test {slug}", "slug": slug, "enabled": True,
            "description": "A test routine.",
            "schedule": {"cron": "0 7 * * 1", "tz": "Europe/Berlin", "catchup": "skip"},
            "workflow": {"library_slug": "test-flow", "library_commit": "abc123"},
            "budgets": {"max_turns": 10, "max_wall_clock_min": 5, "max_total_tokens": 100_000,
                        "max_subruns": 2, "max_subrun_depth": 1, "ask_timeout_min": 1,
                        **(budgets or {})},
        }
        (d / "routine.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
        if instruction is not None:   # real routines don't persist a seed; wizard-shaped tests do
            (d / "instruction.md").write_text(instruction, encoding="utf-8")
        (d / "main.md").write_text(workflow_md, encoding="utf-8")   # the routine's materialized recipe
        (d / "LEDGER.md").write_text("# LEDGER\n\n### seed — routine created for tests\n",
                                     encoding="utf-8")
        return d

    return _make


@pytest.fixture
def scripted(monkeypatch):
    """Returns a factory: scripted([replies]) → ScriptedEndpoint wired into run_routine
    (runtime.EndpointRegistry is monkeypatched) with fast polling."""
    import rsched.engine.loop as loop_mod
    import rsched.engine.runtime as runtime_mod

    monkeypatch.setattr(loop_mod, "POLL_S", 0.02)
    loop_mod._ABORT["flag"] = False

    def _factory(replies: list) -> ScriptedEndpoint:
        ep = ScriptedEndpoint(replies)
        monkeypatch.setattr(runtime_mod, "EndpointRegistry", lambda server: ScriptedRegistry(ep))
        return ep

    yield _factory
    loop_mod._ABORT["flag"] = False


def finish(status="ok", summary="done"):
    return {"say": "Wrapping up.", "kind": "finish", "status": status, "summary": summary}


def util(name, args=None, say="Running a util."):
    action = {"say": say, "kind": "util", "name": name}
    if args:
        action["args"] = args
    return action


def write_file(path, content="x", say="Writing a file."):
    return {"say": say, "kind": "write_file", "path": path, "content": content}


def spawn(prompt, label=None, workflow=None, say="Delegating."):
    action = {"say": say, "kind": "spawn", "prompt": prompt}
    if label:
        action["label"] = label
    if workflow:
        action["workflow"] = workflow
    return action


def subtask(prompt, label=None, workflow=None, turns=None, say="Running a subtask."):
    action = {"say": say, "kind": "subtask", "prompt": prompt}
    if label:
        action["label"] = label
    if workflow:
        action["workflow"] = workflow
    if turns is not None:
        action["turns"] = turns
    return action


def wait_(n=None, all_=False, timeout_s=None, say="Waiting for children."):
    action = {"say": say, "kind": "wait"}
    if n is not None:
        action["n"] = n
    if all_:
        action["all"] = True
    if timeout_s is not None:
        action["timeout_s"] = timeout_s
    return action


# ---- shared daemon-runner double + on-disk builders -------------------------------------------
# One FakeRunner for the scheduler/trigger/schedule-once/hooks tests (records fires and
# resumes, tracks active slugs); the detached-manager suite subclasses it (status-writing
# fire + guarded resume). One git helper and one run-dir factory replace the per-file copies.


class FakeRunner:
    """Runner double: records fire/resume, marks the slug active, returns the run id.
    active_states/recover_orphans satisfy the scheduler protocol as no-ops."""

    def __init__(self, *, ts: str = "20260717-120000"):
        self.fired: list[tuple[str, str]] = []
        self.resumed: list[tuple[str, str, str]] = []
        self.active: dict[str, str] = {}
        self.draining = False
        self.ts = ts

    def is_active(self, slug: str) -> bool:
        return slug in self.active

    def active_states(self):
        return []

    def recover_orphans(self, catalog):
        return 0

    async def fire(self, cfg, *, reason="schedule") -> str:
        self.fired.append((cfg.slug, reason))
        self.active[cfg.slug] = self.ts
        return f"{cfg.slug}:{self.ts}"

    async def resume(self, cfg, ts, *, reason="resume") -> str | None:
        self.resumed.append((cfg.slug, ts, reason))
        self.active[cfg.slug] = ts
        return f"{cfg.slug}:{ts}"


def git_in(d, *args, date: str = "", check: bool = True):
    """Run git in `d` with a pinned test identity (and optionally pinned dates) — the one
    subprocess-git helper for the suite. Returns the CompletedProcess (use .stdout for
    output asserts, check=False to probe outcomes)."""
    import subprocess

    env = dict(os.environ)
    if date:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = date
    return subprocess.run(["git", "-C", str(d), "-c", "user.name=t", "-c", "user.email=t@t",
                           *args], capture_output=True, text=True, timeout=30,
                          check=check, env=env)


def mk_run(routine_dir: Path, ts: str, state: str, *, turn: int = 3, pid: int | None = None,
           usage: dict | None = None, elapsed_s: float | None = None, question=None,
           summary: str | None = None, outcome: str | None = None, model: str | None = None,
           updated: str | None = None, transcript: list[dict] | None = None) -> Path:
    """One run-dir factory: runs/<ts>/status.json plus optional result.md / transcript
    lines. Callers pass only the fields their assertions need."""
    from rsched.paths import atomic_write_json

    run_dir = routine_dir / "runs" / ts
    run_dir.mkdir(parents=True, exist_ok=True)
    st: dict = {"run_id": f"{routine_dir.name}:{ts}", "state": state, "turn": turn}
    if pid is not None:
        st["pid"] = pid
    if usage is not None:
        st["usage"] = usage
    if elapsed_s is not None:
        st["elapsed_s"] = elapsed_s
    if question is not None:
        st["question"] = question
    if outcome is not None:
        st["outcome"] = outcome
    if model is not None:
        st["model"] = model
    if updated is not None:
        st["updated"] = updated
    atomic_write_json(run_dir / "status.json", st)
    if summary:
        (run_dir / "result.md").write_text(summary)
    if transcript is not None:
        (run_dir / "transcript.jsonl").write_text(
            "".join(json.dumps(e) + "\n" for e in transcript))
    return run_dir
