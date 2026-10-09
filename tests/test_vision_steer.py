"""R2249 bug 1: a multimodal run calling the `vision` util on a file it can simply look at is
REFUSED, in both dispatch paths, before any credential question is asked. No network.

The refusal must be exactly as narrow as the engine's own native-view predicate: everything
`vision` genuinely exists for — a text-only run, a media type the endpoint cannot carry, a file
over the native cap, a URL, a flag — has to pass through untouched. These tests pin both edges,
because a refusal that is too wide removes the fallback the engine depends on.
"""

from __future__ import annotations

from types import SimpleNamespace

from rsched.endpoints.base import oversize_reason, supports_media_type
from rsched.engine import actionroute, mediaops, visionsteer


class _Endpoint:
    """The house double from tests/test_view_image.py: one endpoint serves many models, so the
    model's own `multimodal` flag is what `supports_media` is asked about."""

    def __init__(self, multimodal, pdf=True):
        self.multimodal = multimodal
        self.pdf = pdf

    def supports_media(self, mime, *, multimodal):
        return supports_media_type(mime, multimodal=multimodal, pdf=self.pdf)


def _ctx(tmp_path, endpoint):
    routine = SimpleNamespace(dir=tmp_path, fs_read_roots=[], fs_write_roots=[], models={})
    ref = SimpleNamespace(multimodal=endpoint.multimodal, context_tokens=200_000) \
        if endpoint else None
    registry = SimpleNamespace(for_model=lambda k, m: (endpoint, ref)) if endpoint else None
    return SimpleNamespace(routine=routine, grants=None, depth=0,
                           root_run_dir=tmp_path / "runs" / "x",
                           read_roots=lambda: list(routine.fs_read_roots),
                           write_roots=lambda: list(routine.fs_write_roots),
                           server=SimpleNamespace(libraries_home=tmp_path / "utils",
                                                  routines_home=tmp_path / "routines"),
                           registry=registry,
                           seen_paths=set())


def _vision(*args, **extra):
    return {"say": "x", "kind": "util", "name": "vision", "args": list(args), **extra}


# --- the refusal itself ------------------------------------------------------

def test_a_multimodal_run_is_refused_and_told_the_call_that_works(tmp_path):
    """The whole point of R2249: the user was asked to decide an OPENROUTER_VISION_KEY exposure
    for a call that needed no credential, on a run whose model could see the file. The refusal
    replaces the question and names the action that does work."""
    (tmp_path / "shot.png").write_bytes(b"IMG")
    obs = visionsteer.refuse_if_viewable(_vision("shot.png", "--question", "what is this"),
                                         _ctx(tmp_path, _Endpoint(True)))
    assert obs is not None
    assert obs["refused"] is True and obs["name"] == "vision"
    # the suggested call must be COPYABLE: the path is quoted as the run wrote it, so
    # `view_image path=shot.png` works verbatim — a resolved absolute path would not be what
    # the run has in hand, and a run that has to re-derive the argument re-derives the mistake
    assert "`view_image path=shot.png`" in obs["error"]
    # it must say that nothing happened — a refusal read as a failed upload is worse than none
    assert "Nothing was sent" in obs["error"]
    assert "OPENROUTER_VISION_KEY" in obs["error"]


def test_a_pdf_is_refused_where_the_endpoint_carries_pdfs_and_allowed_where_it_does_not(tmp_path):
    """The grant document in R2249 was a PDF, so the PDF edge is the reported case. It also
    proves the refusal tracks the ENDPOINT's document support rather than just multimodality."""
    (tmp_path / "timesheet.pdf").write_bytes(b"%PDF-1.4 fake")
    refused = visionsteer.refuse_if_viewable(_vision("timesheet.pdf"),
                                             _ctx(tmp_path, _Endpoint(True, pdf=True)))
    assert refused is not None and "view_image" in refused["error"]
    # same model, an endpoint without native document support: vision IS the real path
    assert visionsteer.refuse_if_viewable(_vision("timesheet.pdf"),
                                          _ctx(tmp_path, _Endpoint(True, pdf=False))) is None


# --- everything vision exists for must pass through -------------------------

def test_a_text_only_run_is_never_refused(tmp_path):
    (tmp_path / "shot.png").write_bytes(b"IMG")
    assert visionsteer.refuse_if_viewable(_vision("shot.png"),
                                          _ctx(tmp_path, _Endpoint(False))) is None


def test_an_oversized_file_is_never_refused(tmp_path):
    """Over the native cap the engine's own pre-flight routes to the vision util, so a refusal
    there would leave the run with no way to see the file at all."""
    big = tmp_path / "huge.png"
    big.write_bytes(b"\0" * (9 * 1024 * 1024))
    assert oversize_reason(big, "image/png") is not None      # the premise of this test
    assert visionsteer.refuse_if_viewable(_vision("huge.png"),
                                          _ctx(tmp_path, _Endpoint(True))) is None


def test_a_url_a_flag_and_a_missing_path_are_never_refused(tmp_path):
    ctx = _ctx(tmp_path, _Endpoint(True))
    for args in (["https://example.org/x.png"], ["--selftest"], ["absent.png"], []):
        assert visionsteer.refuse_if_viewable(_vision(*args), ctx) is None, args


def test_another_util_is_untouched(tmp_path):
    (tmp_path / "shot.png").write_bytes(b"IMG")
    other = {"say": "x", "kind": "util", "name": "img-fetch", "args": ["shot.png"]}
    assert visionsteer.refuse_if_viewable(other, _ctx(tmp_path, _Endpoint(True))) is None


# --- both dispatch paths, because a flag must not walk around it -------------

def test_the_refusal_holds_in_the_foreground_and_in_the_background(tmp_path):
    """F633's lesson applied to this refusal: `util` is backgroundable, and a kind refused in
    one path and not the other is a refusal a flag walks around. Neither call may reach the
    concurrency cap or the secret gate, which is why `loop` is never touched here."""
    (tmp_path / "shot.png").write_bytes(b"IMG")
    ctx = _ctx(tmp_path, _Endpoint(True))
    fg = actionroute.dispatch_action(None, _vision("shot.png"), ctx)
    bg = actionroute.dispatch_action(None, _vision("shot.png", background=True), ctx)
    for obs in (fg, bg):
        assert obs.get("refused") is True and "view_image" in obs["error"]
        assert not obs.get("engine_error")      # a NameError here would read as a refusal


# --- the shared predicate, and the regression the extraction introduced ------

def test_the_auto_attach_scanner_survives_a_file_it_cannot_show(tmp_path):
    """`media_from_paths` folds the same predicate, which answers None for a file it cannot
    show. Unpacking that None raised TypeError for the WHOLE batch — one unsupported
    attachment would have cost every other attachment in the same message. Lint passed it."""
    (tmp_path / "ok.png").write_bytes(b"IMG")
    (tmp_path / "huge.png").write_bytes(b"\0" * (9 * 1024 * 1024))
    (tmp_path / "notes.txt").write_text("not an image", encoding="utf-8")
    out = mediaops.media_from_paths(_ctx(tmp_path, _Endpoint(True)),
                                    ["huge.png", "ok.png", "notes.txt", "absent.png"])
    assert out == [{"path": str(tmp_path / "ok.png"), "media_type": "image/png"}]


def test_the_predicate_answers_with_the_resolved_path_and_type(tmp_path):
    (tmp_path / "shot.png").write_bytes(b"IMG")
    seen = mediaops.natively_viewable(_ctx(tmp_path, _Endpoint(True)), "shot.png")
    assert seen == (str(tmp_path / "shot.png"), "image/png")
    assert mediaops.natively_viewable(_ctx(tmp_path, None), "shot.png") is None
