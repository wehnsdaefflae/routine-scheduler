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
    monkeypatch.setattr("rsched.config.expand", expand)
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


@pytest.fixture
def api_client(tmp_path):
    """(TestClient, tmp_path) over a hermetic app: tmp homes, bearer auth TEST_TOKEN, a dummy
    endpoint + one-model catalog as the system model, no scheduler. The shared base for the
    web-API test files — each layers its own routines/monkeypatches on top."""
    from fastapi.testclient import TestClient

    from rsched.config import load_server_config
    from rsched.web.app import create_app

    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.safe_dump({
        "token": TEST_TOKEN,
        "routines_home": str(tmp_path / "routines"),
        "libraries_home": str(tmp_path / "library"),
        "endpoints": {"dummy": {"kind": "openai", "base_url": "http://127.0.0.1:1/v1"}},
        "models": {"m": {"endpoint": "dummy", "model": "m"}},
        "system_model": "m",
    }))
    server, problems = load_server_config(cfg_path)
    assert not problems
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
