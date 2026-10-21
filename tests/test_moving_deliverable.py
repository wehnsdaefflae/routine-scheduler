"""A MOVING deliverable is judged by watching it — the rendered-output reminder now says so.

`interface-craft`'s assist fires when a run wrote something a person judges by LOOKING at it and
then never looked. Its predicate (`engine/assist_predicates._rendered_output_unseen`) read a
tuple of still surfaces — `.html`, `.htm`, `.css`, `.js`, `.svg`, `.pdf`, `.tex` — so a run whose
deliverable was an `.mp4`, a `.gif` or a `.webm` was never reminded of anything. The obligation is
identical and arguably stronger: an animation that renders to a black frame, drops its last
second, or plays at the wrong rate looks perfect in source and wrong on screen, and only watching
it says which.

Two routines on this instance ship exactly that kind of file (`eye-stabilize-folder`,
`funscript-trainer`), and thirteen hold `interface-craft`.

`video-extract` joins `_LOOK_UTILS` for the same reason `browser-session` is in it: it is how a
run actually gets frames of its own video in front of a model. A still image a run wrote is
already covered by `view_image`; the moving case had no path at all.

WHY THIS FILE AND NOT `test_assists.py`: that file is at its size ratchet (857 lines), and a
ratcheted file may only shrink — the ratchet's own message says "put the addition in its own
file". A new test file is unconstrained, which is also where this much explanation belongs.

Found while researching how an agent produces motion graphics at all (operator request,
2026-10-21): every pipeline — Remotion, Manim, a frame sequence through ffmpeg — ends in a video
file, and nothing in the engine asked whether anybody had seen it move.
"""

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
