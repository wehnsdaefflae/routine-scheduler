"""A MOVING deliverable is judged by watching it — the rendered-output reminder now says so."""

from __future__ import annotations

from types import SimpleNamespace

from rsched.engine.assist_predicates import Situation, _rendered_output_unseen
from rsched.engine.compaction import turn_record


def _situation(*actions):
    records = [turn_record(n, a) for n, a in enumerate(actions, 1)]
    return Situation(loop=SimpleNamespace(ctx=None, turn_records=records))


def test_a_written_animation_with_no_look_is_flagged():
    for path in ("out/clip.mp4", "out/loop.gif", "out/scene.webm", "out/take.mov",
                 "out/sprite.apng", "out/anim.webp"):
        assert _rendered_output_unseen(_situation({"kind": "write_file", "path": path})), path


def test_watching_it_clears_the_flag():
    clip = {"kind": "write_file", "path": "out/clip.mp4"}
    assert not _rendered_output_unseen(
        _situation(clip, {"kind": "view_image", "path": "out/clip.mp4"}))
    assert not _rendered_output_unseen(
        _situation(clip, {"kind": "util", "name": "video-extract"}))


def test_a_render_script_is_not_a_look():
    """The negative case the predicate exists for: producing the artefact is not judging it."""
    clip = {"kind": "write_file", "path": "out/clip.mp4"}
    assert _rendered_output_unseen(_situation(clip, {"kind": "util", "name": "pytest-run"}))
    assert _rendered_output_unseen(_situation(clip, {"kind": "write_file",
                                                    "path": "render.py"}))


def test_the_still_surfaces_still_work():
    """An anti-drift pin: the extensions that were there before must not have been displaced."""
    page = {"kind": "write_file", "path": "site/index.html"}
    assert _rendered_output_unseen(_situation(page))
    assert not _rendered_output_unseen(_situation(page, {"kind": "util",
                                                        "name": "browser-session"}))
    assert not _rendered_output_unseen(_situation({"kind": "write_file",
                                                   "path": "notes.md"}))
