"""R2249 bug 1, the prompt-surface half: the util catalog tells a MULTIMODAL run that `vision`
is for a text-only one, before any call is made. The engine's refusal (engine/visionsteer.py)
is what enforces it; this is the same fact stated where it costs no turn.

Kept beside the other catalog assertions in spirit but in its own file, because
tests/test_composer.py is already 1000+ lines and the house size bar applies to tests too.
"""

from __future__ import annotations

from types import SimpleNamespace

from helpers import run_context, server_config
from rsched.engine.capabilities import capabilities_digest
from rsched.engine.run_context import RunContext


def _ctx(make_routine, tmp_path, **kwargs) -> RunContext:
    return run_context(make_routine(**kwargs), "20260708-070000",
                       server=server_config(libraries_home=tmp_path / "libraries"))


def _with_vision(ctx):
    d = ctx.server.libraries_home / "utils" / "vision"
    d.mkdir(parents=True)
    (d / "main.py").write_text(
        '"""vision — see images/PDFs for a text-only model: forward them + a question to a '
        'cloud vision model.\n\nusage: gu vision FILE --question Q\ntags: vision, media\n"""\n',
        encoding="utf-8")
    return ctx


def _model(multimodal):
    return SimpleNamespace(for_model=lambda *_a: (
        SimpleNamespace(),
        SimpleNamespace(endpoint="claude-proxy", model="claude-opus-5",
                        context_tokens=1_000_000, multimodal=multimodal)))


def test_a_multimodal_run_sees_vision_flagged_as_for_text_only_runs(make_routine, tmp_path):
    ctx = _with_vision(_ctx(make_routine, tmp_path, slug="capsvision"))
    ctx.registry = _model(True)
    text = capabilities_digest(ctx)
    assert "[for a text-only run" in text
    assert "view_image path=" in text
    # the util is still LISTED and still callable — the note is guidance, not a removal:
    # an oversized file or an endpoint without document support still needs it
    assert "vision — see images/PDFs" in text


def test_a_text_only_run_sees_the_bare_summary(make_routine, tmp_path):
    """The note must not appear where `vision` IS the right call, or it becomes noise that
    teaches a run to distrust the annotation."""
    ctx = _with_vision(_ctx(make_routine, tmp_path, slug="capsvisiontext"))
    ctx.registry = _model(False)
    text = capabilities_digest(ctx)
    assert "vision — see images/PDFs" in text
    assert "[for a text-only run" not in text


def test_an_unresolvable_model_does_not_claim_multimodality(make_routine, tmp_path):
    """The flag is read at the one place the main model is resolved. If that fails the digest
    already says the window is unknown; it must not also assert the run can see images."""
    ctx = _with_vision(_ctx(make_routine, tmp_path, slug="capsvisionnomodel"))

    def for_model(*_a):
        raise RuntimeError("endpoint 'claude-proxy' is unreachable")

    ctx.registry = SimpleNamespace(for_model=for_model)
    text = capabilities_digest(ctx)
    assert "could not be resolved" in text
    assert "[for a text-only run" not in text
