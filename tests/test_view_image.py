"""view_image: action validation, the executor's native-vs-vision routing, the auto-attach
helper + inbox drain, and the loop's runtime fallback net. No network."""

from __future__ import annotations

import json
from types import SimpleNamespace

from rsched import utils_run
from rsched.endpoints.base import EndpointError, supports_media_type
from rsched.engine import executor, fileops
from rsched.engine.actions import KIND_EXAMPLES, validate_action
from rsched.engine.actionschema import KINDS

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
    routine = SimpleNamespace(dir=tmp_path, fs_read_roots=[], fs_write_roots=[], models={})
    # for_model returns (endpoint, resolved ModelRef): the model's multimodal flag is what the
    # executor passes into supports_media (one endpoint serves many models).
    ref = SimpleNamespace(multimodal=endpoint.multimodal, context_chars=200_000) if endpoint else None
    registry = SimpleNamespace(for_model=lambda k, m: (endpoint, ref)) if endpoint else None
    return SimpleNamespace(routine=routine, grants=None, root_run_dir=tmp_path / "runs" / "x",
                           read_roots=lambda: list(routine.fs_read_roots),
                           write_roots=lambda: list(routine.fs_write_roots),
                           server=SimpleNamespace(libraries_home=tmp_path / "utils"), registry=registry,
                           seen_paths=set())


def test_edit_file_near_miss_hint_shows_true_line(tmp_path):
    """F232: when an anchor almost matches but differs on an invisible/ambiguous character (here a
    non-ASCII em-dash — vs a hyphen -), the 'anchor not found' error names the closest ACTUAL line
    via repr(), so the caller sees the true bytes to copy instead of guessing across turns."""
    (tmp_path / "note.md").write_text("take B — see run.py\nnext line\n", encoding="utf-8")
    obs = fileops.do_edit_file(
        {"kind": "edit_file", "path": "note.md",
         "anchor": "take B - see run.py", "replacement": "x"},   # hyphen, not em-dash
        _ctx(tmp_path, None))
    assert "anchor not found" in obs["error"]
    assert "Closest line" in obs["error"]
    assert "\\u2014" in obs["error"] or "—" in obs["error"]   # repr() reveals the em-dash
    # a genuinely absent anchor gets no misleading hint
    obs2 = fileops.do_edit_file(
        {"kind": "edit_file", "path": "note.md",
         "anchor": "completely unrelated content xyzzy", "replacement": "x"},
        _ctx(tmp_path, None))
    assert "Closest line" not in obs2["error"]


def test_do_view_image_native(tmp_path):
    (tmp_path / "shot.png").write_bytes(b"IMG")
    obs = executor.do_view_image({"kind": "view_image", "path": "shot.png"},
                                 _ctx(tmp_path, _Endpoint(True)))
    assert obs["media"] == [{"path": str(tmp_path / "shot.png"), "media_type": "image/png"}]
    assert obs["files"][0]["native"] is True and "abspath" not in obs["files"][0]


def test_do_view_image_vision_fallback(tmp_path, monkeypatch):
    (tmp_path / "shot.png").write_bytes(b"IMG")
    monkeypatch.setattr(fileops, "vision_describe", lambda ctx, _ab, pr: "a red square")
    obs = executor.do_view_image({"kind": "view_image", "path": "shot.png", "prompt": "?"},
                                 _ctx(tmp_path, _Endpoint(False)))
    assert "media" not in obs
    assert obs["files"][0]["via"] == "vision-util" and obs["files"][0]["text"] == "a red square"


def test_do_view_image_no_endpoint_uses_vision(tmp_path, monkeypatch):
    (tmp_path / "shot.png").write_bytes(b"IMG")
    monkeypatch.setattr(fileops, "vision_describe", lambda *a: "described")
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
    monkeypatch.setattr("rsched.engine.fileops.NATIVE_MEDIA_MAX_BYTES", 4)
    monkeypatch.setattr(fileops, "vision_describe", lambda *a: "described")
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
    routine = SimpleNamespace(dir=tmp_path, fs_read_roots=[], fs_write_roots=[])
    ctx = SimpleNamespace(server=SimpleNamespace(libraries_home=tmp_path, sandbox="off"),
                          routine=routine, read_roots=list, write_roots=list)
    monkeypatch.setattr(utils_lib, "exists", lambda home, n: True)
    monkeypatch.setattr(utils_run, "run_util",
                        lambda home, n, args, timeout=300, policy=None, **_kw:
                        (0, json.dumps({"text": "hi"}), ""))
    assert fileops.vision_describe(ctx, "/x.png", "?") == "hi"
    monkeypatch.setattr(utils_run, "run_util", lambda *a, **k: (1, "", "boom"))
    assert fileops.vision_describe(ctx, "/x.png", "?").startswith("error:")
    monkeypatch.setattr(utils_lib, "exists", lambda home, n: False)
    assert "not installed" in fileops.vision_describe(ctx, "/x.png", "?")


# --- auto-attach helper + inbox drain ----------------------------------------

def test_media_from_paths_filters(tmp_path):
    (tmp_path / "a.png").write_bytes(b"x")
    (tmp_path / "b.txt").write_text("x")
    (tmp_path / "c.pdf").write_bytes(b"x")
    out = fileops.media_from_paths(_ctx(tmp_path, _Endpoint(True)),
                                    ["a.png", "b.txt", "c.pdf", "missing.png"])
    assert {m["media_type"] for m in out} == {"image/png", "application/pdf"}
    assert fileops.media_from_paths(_ctx(tmp_path, _Endpoint(False)), ["a.png"]) == []


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


def test_inject_user_message_event_carries_attachments(make_routine, tmp_path, monkeypatch):
    """The user_injection transcript event records the attachment rels — the UI renders
    the files inline from them (user report 2026-08-22: the transcript showed only the
    bare filename list). A message without attachments keeps the payload lean."""
    from rsched.engine import control
    from rsched.engine.transcript import read_events
    monkeypatch.setattr(fileops, "media_from_paths", lambda _ctx, _rels: [])
    loop = _loop(make_routine, tmp_path)
    control.inject_user_message(loop, {"text": "see the screenshot",
                                       "attachments": ["attachments/shot.png"]})
    control.inject_user_message(loop, {"text": "plain", "attachments": []})
    events, _off = read_events(loop.ctx.run_dir / "transcript.jsonl")
    evs = [e for e in events if e["type"] == "user_injection"]
    assert evs[0]["payload"]["attachments"] == ["attachments/shot.png"]
    assert "attachments" not in evs[1]["payload"]


def test_apply_media_fallback(make_routine, tmp_path, monkeypatch):
    monkeypatch.setattr(fileops, "vision_describe", lambda ctx, _ab, pr: "DESCRIBED")
    loop = _loop(make_routine, tmp_path)
    loop.messages = [{"role": "user", "content": "OBS",
                      "media": [{"path": str(tmp_path / "x.png"), "media_type": "image/png"}]}]
    from rsched.engine.window import apply_media_fallback
    assert apply_media_fallback(loop, EndpointError("nope")) is True
    assert "media" not in loop.messages[-1]
    assert "DESCRIBED" in loop.messages[-1]["content"]
    # a tail with no media → False: a genuine endpoint error must propagate
    loop.messages = [{"role": "user", "content": "plain"}]
    assert apply_media_fallback(loop, EndpointError("x")) is False


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


def test_read_file_end_truncates_and_resumes_in_sequence(tmp_path):
    """A file read past the observation cap keeps whole HEAD lines and drops the tail (not the
    head+tail elision opaque output gets), reporting the next start_line so a follow-up read
    continues in order. Operator AUDIT note / F204."""
    from types import SimpleNamespace

    from rsched.engine import fileops
    from rsched.engine.observations import OBS_CAP_CHARS

    big = tmp_path / "big.txt"
    big.write_text("\n".join(f"line-{i:05d}-{'x' * 24}" for i in range(4000)))
    ctx = SimpleNamespace(routine=SimpleNamespace(dir=tmp_path, fs_read_roots=[]),
                          grants=None, seen_paths=set(), read_roots=list)

    obs = fileops._read_one("big.txt", {"max_lines": 500}, ctx)
    assert obs["truncated"] is True
    assert "line-00000-" in obs["content"]                  # head preserved
    assert "line-00499-" not in obs["content"]              # tail dropped, not head+tail
    assert obs["end_line"] < 500                            # fewer lines than the window
    assert len(obs["content"]) <= OBS_CAP_CHARS + 300       # bounded (content + marker)
    assert f"start_line={obs['end_line'] + 1}" in obs["content"]

    nxt = fileops._read_one("big.txt", {"start_line": obs["end_line"] + 1, "max_lines": 500}, ctx)
    assert nxt["start_line"] == obs["end_line"] + 1         # resumes in sequence
    assert f"line-{obs['end_line']:05d}-" in nxt["content"]  # the next line is now shown
