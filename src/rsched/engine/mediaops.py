"""SEEING (or HEARING) a file — images, PDFs, audio and video, natively or via the vision util.

A multimodal model is handed the bytes; everything else gets a described transcription from
the vision util. Which types may be handed over is `endpoints/media_types.py`'s allowlist, and
whether THIS run's model takes a modality is its resolved bits ANDed with the adapter's wire.
Audio and video have no fallback (the vision util describes pictures), so a model not
configured for them is told so, with the native size bound.
"""

from __future__ import annotations

import json
from pathlib import Path

from .. import sandbox, utils_lib, utils_run
from ..endpoints.media_types import (
    NATIVE_MEDIA_LIMIT_NOTE,
    guess_media_type,
    modality_of,
    model_modalities,
    oversize_reason,
    read_media_b64,
)
from ..rundepth import read_gate as _runs_read_gate
from .fileops import UTIL_DEFAULT_TIMEOUT_S, _memory_gate
from .observations import truncate
from .run_context import RunContext, resolve_action_path, work_dir

VISION_UTIL = "vision"
VIEW_DEFAULT_PROMPT = ("Describe this file in full detail — transcribe any text verbatim and "
                       "note structure, data, and notable visual elements.")


def vision_describe(ctx: RunContext, abspath: str, prompt: str) -> str:
    """Run the `vision` util on one file and return its text (or an 'error: …' string). The
    single fallback used both by do_view_image and the loop's runtime net when the main
    endpoint can't take a file natively; the util bills its own key, out of the run's usage.

    The call is the ENGINE's, so no exposure ask precedes it, but its environment is the
    run's: the routine's scoped key shadows the central one, a not-granted optional secret is
    withheld, and a DECLINED secret REFUSES the call (D213) — the engine
    refuses for calls IT chose and withholds for calls the RUN chose
    (`secretgate.withheld_for_util`), so a decline never silently degrades work the run did
    not ask for.
    """
    from .exec_env import _extra_secrets
    from .secretgate import declined_secrets, withheld_optional_secrets

    home = ctx.server.libraries_home
    if not utils_lib.exists(home, VISION_UTIL):
        return "error: the `vision` util is not installed, so this file cannot be described"
    if declined := declined_secrets(ctx, VISION_UTIL):
        # REFUSE, do not withhold (see docstring). A decline enumerates nothing — a count,
        # never the names it protected.
        return (f"error: the user declined exposing {len(declined)} secret"
                f"{'s' if len(declined) != 1 else ''} the vision fallback needs to this "
                "routine, so this file cannot be described here. The engine chose this call, "
                "so it is refused rather than run without the credential — a util the run "
                "calls itself runs with the declined secret withheld instead. The mapping is "
                "editable on the routine page (secret exposure)")
    args = [abspath, "--prompt", prompt or VIEW_DEFAULT_PROMPT, "--json"]
    code, out, err = utils_run.run_util(
        home, VISION_UTIL, args, timeout=UTIL_DEFAULT_TIMEOUT_S,
        policy=sandbox.policy_for_ctx(ctx), extra_secrets=_extra_secrets(ctx),
        withhold_secrets=set(withheld_optional_secrets(ctx, VISION_UTIL)),
        cwd=work_dir(ctx), aborted=ctx.aborted)
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

def _resolve_for_view(ctx: RunContext, rel_path: str) -> tuple[Path | None, str]:
    """`(path, "")` for a file this run may view, or `(None, why not)` — the gating half of
    `_view_one`, separated from the routing half so each reads as one job.
    """
    try:
        path = resolve_action_path(ctx, rel_path)
        if err := _memory_gate(ctx, path) or _runs_read_gate(ctx, path):
            return None, err
        if not path.is_file():
            return None, ("file does not exist — read_file its parent directory (a directory "
                          "reads as its listing) to see what is actually there")
    except (OSError, PermissionError) as exc:
        return None, str(exc)
    return path, ""


def _timebased_refusal(rel_path: str, mime: str, *, carried: bool,
                       too_big: str | None) -> dict | None:
    """The observation for an AUDIO or VIDEO file that cannot ride natively — or None.

    No vision-util fallback exists for these, so the refusal names which of the two fixable
    causes happened: the model/wire does not carry the modality, or the file is over the
    native cap.
    """
    modality = modality_of(mime)
    if modality not in ("audio", "video"):
        return None
    if not carried:
        return {"path": rel_path,
                "error": f"this run's model is not configured for {modality} input ({mime}) — "
                         f"turn the modality on for the model in the catalog and use an "
                         f"endpoint whose wire carries it, or transcribe/extract the file "
                         f"first (the `deepgram` or `video-extract` util). "
                         f"{NATIVE_MEDIA_LIMIT_NOTE}"}
    if too_big:
        return {"path": rel_path,
                "error": f"too large to attach natively: {too_big}"}
    return None


def _view_one(rel_path: str, prompt: str, endpoint, ctx: RunContext, bits: dict) -> dict:
    """Route one file: native (return a media entry for the endpoint to see) when the main
    MODEL carries that modality, the endpoint supports the type, and it's within the native
    size cap, else the vision util.

    `bits` is the resolved model's modality permissions as `supports_media` takes them
    (`media_types.model_modalities`) — `multimodal` plus the per-model `audio`/`video` bits.
    """
    path, gate_error = _resolve_for_view(ctx, rel_path)
    if path is None:
        return {"path": rel_path, "error": gate_error}
    mime = guess_media_type(path)
    if mime is None:
        return {"path": rel_path,
                "error": "not a media type a model can be shown (png/jpeg/webp/gif/pdf, or "
                         "audio/video on a model configured for it) — read text files with "
                         "read_file instead"}
    ctx.seen_paths.add(str(path))   # viewed = seen: grounds a later overwrite of this file
    # PRE-FLIGHT: a file the provider would refuse (bytes or per-side pixels) goes to the vision
    # util instead, and the observation carries the reason so the run can act on it.
    too_big = oversize_reason(path, mime) if endpoint is not None else None
    carried = endpoint is not None and endpoint.supports_media(mime, **bits)
    native = carried and too_big is None
    if refusal := _timebased_refusal(rel_path, mime, carried=carried, too_big=too_big):
        return refusal
    if too_big and carried:
        viewed = _view_via_vision(rel_path, str(path), prompt, ctx)
        return {**viewed, "oversize": too_big}
    if native:
        # Capture the BYTES now, not just the path: the entry is re-sent on every later turn,
        # and a file rewritten or removed since would break an attachment already promised.
        try:
            data = read_media_b64(path)
        except OSError as exc:      # vanished between the stat and the read — say so now
            return {"path": rel_path, "error": f"could not be read: {exc}"}
        return {"path": rel_path, "media_type": mime, "native": True, "abspath": str(path),
                "b64": data}
    return _view_via_vision(rel_path, str(path), prompt, ctx)

def without_bytes(obs: dict) -> dict:
    """The observation as the TRANSCRIPT should keep it: every `media` entry minus its `b64`.

    Nothing reads the bytes back (`history.replay_messages` rebuilds observations text-only),
    and kept verbatim they push a transcript past `fileops.READ_MAX_BYTES`. The live message
    keeps them; the record keeps the path and the media type.
    """
    media = obs.get("media")
    if not media:
        return obs
    return {**obs, "media": [{k: v for k, v in item.items() if k != "b64"} for item in media]}


def natively_viewable(ctx: RunContext, rel: str) -> tuple[str, str] | None:
    """`(abspath, media_type)` if THIS run's main model can be shown `rel` directly, else None.

    The one spelling of that question, shared by conversation auto-attach, the `view_image`
    pre-flight and `visionsteer`'s refusal: multimodal main model, media type carried
    natively, file exists, inside the native size cap. None means the vision-util fallback is
    the real path — also for anything that is not a readable local file.
    """
    try:
        endpoint, ref = ctx.registry.for_model("main", ctx.routine.models, ctx.routine.efforts)
    except Exception:
        # a run whose model cannot be resolved must never be told it can see the file
        return None
    if not ref.multimodal:
        return None            # the common text-only case, answered before touching the disk
    try:
        path = resolve_action_path(ctx, rel)
    except (OSError, PermissionError, ValueError):
        return None
    mime = guess_media_type(path)
    if not mime or not path.is_file() or oversize_reason(path, mime) is not None:
        return None
    if not endpoint.supports_media(mime, **model_modalities(ref)):
        return None
    return str(path), mime


def media_from_paths(ctx: RunContext, rels: list[str]) -> list[dict]:
    """`media` entries (path + media_type) for the image/PDF attachments among `rels` that
    the main endpoint can show natively — conversation auto-attach. Unsupported files (wrong
    type, too big, or a text-only endpoint) are skipped: the model can still view_image them,
    which then routes through the vision util.
    """
    # the SAME predicate the view_image pre-flight and the `vision` refusal use
    seen = (natively_viewable(ctx, str(rel)) for rel in rels)
    return [{"path": s[0], "media_type": s[1]} for s in seen if s is not None]


def do_view_image(action: dict, ctx: RunContext) -> dict:
    """Let the orchestrator SEE (or HEAR) a file: natively when the main MODEL carries that
    modality (the file rides the next message as a `media` block), else via the vision util
    (text back now) for a picture, or a refusal naming the bound for audio/video. Path
    resolution and gating mirror read_file.
    """
    prompt = str(action.get("prompt") or "")
    try:
        endpoint, ref = ctx.registry.for_model("main", ctx.routine.models, ctx.routine.efforts)
    except Exception:
        endpoint, ref = None, None
    bits = model_modalities(ref) if ref else {"multimodal": False, "audio": False,
                                              "video": False}
    raw = action.get("paths") or ([action["path"]] if action.get("path") else [])
    files = [_view_one(str(p), prompt, endpoint, ctx, bits) for p in raw]
    media = [{"path": f.pop("abspath"), "media_type": f["media_type"],
              "b64": f.pop("b64")}
             for f in files if f.get("native")]
    obs = {"kind": "view_image", "files": files}
    if media:
        obs["media"] = media   # the loop attaches this to the observation's user message
    return obs
