"""`mediaops.natively_viewable` — can THIS run's main model be shown a file directly? The one
predicate conversation auto-attach and the `view_image` pre-flight share. No network.
"""

from __future__ import annotations

from types import SimpleNamespace

from rsched.endpoints.media_types import supports_media_type
from rsched.engine import mediaops


class _Endpoint:
    """The house double from tests/test_view_image.py: one endpoint serves many models, so the
    model's own `multimodal` flag is what `supports_media` is asked about."""

    def __init__(self, multimodal, pdf=True):
        self.multimodal = multimodal
        self.pdf = pdf

    def supports_media(self, mime, *, multimodal, audio=False, video=False):
        return supports_media_type(mime, multimodal=multimodal, pdf=self.pdf, audio=audio,
                                   video=video)


def _ctx(tmp_path, endpoint):
    routine = SimpleNamespace(dir=tmp_path, fs_read_roots=[], fs_write_roots=[], models={}, efforts={})
    ref = SimpleNamespace(multimodal=endpoint.multimodal, context_tokens=200_000) \
        if endpoint else None
    registry = SimpleNamespace(for_model=lambda k, m, e: (endpoint, ref)) if endpoint else None
    return SimpleNamespace(routine=routine, grants=None, depth=0,
                           root_run_dir=tmp_path / "runs" / "x",
                           read_roots=lambda: list(routine.fs_read_roots),
                           write_roots=lambda: list(routine.fs_write_roots),
                           server=SimpleNamespace(libraries_home=tmp_path / "utils",
                                                  routines_home=tmp_path / "routines"),
                           registry=registry,
                           seen_paths=set())


# --- the predicate, and the regression its extraction introduced -------------

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
