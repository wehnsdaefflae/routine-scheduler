"""Refuse `util vision <local file>` when THIS run can simply look at the file (R2249 bug 1).

A multimodal run that calls the `vision` util pays for nothing it gets: the util needs
`OPENROUTER_VISION_KEY`, so the call opens a credential-exposure question the user has to
think about; it uploads the file to a third-party provider; and it hands back a transcription
where `view_image` would have handed back the document. The engine knows both facts it needs
to say so -- the run's own model and whether the file is viewable -- before the gate ever asks.

So the refusal replaces the question. R2249's second-order cost is the reason it must:
declining an exposure is scoped to the WHOLE run, so one needless prompt closes off every
legitimate `vision` call for the rest of that run.

The predicate is deliberately NOT a new one. `mediaops` decides natively-viewable as
`oversize_reason(path, mime) is None and endpoint.supports_media(mime, multimodal=...)`,
and this module asks the same pair through `mediaops.natively_viewable`, so a bound added
there is never missing here (the lesson F623 left: two predicates for one question drift).
Everything that is NOT that case falls through untouched -- a text-only run, an endpoint that
cannot carry the type, an oversized file, a URL, a `--selftest`, a verb that reads no file.
Those are exactly the calls `vision` exists for.
"""
from __future__ import annotations

from .mediaops import natively_viewable
from .run_context import RunContext

#: The util this module speaks for. One name, because the refusal's whole justification is
#: that `view_image` does this util's job better for this run.
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
