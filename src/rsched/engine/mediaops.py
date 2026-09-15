"""SEEING a file — images and PDFs, natively or through the vision util.

Split out of `fileops.py` (F393). Reading text is one job; getting a picture in front of a model
is another, and it has a fallback path that text reads do not: a multimodal model is handed the
bytes, and everything else gets a described transcription from the vision util. The run's prose
names the CAPABILITY, never the util, so the fallback can change without the recipe changing.
"""

from __future__ import annotations

import json

from .. import sandbox, utils_lib, utils_run
from ..endpoints.base import NATIVE_MEDIA_MAX_BYTES, guess_media_type, read_media_b64
from ..paths import resolve_rel
from .fileops import UTIL_DEFAULT_TIMEOUT_S, VIEW_DEFAULT_PROMPT, VISION_UTIL, _runs_read_gate
from .observations import truncate
from .run_context import RunContext


def vision_describe(ctx: RunContext, abspath: str, prompt: str) -> str:
    """Run the `vision` util on one file and return its text (or an 'error: …' string). The
    single fallback used both by do_view_image and the loop's runtime net when the main
    endpoint can't take a file natively; the util bills its own key, out of the run's usage.
    """
    home = ctx.server.libraries_home
    if not utils_lib.exists(home, VISION_UTIL):
        return "error: the `vision` util is not installed, so this file cannot be described"
    args = [abspath, "--prompt", prompt or VIEW_DEFAULT_PROMPT, "--json"]
    code, out, err = utils_run.run_util(home, VISION_UTIL, args, timeout=UTIL_DEFAULT_TIMEOUT_S,
                                        policy=sandbox.policy_for_ctx(ctx),
                                        cwd=ctx.routine.dir)
    if code != 0:
        return f"error: vision util failed (exit {code}): {(err or out or '').strip()[:800]}"
    try:
        return json.loads(out).get("text") or out
    except (json.JSONDecodeError, AttributeError):
        return out

def _view_via_vision(rel_path: str, abspath: str, prompt: str, ctx: RunContext) -> dict:
    text = vision_describe(ctx, abspath, prompt)
    if text.startswith("error:"):
        return {"path": rel_path, "via": "vision-util", "error": text[len("error:"):].strip()}
    text, truncated = truncate(text)
    return {"path": rel_path, "via": "vision-util", "text": text, "truncated": truncated}

def _view_one(rel_path: str, prompt: str, endpoint, ctx: RunContext, multimodal: bool) -> dict:
    """Route one file: native (return a media entry for the endpoint to see) when the main
    MODEL is multimodal, the endpoint supports the type, and it's within the native size cap,
    else the vision util.
    """
    try:
        path = resolve_rel(ctx.routine.dir, rel_path, ctx.read_roots())
        if err := _runs_read_gate(ctx, path):
            return {"path": rel_path, "error": err}
        if not path.is_file():
            return {"path": rel_path, "error": "file does not exist"}
    except (OSError, PermissionError) as exc:
        return {"path": rel_path, "error": str(exc)}
    mime = guess_media_type(path)
    if mime is None:
        return {"path": rel_path, "error": "not a viewable image/PDF (png/jpeg/webp/gif/pdf) — "
                                           "read text files with read_file instead"}
    ctx.seen_paths.add(str(path))   # viewed = seen: grounds a later overwrite of this file
    native = (endpoint is not None and path.stat().st_size <= NATIVE_MEDIA_MAX_BYTES
              and endpoint.supports_media(mime, multimodal=multimodal))
    if native:
        # R1493: capture the BYTES here, not just the path. The media entry rides the
        # observation's message and stays in the conversation, so the endpoint re-renders it on
        # every later send — reading the path each time. A run that re-renders a check artifact
        # into a fixed filename (or cleans it up) then invalidates its own earlier attachment,
        # and the failure surfaces inside the endpoint, AFTER the observation already said
        # "shown to you below; look at it now". Reading once, at the one moment existence was
        # verified, makes the attachment immutable: what the model was told it would see is what
        # the provider receives, this turn and every later one.
        try:
            data = read_media_b64(path)
        except OSError as exc:      # vanished between the stat and the read — say so now
            return {"path": rel_path, "error": f"could not be read: {exc}"}
        return {"path": rel_path, "media_type": mime, "native": True, "abspath": str(path),
                "b64": data}
    return _view_via_vision(rel_path, str(path), prompt, ctx)

def media_from_paths(ctx: RunContext, rels: list[str]) -> list[dict]:
    """`media` entries (path + media_type) for the image/PDF attachments among `rels` that
    the main endpoint can show natively — conversation auto-attach. Unsupported files (wrong
    type, too big, or a text-only endpoint) are skipped: the model can still view_image them,
    which then routes through the vision util.
    """
    try:
        endpoint, ref = ctx.registry.for_model("main", ctx.routine.models)
    except Exception:
        return []
    out: list[dict] = []
    for rel in rels:
        try:
            path = resolve_rel(ctx.routine.dir, str(rel), ctx.read_roots())
        except (OSError, PermissionError):
            continue
        mime = guess_media_type(path)
        if (mime and path.is_file() and path.stat().st_size <= NATIVE_MEDIA_MAX_BYTES
                and endpoint.supports_media(mime, multimodal=ref.multimodal)):
            out.append({"path": str(path), "media_type": mime})
    return out

def do_view_image(action: dict, ctx: RunContext) -> dict:
    """Let the orchestrator SEE an image/PDF: natively when the main MODEL is multimodal
    (the file rides the next message as a `media` block), else via the vision util (text back
    now). Path resolution and gating mirror read_file.
    """
    prompt = str(action.get("prompt") or "")
    try:
        endpoint, ref = ctx.registry.for_model("main", ctx.routine.models)
    except Exception:
        endpoint, ref = None, None
    multimodal = bool(ref.multimodal) if ref else False
    raw = action.get("paths") or ([action["path"]] if action.get("path") else [])
    files = [_view_one(str(p), prompt, endpoint, ctx, multimodal) for p in raw]
    media = [{"path": f.pop("abspath"), "media_type": f["media_type"],
              "b64": f.pop("b64")}
             for f in files if f.get("native")]
    obs = {"kind": "view_image", "files": files}
    if media:
        obs["media"] = media   # the loop attaches this to the observation's user message
    return obs
