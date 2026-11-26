"""The MEDIA fallback: a prompt carrying images served by a model that cannot see one.

Split out of `engine/window.py` (D315), which named this as one of its responsibilities and
had no room left under the 350-line house bar. window.py keeps what makes a turn FIT — the
context window, calibration, compaction; this module owns the one other sanctioned rewrite of
the message list: replacing image `media` with vision-util TEXT.

Two callers, ONE conversion (`_convert_media_to_text`) and ONE transcript record
(`_record_media_conversion`):

* `apply_media_fallback` — AFTER a provider refused the images (`engine/degrade.py`), so the
  retry is text-only and the content still reaches the model.
* `media_preflight` — BEFORE a request whose serving model is not multimodal
  (`engine/turntakeover.adopt_takeover`, the uncensored takeover), so the round-trip that
  would be refused is never spent.

Rewriting the message list breaks the prompt-CACHING contract deliberately; see window.py's
docstring for why that is sanctioned here.

net: fs: none
"""

from __future__ import annotations

from pathlib import Path

from ..endpoints.base import EndpointError
from . import mediaops

#: How much of the PROVIDER'S refusal the media-fallback error event keeps: enough to name the
#: limit it refused on. Written at most once per run, on a path that has already failed.
MEDIA_ERROR_CHARS = 1200


def _convert_media_to_text(loop) -> tuple[int, int]:
    """Convert EVERY media-bearing message to vision-util text in place and drop the
    attachments; return (messages converted, files described).

    EVERY message, not just the tail: a later message may follow the media-bearing one, and an
    image anywhere is a liability to a model that cannot see one. A deliberate, sanctioned
    break in the prompt-caching contract.

    The conversion itself, with no policy in it — both entry points below share this one
    implementation.
    """
    converted = 0
    files = 0
    for msg in loop.messages:
        media = msg.get("media")
        if not media:
            continue
        notes = []
        for item in media:
            desc = mediaops.vision_describe(loop.ctx, item["path"], "")
            notes.append(f"[{Path(item['path']).name}: this run's model could not display it — "
                         f"description from the vision util]\n{desc}")
        msg.pop("media", None)
        msg["content"] = msg["content"] + "\n\n" + "\n\n".join(notes)
        converted += 1
        files += len(media)
    return converted, files


def _record_media_conversion(loop, message: str) -> None:
    """The ONE transcript record of a media→text conversion, on the `error` event with
    `where: "media"` that the console and the CLI renderer already render.

    Deliberately NOT a new event type: `transcript.EVENT_TYPES` is a contract both readers and
    the console must be extended for together, and nothing counts a `where: "media"` error as a
    failure (the friction counters read `where == "schema"` only), so a conversion that merely
    saved a round-trip cannot read as a broken turn.

    The DESCRIPTIONS the model then reasons from go on the record either way.
    """
    loop.ctx.transcript.event("error", {"where": "media", "message": message,
        "descriptions": [m["content"][-2000:] for m in loop.messages
                         if "description from the vision util]" in m["content"]][-3:]})


def apply_media_fallback(loop, exc: EndpointError) -> bool:
    """The POST-error half: the endpoint failed on a turn whose prompt carries image `media`
    (it rejected the file, or serves no model that can see one). Convert the media to
    vision-util text so the retried completion is text-only and the model still gets the
    content. False when the prompt carries no media at all — then the failure is a genuine
    endpoint error that must propagate.

    The provider's own message is kept long enough to name the bound it refused on
    (MEDIA_ERROR_CHARS).
    """
    converted, files = _convert_media_to_text(loop)
    if not converted:
        return False
    _record_media_conversion(
        loop, f"this run's model could not show {files} file(s) in {converted} message(s) "
              f"({str(exc)[:MEDIA_ERROR_CHARS]}); fell back to the vision util")
    return True


def media_preflight(loop, ref) -> bool:
    """The PRE-request half (D315): the model about to serve THIS turn cannot see an image, so
    convert the media NOW instead of spending a provider round-trip to be told so.

    False — nothing done — when `ref` is multimodal, or when the prompt carries no media at
    all. True when the prompt was converted, and then the transcript says WHY before the
    request rather than after a 400: the model named here is reading a description.

    The uncensored takeover is the caller (`engine/turntakeover.adopt_takeover`): its model is
    bound non-multimodal on every routine here, while the refused turn may well carry media.
    `apply_media_fallback` recovered that case correctly, but only after the provider had
    already answered "does not support image inputs".
    """
    if ref.multimodal:
        return False
    converted, files = _convert_media_to_text(loop)
    if not converted:
        return False
    _record_media_conversion(
        loop, f"{ref.name or ref.model} takes this turn and cannot see an image, so "
              f"{files} file(s) in {converted} message(s) were converted to vision-util text "
              f"BEFORE the request; it is reading a description, and no provider round-trip "
              f"is spent being refused")
    return True
