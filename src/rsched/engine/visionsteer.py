"""Refuse `util vision <local file>` when THIS run can simply look at the file (R2249).

For a multimodal run the `vision` util is all cost: it needs `OPENROUTER_VISION_KEY` (an
exposure question for the user, and declining one closes off `vision` for the whole run),
uploads the file to a third party, and returns a transcription where `view_image` returns the
document. So the refusal replaces the question.

The predicate is `mediaops.natively_viewable`, the same one `view_image` uses, so the two cannot
drift. Everything else falls through untouched — a text-only run, an endpoint that cannot carry
the type, an oversized file, a URL, a `--selftest`, a verb that reads no file.
"""
from __future__ import annotations

from .mediaops import natively_viewable
from .run_context import RunContext

#: The util this module speaks for.
VISION = "vision"


def refuse_if_viewable(action: dict, ctx: RunContext) -> dict | None:
    """The observation that replaces a needless `vision` call, or None to let it proceed.

    None for every call this run genuinely needs: a text-only model, a media type the
    endpoint cannot carry natively, a file over the native cap, a remote URL, or an
    invocation that opens no local file at all.
    """
    if str(action.get("name") or "") != VISION:
        return None
    viewable = [a for a in (action.get("args") or []) if natively_viewable(ctx, str(a))]
    if not viewable:
        return None
    shown = ", ".join(f"`{p}`" for p in viewable[:3])
    return {
        "kind": "util", "name": VISION, "refused": True,
        "error": f"This run's model is multimodal and can see {shown} directly: call "
                 f"`view_image path={viewable[0]}` (or `paths=[...]` for several) and the "
                 "file is shown to you on the next turn. The `vision` util is for a "
                 "text-only run: it needs OPENROUTER_VISION_KEY, uploads the file to a "
                 "third-party provider, and returns one model's description instead of the "
                 "document. Nothing was sent and no credential was requested.",
    }
