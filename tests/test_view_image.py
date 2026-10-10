"""view_image: action validation, the executor's native-vs-vision routing, the auto-attach
helper + inbox drain, and the loop's runtime fallback net. No network."""

from __future__ import annotations

import base64
import json
import struct as _struct
import zlib as _zlib
from pathlib import Path as _Path
from types import SimpleNamespace

from helpers import run_context, server_config
from rsched import utils_run
from rsched.endpoints import anthropic_messages, openai_compat
from rsched.endpoints.base import EndpointError, supports_media_type
from rsched.engine import executor, fileops, mediaops, obs_files
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
    ref = SimpleNamespace(multimodal=endpoint.multimodal, context_tokens=200_000) if endpoint else None
    registry = SimpleNamespace(for_model=lambda k, m: (endpoint, ref)) if endpoint else None
    return SimpleNamespace(routine=routine, grants=None, depth=0,
                           root_run_dir=tmp_path / "runs" / "x",
                           read_roots=lambda: list(routine.fs_read_roots),
                           write_roots=lambda: list(routine.fs_write_roots),
                           server=SimpleNamespace(libraries_home=tmp_path / "utils",
                                                  routines_home=tmp_path / "routines"),
                           registry=registry,
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
    assert obs["media"] == [{"path": str(tmp_path / "shot.png"), "media_type": "image/png",
                             "b64": base64.b64encode(b"IMG").decode("ascii")}]
    assert obs["files"][0]["native"] is True and "abspath" not in obs["files"][0]
    assert "b64" not in obs["files"][0]        # the bytes ride the media entry, not the file row


def test_native_media_carries_bytes_so_a_later_overwrite_cannot_unshow_it(tmp_path):
    """R1493: the media entry rides the observation's message, which stays in the conversation,
    so the endpoint re-renders it on EVERY later send. While it carried only a path, a run that
    re-rendered a check artifact into the same filename (or cleaned it up) invalidated an
    attachment the model had already been told to look at — and the failure surfaced inside the
    endpoint, appended to the end of the very line that said "shown to you below; look at it now".

    Pins the fix at both layers: the engine captures the bytes at the one moment it verified the
    file, and the endpoint renderers prefer those bytes over re-reading the path."""
    shot = tmp_path / "shot.png"
    shot.write_bytes(b"ORIGINAL")
    obs = executor.do_view_image({"kind": "view_image", "path": "shot.png"},
                                 _ctx(tmp_path, _Endpoint(True)))
    media = obs["media"]
    assert media[0]["b64"] == base64.b64encode(b"ORIGINAL").decode("ascii")
    # the run moves on and the path stops holding those bytes — the two real cases
    shot.write_bytes(b"A DIFFERENT RENDER ENTIRELY")
    blocks = anthropic_messages.content_blocks("look", media)
    assert blocks[1]["source"]["data"] == base64.b64encode(b"ORIGINAL").decode("ascii")
    parts = openai_compat._openai_content("look", media)
    assert base64.b64encode(b"ORIGINAL").decode("ascii") in parts[1]["image_url"]["url"]
    shot.unlink()
    assert anthropic_messages.content_blocks("look", media)[1]["type"] == "image"
    assert "Attachment unavailable" not in json.dumps(
        openai_compat._openai_content("look", media))
    # an entry with NO captured bytes (conversation auto-attach) still reads from disk, and a
    # missing file there still degrades to the honest note rather than crashing the send
    bare = [{"path": str(shot), "media_type": "image/png"}]
    assert "Attachment unavailable" in json.dumps(openai_compat._openai_content("look", bare))


def test_failed_view_reads_as_a_failure_and_invites_nothing(tmp_path):
    """R1493's second half: the observation line for a file that could NOT be shown must not
    carry the invitation to describe it. The report's author narrated a page twice from a line
    whose head said "look at it now" and whose tail carried the failure."""
    obs = executor.do_view_image({"kind": "view_image", "path": "nope.png"},
                                 _ctx(tmp_path, _Endpoint(True)))
    text = obs_files.format_files(obs, "view_image")
    assert "NOT SHOWN" in text and "describe nothing from it" in text
    assert "look at it now" not in text


def test_do_view_image_vision_fallback(tmp_path, monkeypatch):
    (tmp_path / "shot.png").write_bytes(b"IMG")
    monkeypatch.setattr(mediaops, "vision_describe", lambda ctx, _ab, pr: "a red square")
    obs = executor.do_view_image({"kind": "view_image", "path": "shot.png", "prompt": "?"},
                                 _ctx(tmp_path, _Endpoint(False)))
    assert "media" not in obs
    assert obs["files"][0]["via"] == "vision-util" and obs["files"][0]["text"] == "a red square"


def test_do_view_image_no_endpoint_uses_vision(tmp_path, monkeypatch):
    (tmp_path / "shot.png").write_bytes(b"IMG")
    monkeypatch.setattr(mediaops, "vision_describe", lambda *a: "described")
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
    err = obs["files"][0]["error"]
    # a not-found refusal names the read that shows what IS there, never the bare fact
    assert "does not exist" in err and "read_file its parent directory" in err


def test_do_view_image_oversize_uses_vision(tmp_path, monkeypatch):
    # F623: the byte bound is read by `endpoints.base.oversize_reason`, the ONE predicate both
    # view_image and auto-attach now share, so that is where it must be patched. Patched on
    # `mediaops` it would have no effect and this test would pass having proved nothing.
    monkeypatch.setattr("rsched.endpoints.base.NATIVE_MEDIA_MAX_BYTES", 4)
    monkeypatch.setattr(mediaops, "vision_describe", lambda *a: "described")
    (tmp_path / "shot.png").write_bytes(b"toolong")
    obs = executor.do_view_image({"kind": "view_image", "path": "shot.png"},
                                 _ctx(tmp_path, _Endpoint(True)))
    assert "media" not in obs and obs["files"][0]["via"] == "vision-util"


def test_do_view_image_batched_mixed(tmp_path):
    (tmp_path / "a.png").write_bytes(b"IMG")
    (tmp_path / "b.txt").write_text("hi")
    obs = executor.do_view_image({"kind": "view_image", "paths": ["a.png", "b.txt"]},
                                 _ctx(tmp_path, _Endpoint(True)))
    assert obs["media"] == [{"path": str(tmp_path / "a.png"), "media_type": "image/png",
                             "b64": base64.b64encode(b"IMG").decode("ascii")}]
    assert obs["files"][0]["native"] is True
    assert "not a viewable" in obs["files"][1]["error"]


# --- vision_describe ---------------------------------------------------------

def _vision_ctx(tmp_path, monkeypatch, *, grants=None):
    """A run context carrying what a util call's environment is assembled from, over a
    library whose `vision` util declares one required key, and tmp secret stores."""
    from rsched import secrets

    monkeypatch.setattr(secrets, "secrets_path", lambda: tmp_path / "store" / "secrets.env")
    home = tmp_path / "lib"
    (home / "utils" / "vision").mkdir(parents=True)
    (home / "utils" / "vision" / "main.py").write_text(
        '"""vision — describe a file.\n\nsecrets: VISION_KEY\nnet: outbound\nfs: roots\n"""\n',
        encoding="utf-8")
    routine = SimpleNamespace(slug="seer", dir=tmp_path, fs_read_roots=[], fs_write_roots=[],
                              connections={}, machines=[], grants=grants or {})
    return SimpleNamespace(server=SimpleNamespace(libraries_home=home, sandbox="off",
                                                  machines={}, routine_token=""),
                           routine=routine, read_roots=list, write_roots=list,
                           granted_now=set(), denied_now=set(), grant_args={},
                           aborted=lambda: False)


def test_vision_describe_parses_and_errors(tmp_path, monkeypatch):
    from rsched import utils_lib
    ctx = _vision_ctx(tmp_path, monkeypatch)
    monkeypatch.setattr(utils_run, "run_util",
                        lambda home, n, args, timeout=300, policy=None, **_kw:
                        (0, json.dumps({"text": "hi"}), ""))
    assert mediaops.vision_describe(ctx, "/x.png", "?") == "hi"
    monkeypatch.setattr(utils_run, "run_util", lambda *a, **k: (1, "", "boom"))
    assert mediaops.vision_describe(ctx, "/x.png", "?").startswith("error:")
    monkeypatch.setattr(utils_lib, "exists", lambda home, n: False)
    assert "not installed" in mediaops.vision_describe(ctx, "/x.png", "?")


def test_the_vision_fallback_runs_with_the_routines_own_secret_standing(tmp_path, monkeypatch):
    """The engine's call asks nothing, but it is made in the run's environment: a DECLINED
    key is not handed over (R17: the refusal counts, it does not name), and the routine's
    own scoped key shadows the central one (D103) exactly as on a util the run called."""
    from rsched import secrets

    ctx = _vision_ctx(tmp_path, monkeypatch, grants={"secret:VISION_KEY": False})
    secrets.set_secret("VISION_KEY", "central")
    calls: list[dict] = []
    monkeypatch.setattr(utils_run, "run_util", lambda *a, **k: calls.append(k) or (
        0, json.dumps({"text": "seen"}), ""))
    declined = mediaops.vision_describe(ctx, "/x.png", "?")
    assert declined.startswith("error: the user declined exposing 1 secret the vision")
    assert "VISION_KEY" not in declined and calls == []
    ctx.routine.grants = {}                       # undecided: the engine's call still runs
    secrets.set_routine_secret("seer", "VISION_KEY", "mine")
    assert mediaops.vision_describe(ctx, "/x.png", "?") == "seen"
    assert calls[0]["extra_secrets"]["VISION_KEY"] == "mine"


# --- auto-attach helper + inbox drain ----------------------------------------

def test_media_from_paths_filters(tmp_path):
    (tmp_path / "a.png").write_bytes(b"x")
    (tmp_path / "b.txt").write_text("x")
    (tmp_path / "c.pdf").write_bytes(b"x")
    out = mediaops.media_from_paths(_ctx(tmp_path, _Endpoint(True)),
                                    ["a.png", "b.txt", "c.pdf", "missing.png"])
    assert {m["media_type"] for m in out} == {"image/png", "application/pdf"}
    assert mediaops.media_from_paths(_ctx(tmp_path, _Endpoint(False)), ["a.png"]) == []


def test_drain_messages_carries_attachments(tmp_path):
    from rsched.engine import inbox
    from rsched.paths import atomic_write_json
    d = tmp_path / "r"
    (d / "inbox").mkdir(parents=True)
    atomic_write_json(d / "inbox" / "msg-1.json",
                      {"text": "hi", "attachments": ["attachments/a.png"]})
    atomic_write_json(d / "inbox" / "msg-2.json", {"text": "yo"})
    assert inbox.drain_messages(d, tmp_path / "consumed") == [
        {"text": "hi", "via": "", "attachments": ["attachments/a.png"]},
        {"text": "yo", "via": "", "attachments": []}]


# --- loop runtime fallback net -----------------------------------------------

def _loop(make_routine, tmp_path):
    from rsched.engine.loop import EngineLoop
    d = make_routine(slug="mm")
    ctx = run_context(d, "20260714-070000", server=server_config(routines_home=d.parent))
    return EngineLoop(ctx, "## Run flow", "instr")


def test_inject_user_message_event_carries_attachments(make_routine, tmp_path, monkeypatch):
    """The user_injection transcript event records the attachment rels — the UI renders
    the files inline from them (user report 2026-08-22: the transcript showed only the
    bare filename list). A message without attachments keeps the payload lean."""
    from rsched.engine import control
    from rsched.engine.transcript import read_events
    monkeypatch.setattr(mediaops, "media_from_paths", lambda _ctx, _rels: [])
    loop = _loop(make_routine, tmp_path)
    control.inject_user_message(loop, {"text": "see the screenshot",
                                       "attachments": ["attachments/shot.png"]})
    control.inject_user_message(loop, {"text": "plain", "attachments": []})
    events, _off = read_events(loop.ctx.run_dir / "transcript.jsonl")
    evs = [e for e in events if e["type"] == "user_injection"]
    assert evs[0]["payload"]["attachments"] == ["attachments/shot.png"]
    assert "attachments" not in evs[1]["payload"]


def test_apply_media_fallback(make_routine, tmp_path, monkeypatch):
    monkeypatch.setattr(mediaops, "vision_describe", lambda ctx, _ab, pr: "DESCRIBED")
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


def test_apply_media_fallback_converts_every_message_not_just_the_tail(
        make_routine, tmp_path, monkeypatch):
    """An image the run viewed SEVERAL turns ago is still a liability to a model that
    cannot see one. c-20260922-072125: a user message landed after the media-bearing
    observation, the tail-only fallback found nothing, and the chain walked three
    text-only models to `does not support image inputs` and killed the conversation.
    """
    monkeypatch.setattr(mediaops, "vision_describe", lambda ctx, _ab, pr: "DESCRIBED")
    loop = _loop(make_routine, tmp_path)
    loop.messages = [
        {"role": "user", "content": "OBS",
         "media": [{"path": str(tmp_path / "x.png"), "media_type": "image/png"}]},
        {"role": "assistant", "content": "{}"},
        {"role": "user", "content": "and now do this"},       # the tail carries no media
    ]
    from rsched.engine.window import apply_media_fallback
    assert apply_media_fallback(loop, EndpointError("does not support image inputs")) is True
    assert all("media" not in m for m in loop.messages)
    assert "DESCRIBED" in loop.messages[0]["content"]


def test_the_media_fallback_keeps_the_limit_the_provider_named(
        make_routine, tmp_path, monkeypatch):
    """F623: the provider's refusal is recorded far enough to say WHICH bound it refused on.

    The real Anthropic 400 for an oversized image is long, and the part a reader needs — the
    constraint's name and its value — sits well past the 120 characters the event used to
    keep. The recorded message stopped at `messages.696.content.2.image.`, one character
    before the name, so the one fact worth having was the one fact lost and the finding could
    be neither confirmed nor refuted from disk.
    """
    from rsched.engine.transcript import read_events
    from rsched.engine.window import apply_media_fallback

    monkeypatch.setattr(mediaops, "vision_describe", lambda ctx, _ab, pr: "DESCRIBED")
    loop = _loop(make_routine, tmp_path)
    loop.messages = [{"role": "user", "content": "OBS",
                      "media": [{"path": str(tmp_path / "x.png"), "media_type": "image/png"}]}]
    real_400 = (
        "Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error', "
        "'message': 'messages.696.content.2.image.source.base64.data: At least one of the "
        "image dimensions exceed max allowed size for many-image requests: 2000 pixels'}}")
    assert apply_media_fallback(loop, EndpointError(real_400)) is True

    events, _off = read_events(loop.ctx.run_dir / "transcript.jsonl")
    message = next(e["payload"]["message"] for e in events
                   if e["type"] == "error" and e["payload"].get("where") == "media")
    assert "exceed max allowed size" in message, (
        f"the provider's own diagnosis was truncated away: {message}")
    assert "2000 pixels" in message, (
        f"the LIMIT the provider refused on is what a reader needs: {message}")


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
                          grants=None, depth=0, seen_paths=set(), read_roots=list)

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


# ---- F623 half 2: the PRE-FLIGHT, so one oversized image cannot 400 a whole message -----------
#
# Half 1 (0.384.1) widened the recorded provider message far enough to read the constraint, and
# the number this half checks against is the provider's own, quoted from that very 400:
# "At least one of the image dimensions exceed max allowed size for many-image requests: 2000
# pixels". The engine's only pre-flight before this was NATIVE_MEDIA_MAX_BYTES (7 MiB), which
# cannot express a pixel bound at all — a 2 MB 12000x9000 screenshot passes it and then makes the
# provider refuse the ENTIRE message: every image in it, and the text with them.



def _png(path, width, height):
    """A real, structurally valid PNG of the given dimensions — header fields the reader parses
    plus a CRC'd IHDR, written without Pillow so the test has no dependency the engine lacks."""
    ihdr = _struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    chunk = b"IHDR" + ihdr
    data = (b"\x89PNG\r\n\x1a\n"
            + _struct.pack(">I", len(ihdr)) + chunk + _struct.pack(">I", _zlib.crc32(chunk))
            + _struct.pack(">I", 0) + b"IEND" + _struct.pack(">I", _zlib.crc32(b"IEND")))
    path.write_bytes(data)
    return path


def _gif(path, width, height):
    path.write_bytes(b"GIF89a" + _struct.pack("<HH", width, height) + b"\x00" * 10)
    return path


def _jpeg(path, width, height):
    """SOI, one APP0 to be skipped, then an SOF0 frame header — the marker walk's real shape."""
    app0 = b"\xff\xe0" + _struct.pack(">H", 16) + b"JFIF\x00" + b"\x00" * 9
    sof0 = b"\xff\xc0" + _struct.pack(">H", 17) + b"\x08" + _struct.pack(">HH", height, width) \
        + b"\x03" + b"\x00" * 9
    path.write_bytes(b"\xff\xd8" + app0 + sof0 + b"\xff\xd9")
    return path


def _webp_vp8x(path, width, height):
    body = b"WEBPVP8X" + _struct.pack("<I", 10) + b"\x00" * 4 \
        + (width - 1).to_bytes(3, "little") + (height - 1).to_bytes(3, "little")
    path.write_bytes(b"RIFF" + _struct.pack("<I", len(body)) + body)
    return path


def test_image_dimensions_reads_every_native_format(tmp_path):
    """All four IMAGE_MIMES formats, because a bound that only works for PNG would pass exactly
    the file that slipped through before (a screenshot) and miss a photo."""
    from rsched.endpoints.base import image_dimensions

    assert image_dimensions(_png(tmp_path / "a.png", 1234, 567)) == (1234, 567)
    assert image_dimensions(_gif(tmp_path / "b.gif", 640, 480)) == (640, 480)
    assert image_dimensions(_jpeg(tmp_path / "c.jpg", 4032, 3024)) == (4032, 3024)
    assert image_dimensions(_webp_vp8x(tmp_path / "d.webp", 800, 600)) == (800, 600)


def test_an_unmeasurable_file_reads_as_unknown_and_never_as_within_the_limit(tmp_path):
    """None means "could not measure". If it meant "fine", a header this cannot parse would pass
    a bound it was never checked against — which is the failure mode being fixed, inverted."""
    from rsched.endpoints.base import image_dimensions

    broken = tmp_path / "truncated.png"
    broken.write_bytes(b"\x89PNG\r\n\x1a\n")          # header only, no IHDR
    assert image_dimensions(broken) is None
    assert image_dimensions(tmp_path / "absent.png") is None
    nonsense = tmp_path / "x.png"
    nonsense.write_bytes(b"not an image at all")
    assert image_dimensions(nonsense) is None


def test_the_pixel_limit_is_the_one_the_provider_named():
    """The constant is not a guess. It is the number in the 400 half 1 preserved, and
    test_the_media_fallback_keeps_the_limit_the_provider_named above holds that text."""
    from rsched.endpoints.base import NATIVE_MEDIA_MAX_PIXELS_PER_SIDE

    assert NATIVE_MEDIA_MAX_PIXELS_PER_SIDE == 2000


def test_oversize_reason_names_the_limit_and_the_files_own_size(tmp_path):
    """The whole point of the pre-flight: a run told only "too big" cannot act, and a run told
    "12000x9000 px exceeds 2000 per side" downscales and looks at it. Both numbers, every time."""
    from rsched.endpoints.base import oversize_reason

    ok = _png(tmp_path / "small.png", 1000, 800)
    assert oversize_reason(ok, "image/png") is None

    tall = _png(tmp_path / "tall.png", 1200, 9000)
    reason = oversize_reason(tall, "image/png")
    assert reason and "9000" in reason and "2000" in reason, reason
    assert "1200x9000" in reason, reason
    assert "whole message" in reason.lower() or "WHOLE message" in reason, reason

    wide = _png(tmp_path / "wide.png", 12000, 300)
    assert "12000x300" in (oversize_reason(wide, "image/png") or ""), "the WIDE side counts too"


def test_oversize_reason_judges_a_pdf_by_bytes_alone(tmp_path, monkeypatch):
    """A PDF has no pixel dimensions to measure, so the pixel bound must not be applied to one —
    a PDF that reads as unmeasurable must not be refused for being unmeasurable."""
    from rsched.endpoints import base as base_mod
    from rsched.endpoints.base import oversize_reason

    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.7\n" + b"x" * 2048)
    assert oversize_reason(pdf, "application/pdf") is None
    monkeypatch.setattr(base_mod, "NATIVE_MEDIA_MAX_BYTES", 16)
    assert "ceiling" in (oversize_reason(pdf, "application/pdf") or "")


def test_an_oversized_image_is_described_instead_of_sent_and_the_run_is_told_why(tmp_path):
    """The behaviour through `do_view_image` on a MULTIMODAL endpoint: the model can see images,
    this one is simply too large to send, so it routes to the vision util carrying the measured
    reason — and crucially NO media entry is produced, because that entry is what would have made
    the provider refuse the whole message."""
    from rsched.endpoints.base import NATIVE_MEDIA_MAX_PIXELS_PER_SIDE

    endpoint = SimpleNamespace(multimodal=True,
                               supports_media=lambda _m, *, multimodal: True)
    ctx = _ctx(tmp_path, endpoint)
    _png(tmp_path / "huge.png", 6000, NATIVE_MEDIA_MAX_PIXELS_PER_SIDE + 1)

    obs = mediaops.do_view_image({"kind": "view_image", "path": "huge.png"}, ctx)
    assert "media" not in obs, (
        "the oversized image was still attached — the pre-flight did not prevent the 400")
    f = obs["files"][0]
    assert f.get("via") == "vision-util"
    assert f.get("native") is not True
    assert "2001" in f["oversize"] and "2000" in f["oversize"], f["oversize"]

    # The measurement must reach the model through BOTH renderings, because which one it gets
    # depends on whether the vision util is installed — and in neither case may the run be left
    # with an unactionable "could not" when the actionable fact is a size it can reduce.
    rendered = obs_files.format_files(obs, "view_image")
    assert "2001" in rendered, (
        "the file's measured size never reached the model, so the run cannot know to downscale "
        f"it: {rendered}")
    assert "can't view it directly" not in rendered, (
        "a multimodal run told its model cannot view images is told something false — the file "
        f"was too big, which is actionable: {rendered}")

    # (a) the description SUCCEEDED → the line says it was too large to show directly
    described = {**obs, "files": [{**f, "error": None, "text": "DESCRIBED",
                                   "via": "vision-util"}]}
    described["files"][0].pop("error")
    text_ok = obs_files.format_files(described, "view_image")
    assert "TOO LARGE to show you directly" in text_ok, text_ok
    assert "2001" in text_ok and "2000" in text_ok, text_ok

    # (b) the description FAILED too → the failure still reads as a failure (R1493) and the
    # measurement rides along, which is the half F623 would otherwise lose one layer further in
    failed = {**obs, "files": [{"path": "huge.png", "via": "vision-util",
                                "error": "the `vision` util is not installed",
                                "oversize": f["oversize"]}]}
    text_bad = obs_files.format_files(failed, "view_image")
    assert "NOT SHOWN" in text_bad and "describe nothing from it" in text_bad, text_bad
    assert "2001" in text_bad, (
        "a file too large to send AND impossible to describe left the run with no measurement "
        f"at all — the F623 dead end, one layer in: {text_bad}")


def test_a_within_limit_image_still_rides_the_message_natively(tmp_path):
    """The pre-flight must not cost the ordinary case. A normal screenshot is still attached."""
    endpoint = SimpleNamespace(multimodal=True,
                               supports_media=lambda _m, *, multimodal: True)
    ctx = _ctx(tmp_path, endpoint)
    _png(tmp_path / "fine.png", 1600, 900)
    obs = mediaops.do_view_image({"kind": "view_image", "path": "fine.png"}, ctx)
    assert obs["files"][0].get("native") is True
    assert obs["media"] and obs["media"][0]["media_type"] == "image/png"
    assert "oversize" not in obs["files"][0]


def test_auto_attach_uses_the_same_pre_flight_as_view_image(tmp_path, monkeypatch):
    """The two sites carried duplicate eligibility conditions, so a bound added to one would have
    been missing from the other — which is how a conversation's auto-attached screenshot would
    have 400'd a message `view_image` had learned to protect."""
    endpoint = SimpleNamespace(multimodal=True,
                               supports_media=lambda _m, *, multimodal: True)
    ctx = _ctx(tmp_path, endpoint)
    monkeypatch.setattr(ctx.registry, "for_model",
                        lambda _role, _models: (endpoint, SimpleNamespace(multimodal=True)),
                        raising=False)
    _png(tmp_path / "ok.png", 1024, 768)
    _png(tmp_path / "giant.png", 9000, 9000)
    got = mediaops.media_from_paths(ctx, ["ok.png", "giant.png"])
    assert [_Path(m["path"]).name for m in got] == ["ok.png"], got
