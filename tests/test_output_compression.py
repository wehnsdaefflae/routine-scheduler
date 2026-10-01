"""Compression must preserve evidence, replay and the uncompressed baseline."""

import json

import pytest

from rsched.config import load_routine
from rsched.engine import output_compression as compression
from rsched.engine import outputs
from rsched.engine.executor import dispatch
from rsched.engine.history import replay_messages
from rsched.engine.observations import format_observation
from test_util_outputs import _ctx

DATA = json.dumps([{"id": i, "status": "ok", "service": "scheduler"} for i in range(150)])


@pytest.fixture
def ctx(make_routine):
    return _ctx(make_routine)


def _baseline(ctx, monkeypatch, text: str, err: str) -> dict:
    """The observation with no encoding applied — the existing capped output and pointer."""
    with monkeypatch.context() as m:
        m.setattr(compression, "_compress", lambda *_: None)
        obs = compression.command_output(ctx, "sample", text, err, 0)
    return {k: v for k, v in obs.items() if k != "compression"}


@pytest.mark.parametrize(("original", "candidate"), [
    ('["first", {"critical": "middle"}, 42, null, "last"]', '["first", "last"]'),
    ('{"value": 1}', "compact representation"),
    ('{"value": 1}', "{invalid"),
    ('{"value": 1}', '{"value": 1.0}'),
    ('{"value": 1}', '{"value": true}'),
    ('{"value": 9007199254740993}', '{"value": 9007199254740992}'),
    ('{"value": 0.123456789012345678901}', '{"value": 0.123456789012345678902}'),
    ('{"value": -0.0}', '{"value": 0.0}'),
    ('{"value": 1, "value": 2}', '{"value": 2}'),
    ('{"value": 2}', '{"value": 1, "value": 2}'),
    ('{"value": NaN}', '{"value": NaN}'),
    ('{"value": Infinity}', '{"value": Infinity}'),
    ('{"value": -Infinity}', '{"value": -Infinity}'),
    ('{"value": 1e999}', '{"value": 2e999}'),
], ids=["middle-omission", "non-json", "invalid-json", "int-to-float", "int-to-bool",
        "large-int", "precise-decimal", "signed-zero", "duplicate-original",
        "duplicate-candidate", "nan", "infinity", "negative-infinity", "overflow"])
def test_rejects_unverified_json_without_changing_baseline(ctx, monkeypatch, original, candidate):
    # Padding makes each synthetic JSON eligible without obscuring the changed value.
    original = original + " " * 2500
    baseline = _baseline(ctx, monkeypatch, original, "warning")
    monkeypatch.setattr(compression.lossless, "encode_json", lambda *_: (candidate, False))
    obs = compression.command_output(ctx, "sample", original, "warning", 0)
    assert obs["compression"]["status"] == "fallback"
    assert {k: v for k, v in obs.items() if k != "compression"} == baseline


def test_original_recovery_survives_other_runs_and_replay(ctx, monkeypatch):
    monkeypatch.setattr(compression, "_compress", lambda *_: ("json", json.dumps(json.loads(DATA), separators=(",", ":"))))
    obs = {"kind": "util", "name": "sample", "exit": 0,
           **compression.command_output(ctx, "sample", DATA, "unchanged stderr", 0)}
    assert obs["compression"]["status"] == "applied"
    assert obs["stderr"] == "unchanged stderr"
    original = ctx.routine.dir / obs["full_output"]["stdout"]
    assert original.read_text() == DATA
    original_ts = ctx.run_ts
    for i in range(outputs.KEEP_RUNS + 1):
        ctx.run_ts = f"20260910-1200{i:02d}"
        outputs.spill(ctx, f"other-{i}", DATA, "", out_truncated=True, err_truncated=False)
    ctx.run_ts = original_ts
    outputs._prune(ctx.routine.dir / outputs.OUTPUTS_DIR)
    assert original.read_text() == DATA
    monkeypatch.setattr(compression, "_compress", lambda *_: pytest.fail("recompressed"))
    recovered = dispatch({"kind": "read_file", "path": str(original)}, ctx)
    assert DATA in format_observation(recovered)
    messages, _, _ = replay_messages([{"type": "observation", "payload": obs}])
    assert messages[0]["content"] == format_observation(obs)


@pytest.mark.parametrize("result", [DATA, "x" * 9_000])
def test_unusable_or_larger_result_falls_back(ctx, monkeypatch, result):
    monkeypatch.setattr(compression, "_compress", lambda *_: ("json", result))
    obs = compression.command_output(ctx, "sample", DATA, "", 0)
    assert obs["compression"]["status"] in {"fallback", "unchanged"}
    assert obs["stdout"] == compression.truncate(DATA, keep="head")[0]


@pytest.mark.parametrize("error", [ImportError("missing"), RuntimeError("secret text")])
def test_a_failed_compressor_is_visible_without_leaking(ctx, monkeypatch, error):
    def fail(*_):
        raise error
    monkeypatch.setattr(compression, "_compress", fail)
    obs = compression.command_output(ctx, "sample", DATA, "", 0)
    assert obs["compression"]["status"] == "fallback"
    assert "secret text" not in json.dumps(obs)


def test_original_save_failure_never_replaces_output(ctx, monkeypatch):
    monkeypatch.setattr(compression, "_compress", lambda *_: ("json", json.dumps(json.loads(DATA), separators=(",", ":"))))
    def fail(*_):
        raise OSError("no disk space")
    monkeypatch.setattr(compression, "atomic_write", fail)
    obs = compression.command_output(ctx, "sample", DATA, "", 0)
    assert obs["compression"]["status"] == "fallback"
    assert obs["stdout"] == compression.truncate(DATA, keep="head")[0]


# The shape the native crusher silently truncated to `max_items_after_crush`
# (headroomlabs-ai/headroom#3625) — the reason JSON is stdlib-minified here. Sized to
# minify UNDER the observation cap: a payload whose minified form still exceeds it keeps
# the capped head and its pointer, which is the honest outcome, not this test's subject.
HETEROGENEOUS = json.dumps(["first", {"critical": "middle", "n": 1.0}, None, True,
                            *[f"unique dependency {i} >= {i}.0" for i in range(150)],
                            "last"], indent=2)


@pytest.mark.parametrize("text", [DATA, HETEROGENEOUS], ids=["uniform", "heterogeneous"])
def test_json_is_encoded_losslessly(ctx, text):
    """JSON is minified and a uniform array of flat objects is written as a table — the
    preview must decode back to the ORIGINAL value, the middle included.
    """
    from rsched.engine import lossless
    obs = compression.command_output(ctx, "sample", text, "", 0)
    assert obs["compression"]["status"] == "applied"
    assert "minified JSON" in obs["stdout"] and "nothing removed" in obs["stdout"]
    encoded = obs["stdout"].split("\n", 1)[1]
    tabulated = obs["compression"]["kind"] == "json-table"
    assert lossless.decode_json(encoded, tabulated) == json.loads(text)
    assert tabulated == (text == DATA)


def test_minified_json_keeps_the_verification_gate(ctx, monkeypatch):
    """Minification is faithful by construction, so the verifier is now an assertion
    rather than a safety net — it must still refuse a candidate that is not.
    """
    monkeypatch.setattr(compression.lossless, "encode_json",
                        lambda *_: ('{"value": 1.0}', False))
    obs = compression.command_output(ctx, "sample", '{"value": 1}' + " " * 2500, "", 0)
    assert obs["compression"]["status"] == "fallback"


@pytest.mark.parametrize(("text", "code"), [(DATA, 1), ("short", 0)])
def test_ineligible_output_is_never_compressed(ctx, monkeypatch, text, code):
    monkeypatch.setattr(compression, "_compress", lambda *_: pytest.fail("must bypass"))
    compression.command_output(ctx, "sample", text, "exact", code)


def test_prose_is_never_re_encoded(ctx):
    prose = "ordinary prose " * 300
    obs = compression.command_output(ctx, "sample", prose, "", 0)
    assert obs["compression"]["status"] == "skipped"
    assert obs["stdout"] == compression.truncate(prose, keep="head")[0]


def test_every_outcome_is_tallied_on_the_run(ctx, monkeypatch):
    """The run carries ONE tally — what the durable usage record hands the Stats tab's
    per-routine roll-up. A saving counts for an APPLIED preview only; the time counts for
    every outcome, because a rejected compression cost the run exactly what a kept one did.
    """
    monkeypatch.setattr(compression, "_compress",
                        lambda *_: ("json", json.dumps(json.loads(DATA), separators=(",", ":"))))
    applied = compression.command_output(ctx, "sample", DATA, "", 0)
    assert applied["compression"]["status"] == "applied"
    monkeypatch.undo()
    monkeypatch.setattr(compression.lossless, "encode_json", lambda *_: ('["cut"]', False))
    assert compression.command_output(ctx, "sample", DATA, "", 0)["compression"]["status"] == "fallback"
    compression.command_output(ctx, "sample", "short", "", 0)              # ineligible
    tally = ctx.compression_stats
    assert tally["applied"] == 1
    assert tally["fallback"] == 1
    assert tally["skipped"] == 1
    assert tally["tokens_saved"] == applied["compression"]["estimated_tokens_saved"] > 0
    assert tally["ms"] > 0


def test_compression_is_engine_behaviour_not_a_setting(make_routine):
    """No routine chooses it: an encoding is used only when its exact inverse gives back the
    output and the result is smaller, so there is nothing to decide per routine."""
    cfg, _ = load_routine(make_routine())
    assert not hasattr(cfg, "output_compression")


def test_log_shaped_output_keeps_the_capped_head_and_its_pointer(ctx):
    """No lossless encoding fits a log (it pulled 28 packages into the engine image once, for
    0.03% of the fleet's input tokens): the observation keeps the capped head, the spill
    pointer still carries the rest.
    """
    logs = "\n".join(f"INFO heartbeat healthy worker {i % 3}" for i in range(500))
    obs = compression.command_output(ctx, "logs", logs, "", 0)
    assert obs["compression"]["status"] == "skipped"
    assert obs["stdout"] == compression.truncate(logs, keep="head")[0]


GREP = "\n".join(f"src/rsched/{f}.py:{n}:    value = compute({n})"
                 for f in ("alpha", "beta") for n in range(40))


def test_grep_hits_are_grouped_under_their_file_and_decode_exactly(ctx):
    from rsched.engine import lossless
    obs = compression.command_output(ctx, "code-search", GREP, "", 0)
    assert obs["compression"]["status"] == "applied" and obs["compression"]["kind"] == "grep"
    body = obs["stdout"].split("\n", 1)[1]
    assert body.count("src/rsched/alpha.py") == 1
    assert lossless.decode_grep(body) == GREP


@pytest.mark.parametrize("text", [
    GREP + "\n--\nnot a hit",
    "a.py:1:x\n" * 3,
    "\n".join(f"dir/sub/{i}.txt" for i in range(30)),
    "\n".join(f"/abs/path/{i % 3}/file {i}" for i in range(30)),
])
def test_every_encoding_round_trips(text):
    from rsched.engine import lossless
    for encode, decode in ((lossless.encode_grep, lossless.decode_grep),
                           (lossless.encode_paths, lossless.decode_paths)):
        encoded = encode(text)
        if encoded is not None:
            assert decode(encoded) == text


def test_a_second_original_under_the_same_turn_keeps_the_first(ctx, monkeypatch):
    """Same turn number, same command name — a slash command right after the model's own
    call — must not overwrite the original the earlier pointer names."""
    minify = json.dumps(json.loads(DATA), separators=(",", ":"))
    monkeypatch.setattr(compression, "_compress", lambda *_: ("json", minify))
    first = compression.command_output(ctx, "sample", DATA, "", 0)["full_output"]["stdout"]
    second = compression.command_output(ctx, "sample", DATA + " ", "", 0)["full_output"]["stdout"]
    assert first != second and second.endswith("t7-sample-2.out")
    assert (ctx.routine.dir / first).read_text() == DATA


def test_child_original_is_engine_owned(ctx, monkeypatch):
    from rsched.grantpolicy import GrantPolicy

    ctx.run_dir = ctx.run_dir / "sub" / "1"
    ctx.run_dir.mkdir(parents=True)
    ctx.routine = ctx.routine.model_copy(update={"dir": ctx.run_dir})
    ctx.depth = 1
    ctx.grants = GrantPolicy()
    monkeypatch.setattr(compression, "_compress", lambda *_: ("json", json.dumps(json.loads(DATA), separators=(",", ":"))))
    obs = compression.command_output(ctx, "sample", DATA, "", 0)
    path = obs["full_output"]["stdout"]
    result = dispatch({"kind": "write_file", "path": path, "content": "forged"}, ctx)
    assert "engine-owned" in result["error"]
    read = dispatch({"kind": "read_file", "path": path}, ctx)
    assert DATA in format_observation(read)
