"""Capture loss remains parseable and distinct from observation preview truncation."""
import io
import json

import pytest

from rsched.captured_output import read_capped
from rsched.engine import outputs
from rsched.engine.output_compression import command_output
from test_util_outputs import _ctx


@pytest.mark.parametrize("size", [999, 1000])
def test_under_limit_output_is_unchanged(size):
    text = "x" * size
    result = read_capped(io.StringIO(text), 1000)
    assert result == text
    assert not result.capture_truncated


@pytest.mark.parametrize("text", ["x" * 1001, json.dumps(["word"] * 1000),
                                  '\\"\n😀' * 1000])
@pytest.mark.parametrize("diagnostic", ["", "process group killed after timeout"])
def test_overflow_envelope_is_parseable_and_serialized_size_bounded(text, diagnostic):
    result = read_capped(io.StringIO(text), 1000, diagnostic=diagnostic)
    value = json.loads(result)
    assert result.capture_truncated
    assert value["complete"] is False
    assert value["capture_truncated"] is True
    assert value["diagnostic"] == diagnostic
    assert text.startswith(value["preview"])
    assert len(result) <= 1000


def test_spill_does_not_claim_lost_original_is_complete(make_routine):
    ctx = _ctx(make_routine)
    ctx.routine.output_compression = "compress"
    result = read_capped(io.StringIO("x" * 30001), 30000)
    obs = command_output(ctx, "fixture", result, result, 0)
    assert obs["capture_truncated"] == {"stdout": True, "stderr": True}
    assert "compression" not in obs
    pointer = obs["full_output"]
    assert pointer["stdout_capture_truncated"]
    assert "incomplete capture envelope" in outputs.pointer_line(pointer)
    stored = (ctx.routine.dir / pointer["stdout"]).read_text()
    assert json.loads(stored)["complete"] is False
