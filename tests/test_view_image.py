"""view_image: action validation, the executor's native-vs-vision routing, the auto-attach
helper + inbox drain, and the loop's runtime fallback net. No network."""

from __future__ import annotations

import json
from types import SimpleNamespace

from rsched.endpoints.base import EndpointError, supports_media_type
from rsched.engine import executor
from rsched.engine.actions import KIND_EXAMPLES, KINDS, validate_action

# --- action schema -----------------------------------------------------------

def test_view_image_registered_and_example_valid():
    assert "view_image" in KINDS
    assert validate_action(KIND_EXAMPLES["view_image"]) == []


def test_view_image_path_or_paths():
    assert validate_action({"say": "x", "kind": "view_image", "path": "a.png"}) == []
    assert validate_action({"say": "x", "kind": "view_image", "paths": ["a.png", "b.jpg"]}) == []
    assert any("requires 'path'" in p
               for p in validate_action({"say": "x", "kind": "view_image"}))
    assert any("OR 'paths'" in p for p in validate_action(
        {"say": "x", "kind": "view_image", "path": "a.png", "paths": ["b.png"]}))


def test_view_image_rejects_memory_and_allows_prompt():
    assert any(".memory/" in p for p in validate_action(
        {"say": "x", "kind": "view_image", "path": ".memory/x.png"}))
    assert validate_action({"say": "x", "kind": "view_image", "path": "a.png", "prompt": "w"}) == []


# --- executor routing --------------------------------------------------------

class _Endpoint:
    def __init__(self, multimodal):
        self.multimodal = multimodal

    def supports_media(self, mime, *, multimodal):
        return supports_media_type(mime, multimodal=multimodal, pdf=True)


def _ctx(tmp_path, endpoint):
    routine = SimpleNamespace(dir=tmp_path, fs_read_roots=[], models={})
    # for_model returns (endpoint, resolved ModelRef): the model's multimodal flag is what the
    # executor passes into supports_media (one endpoint serves many models).
    ref = SimpleNamespace(multimodal=endpoint.multimodal, context_chars=200_000) if endpoint else None
    registry = SimpleNamespace(for_model=lambda k, m: (endpoint, ref)) if endpoint else None
    return SimpleNamespace(routine=routine, grants=None, root_run_dir=tmp_path / "runs" / "x",
                           server=SimpleNamespace(utils_home=tmp_path / "utils"), registry=registry)


def test_do_view_image_native(tmp_path):
    (tmp_path / "shot.png").write_bytes(b"IMG")
    obs = executor.do_view_image({"kind": "view_image", "path": "shot.png"},
                                 _ctx(tmp_path, _Endpoint(True)))
    assert obs["media"] == [{"path": str(tmp_path / "shot.png"), "media_type": "image/png"}]
    assert obs["files"][0]["native"] is True and "abspath" not in obs["files"][0]


def test_do_view_image_vision_fallback(tmp_path, monkeypatch):
    (tmp_path / "shot.png").write_bytes(b"IMG")
    monkeypatch.setattr(executor, "vision_describe", lambda ctx, ab, pr: "a red square")
    obs = executor.do_view_image({"kind": "view_image", "path": "shot.png", "prompt": "?"},
                                 _ctx(tmp_path, _Endpoint(False)))
    assert "media" not in obs
    assert obs["files"][0]["via"] == "vision-util" and obs["files"][0]["text"] == "a red square"


def test_do_view_image_no_endpoint_uses_vision(tmp_path, monkeypatch):
    (tmp_path / "shot.png").write_bytes(b"IMG")
    monkeypatch.setattr(executor, "vision_describe", lambda *a: "described")
    obs = executor.do_view_image({"kind": "view_image", "path": "shot.png"}, _ctx(tmp_path, None))
    assert obs["files"][0]["via"] == "vision-util"


def test_do_view_image_rejects_non_media(tmp_path):
    (tmp_path / "notes.txt").write_text("hi")
    obs = executor.do_view_image({"kind": "view_image", "path": "notes.txt"},
                                 _ctx(tmp_path, _Endpoint(True)))
    assert "not a viewable" in obs["files"][0]["error"]


def test_do_view_image_missing_file(tmp_path):
    obs = executor.do_view_image({"kind": "view_image", "path": "nope.png"},
                                 _ctx(tmp_path, _Endpoint(True)))
    assert "does not exist" in obs["files"][0]["error"]


def test_do_view_image_oversize_uses_vision(tmp_path, monkeypatch):
    monkeypatch.setattr(executor, "NATIVE_MEDIA_MAX_BYTES", 4)
    monkeypatch.setattr(executor, "vision_describe", lambda *a: "described")
    (tmp_path / "shot.png").write_bytes(b"toolong")
    obs = executor.do_view_image({"kind": "view_image", "path": "shot.png"},
                                 _ctx(tmp_path, _Endpoint(True)))
    assert "media" not in obs and obs["files"][0]["via"] == "vision-util"


def test_do_view_image_batched_mixed(tmp_path):
    (tmp_path / "a.png").write_bytes(b"IMG")
    (tmp_path / "b.txt").write_text("hi")
    obs = executor.do_view_image({"kind": "view_image", "paths": ["a.png", "b.txt"]},
                                 _ctx(tmp_path, _Endpoint(True)))
    assert obs["media"] == [{"path": str(tmp_path / "a.png"), "media_type": "image/png"}]
    assert obs["files"][0]["native"] is True
    assert "not a viewable" in obs["files"][1]["error"]


# --- vision_describe ---------------------------------------------------------

def test_vision_describe_parses_and_errors(tmp_path, monkeypatch):
    from rsched import utils_lib
    ctx = SimpleNamespace(server=SimpleNamespace(utils_home=tmp_path))
    monkeypatch.setattr(utils_lib, "exists", lambda home, n: True)
    monkeypatch.setattr(utils_lib, "run_util",
                        lambda home, n, args, timeout=300: (0, json.dumps({"text": "hi"}), ""))
    assert executor.vision_describe(ctx, "/x.png", "?") == "hi"
    monkeypatch.setattr(utils_lib, "run_util", lambda *a, **k: (1, "", "boom"))
    assert executor.vision_describe(ctx, "/x.png", "?").startswith("error:")
    monkeypatch.setattr(utils_lib, "exists", lambda home, n: False)
    assert "not installed" in executor.vision_describe(ctx, "/x.png", "?")


# --- auto-attach helper + inbox drain ----------------------------------------

def test_media_from_paths_filters(tmp_path):
    (tmp_path / "a.png").write_bytes(b"x")
    (tmp_path / "b.txt").write_text("x")
    (tmp_path / "c.pdf").write_bytes(b"x")
    out = executor.media_from_paths(_ctx(tmp_path, _Endpoint(True)),
                                    ["a.png", "b.txt", "c.pdf", "missing.png"])
    assert {m["media_type"] for m in out} == {"image/png", "application/pdf"}
    assert executor.media_from_paths(_ctx(tmp_path, _Endpoint(False)), ["a.png"]) == []


def test_drain_messages_carries_attachments(tmp_path):
    from rsched.engine import inbox
    from rsched.paths import atomic_write_json
    d = tmp_path / "r"
    (d / "inbox").mkdir(parents=True)
    atomic_write_json(d / "inbox" / "msg-1.json",
                      {"text": "hi", "attachments": ["attachments/a.png"]})
    atomic_write_json(d / "inbox" / "msg-2.json", {"text": "yo"})
    assert inbox.drain_messages(d, tmp_path / "consumed") == [
        {"text": "hi", "attachments": ["attachments/a.png"]},
        {"text": "yo", "attachments": []}]


# --- loop runtime fallback net -----------------------------------------------

def _loop(make_routine, tmp_path):
    from rsched.config import ServerConfig, load_routine
    from rsched.engine.loop import EngineLoop
    from rsched.engine.run_context import Budgets, RunContext
    from rsched.engine.transcript import Transcript
    d = make_routine(slug="mm")
    server = ServerConfig()
    server.routines_home = d.parent
    run_dir = d / "runs" / "20260714-070000"
    run_dir.mkdir(parents=True)
    cfg, _ = load_routine(d)
    ctx = RunContext(routine=cfg, server=server, registry=None, run_ts="20260714-070000",
                     run_dir=run_dir, transcript=Transcript(run_dir / "transcript.jsonl"),
                     budgets=Budgets.from_config(cfg.budgets))
    return EngineLoop(ctx, "## Run flow", "instr")


def test_apply_media_fallback(make_routine, tmp_path, monkeypatch):
    monkeypatch.setattr(executor, "vision_describe", lambda ctx, ab, pr: "DESCRIBED")
    loop = _loop(make_routine, tmp_path)
    loop.messages = [{"role": "user", "content": "OBS",
                      "media": [{"path": str(tmp_path / "x.png"), "media_type": "image/png"}]}]
    assert loop._apply_media_fallback(EndpointError("nope")) is True
    assert "media" not in loop.messages[-1]
    assert "DESCRIBED" in loop.messages[-1]["content"]
    # a tail with no media → False: a genuine endpoint error must propagate
    loop.messages = [{"role": "user", "content": "plain"}]
    assert loop._apply_media_fallback(EndpointError("x")) is False


def test_view_image_native_end_to_end(make_routine, scripted, tmp_path):
    """A scripted run: view_image on a multimodal endpoint → the observation carries media,
    and the loop attaches it to the NEXT completion's tail user message (the model sees it)."""
    from rsched.config import ServerConfig
    from rsched.engine.runtime import run_routine
    from rsched.engine.transcript import read_events
    d = make_routine(slug="mmrun")
    (d / "shot.png").write_bytes(b"IMG")
    ep = scripted([
        {"say": "look", "kind": "view_image", "path": "shot.png"},
        {"say": "done", "kind": "finish", "status": "ok", "summary": "saw the image"},
    ])
    ep.multimodal = True                          # the run's main endpoint is multimodal
    server = ServerConfig()
    server.routines_home = d.parent
    server.libraries_home = tmp_path / "lib"
    status, run_dir = run_routine(d, server, run_ts="20260714-071500")
    assert status == "ok"
    events, _ = read_events(run_dir / "transcript.jsonl")
    view_obs = [e for e in events if e["type"] == "observation"
                and e["payload"].get("kind") == "view_image"]
    assert view_obs and view_obs[0]["payload"]["media"][0]["media_type"] == "image/png"
    assert ep.calls[-1]["messages"][-1].get("media")   # media rode the finish turn's prompt
