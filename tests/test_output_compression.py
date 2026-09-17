"""Compression must preserve evidence, replay and the uncompressed baseline."""

import importlib.util
import json
import os
import subprocess
import sys
import time

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
    ctx = _ctx(make_routine)
    ctx.routine.output_compression = "compress"
    return ctx


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
@pytest.mark.parametrize("mode", ["compress", "measure"])
def test_rejects_unverified_json_without_changing_baseline(ctx, monkeypatch, original, candidate, mode):
    # Padding makes each synthetic JSON eligible without obscuring the changed value.
    original = original + " " * 2500
    ctx.routine.output_compression = "off"
    baseline = compression.command_output(ctx, "sample", original, "warning", 0)
    ctx.routine.output_compression = mode
    monkeypatch.setattr(compression, "_compress", lambda *_: candidate)
    obs = compression.command_output(ctx, "sample", original, "warning", 0)
    assert obs["compression"]["status"] == "fallback"
    assert {k: v for k, v in obs.items() if k != "compression"} == baseline


def test_measure_preserves_model_input_and_replay(ctx, monkeypatch):
    monkeypatch.setattr(compression, "_compress", lambda *_: json.dumps(json.loads(DATA), separators=(",", ":")))
    ctx.routine.output_compression = "off"
    baseline = compression.command_output(ctx, "sample", DATA, "warning", 0)
    ctx.routine.output_compression = "measure"
    measured = compression.command_output(ctx, "sample", DATA, "warning", 0)
    assert {k: v for k, v in measured.items() if k != "compression"} == baseline
    assert measured["compression"]["status"] == "measured"
    assert not (ctx.run_dir / "outputs").exists()
    obs = {"kind": "util", "name": "sample", "exit": 0, **measured}
    messages, _, _ = replay_messages([{"type": "observation", "payload": obs}])
    assert messages[0]["content"] == format_observation(obs)
    assert "estimated_tokens_saved" not in messages[0]["content"]


def test_original_recovery_survives_other_runs_and_replay(ctx, monkeypatch):
    monkeypatch.setattr(compression, "_compress", lambda *_: json.dumps(json.loads(DATA), separators=(",", ":")))
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


@pytest.mark.parametrize("result", [DATA, "", "<<ccr:missing>>", "x" * 9_000])
def test_unusable_or_larger_result_falls_back(ctx, monkeypatch, result):
    monkeypatch.setattr(compression, "_compress", lambda *_: result)
    obs = compression.command_output(ctx, "sample", DATA, "", 0)
    assert obs["compression"]["status"] in {"fallback", "unchanged"}
    assert obs["stdout"] == compression.truncate(DATA, keep="head")[0]


@pytest.mark.parametrize("error", [ImportError("missing"), RuntimeError("secret text")])
def test_unavailable_or_failed_compressor_is_visible_without_leaking(ctx, monkeypatch, error):
    def fail(*_):
        raise error
    monkeypatch.setattr(compression, "_compress", fail)
    obs = compression.command_output(ctx, "sample", DATA, "", 0)
    assert obs["compression"]["status"] in {"unavailable", "fallback"}
    assert "secret text" not in json.dumps(obs)


def test_original_save_failure_never_replaces_output(ctx, monkeypatch):
    monkeypatch.setattr(compression, "_compress", lambda *_: json.dumps(json.loads(DATA), separators=(",", ":")))
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
def test_json_is_minified_whole_without_the_optional_extra(ctx, monkeypatch, text):
    """JSON compression is stdlib only: it must not import the optional package at all,
    and the preview must parse back to the ORIGINAL — the middle included.
    """
    monkeypatch.setitem(sys.modules, "headroom._core", None)   # any import here → ImportError
    obs = compression.command_output(ctx, "sample", text, "", 0)
    assert obs["compression"]["status"] == "applied"
    assert "minified JSON; nothing removed" in obs["stdout"]
    assert json.loads(obs["stdout"].split("\n", 1)[1]) == json.loads(text)


def test_minified_json_keeps_the_verification_gate(ctx, monkeypatch):
    """Minification is faithful by construction, so the verifier is now an assertion
    rather than a safety net — it must still refuse a candidate that is not.
    """
    monkeypatch.setattr(compression, "_compress", lambda *_: '{"value": 1.0}')
    obs = compression.command_output(ctx, "sample", '{"value": 1}' + " " * 2500, "", 0)
    assert obs["compression"]["status"] == "fallback"


@pytest.mark.parametrize(("mode", "text", "code"), [
    ("off", DATA, 0), ("compress", DATA, 1), ("compress", "short", 0),
    ("measure", "ordinary prose " * 300, 0),
])
def test_ineligible_never_loads_headroom(ctx, monkeypatch, mode, text, code):
    ctx.routine.output_compression = mode
    monkeypatch.setattr(compression, "_compress", lambda *_: pytest.fail("must bypass"))
    compression.command_output(ctx, "sample", text, "exact", code)


def test_every_outcome_is_tallied_on_the_run(ctx, monkeypatch):
    """The run carries ONE tally — what the durable usage record hands the Stats tab's
    per-routine roll-up. A saving counts for an APPLIED preview only; the time counts for
    every outcome, because a rejected compression cost the run exactly what a kept one did.
    """
    monkeypatch.setattr(compression, "_compress",
                        lambda *_: json.dumps(json.loads(DATA), separators=(",", ":")))
    applied = compression.command_output(ctx, "sample", DATA, "", 0)
    assert applied["compression"]["status"] == "applied"
    monkeypatch.setattr(compression, "_compress", lambda *_: '["cut"]')
    assert compression.command_output(ctx, "sample", DATA, "", 0)["compression"]["status"] == "fallback"
    compression.command_output(ctx, "sample", "short", "", 0)              # ineligible
    tally = ctx.compression_stats
    assert tally["applied"] == 1
    assert tally["fallback"] == 1
    assert tally["skipped"] == 1
    assert tally["tokens_saved"] == applied["compression"]["estimated_tokens_saved"] > 0
    assert tally["ms"] > 0


def test_off_tallies_nothing(ctx):
    """Off is not an outcome: nothing was considered, so nothing is counted."""
    ctx.routine.output_compression = "off"
    compression.command_output(ctx, "sample", DATA, "", 0)
    assert ctx.compression_stats == {}


def test_config_rejects_invalid_mode_and_defaults_compress(make_routine):
    path = make_routine()
    cfg, _ = load_routine(path)
    assert cfg.output_compression == "compress"
    with (path / "routine.yaml").open("a") as f:
        f.write("\noutput_compression: invalid\n")
    cfg, problems = load_routine(path)
    assert cfg.output_compression == "compress"
    assert any("output_compression" in p for p in problems)


@pytest.mark.skipif(importlib.util.find_spec("headroom") is None,
                    reason="optional real-package smoke: install rsched[headroom]")
def test_real_headroom_native_api(ctx):
    obs = compression.command_output(ctx, "sample", DATA, "", 0)
    assert obs["compression"]["status"] in {"applied", "fallback", "unchanged"}
    assert "<<ccr:" not in obs["stdout"]
    if obs["compression"]["status"] == "applied":
        assert json.loads(obs["stdout"].split("\n", 1)[1]) == json.loads(DATA)
    else:
        assert obs["stdout"] == compression.truncate(DATA, keep="head")[0]
    logs = "\n".join(f"INFO heartbeat healthy worker {i % 3}" for i in range(500))
    obs = compression.command_output(ctx, "logs", logs, "", 0)
    assert obs["compression"]["status"] == "applied"
    assert "log excerpt; lines omitted" in obs["stdout"]


@pytest.mark.skipif(importlib.util.find_spec("headroom") is None,
                    reason="optional installed-package offline probe")
def test_real_headroom_fresh_process_offline_and_bounded(tmp_path):
    # Audit before importing Headroom: fail on attempted Python socket use, process
    # launches or persistent writes, including imports into an isolated empty HOME.
    probe = r"""
import json, sys, time
from importlib.metadata import version
from rsched.engine.output_compression import _compress, _verified_json
attempts = []
def audit(event, args):
    blocked = event.startswith("socket.") or event in {"subprocess.Popen", "os.system"}
    if event == "open":
        mode, flags = args[1:3]
        blocked = (isinstance(mode, str) and any(c in mode for c in "wax+")) or (
            isinstance(flags, int) and bool(flags & (1 | 2 | 64 | 512 | 1024)))
    if blocked:
        attempts.append(event)
        raise RuntimeError("offline probe blocked " + event)
sys.addaudithook(audit)
rows = ["first", {"critical": "middle", "n": 1.0}, None, True,
        *[f"unique dependency {i} >= {i}.0" for i in range(300)], "last"]
samples = {
    "heterogeneous": json.dumps(rows, indent=2),
    "large-json": json.dumps([{"id": i, "message": "synthetic " * 12} for i in range(5000)]),
    "logs": "\n".join(f"INFO synthetic heartbeat worker {i % 3}" for i in range(25000)),
}
results = []
for name, text in samples.items():
    started = time.monotonic()
    candidate = _compress(text, "logs" if name == "logs" else "json")
    elapsed = time.monotonic() - started
    equivalent = None
    if name != "logs":
        try:
            equivalent = _verified_json(text) == _verified_json(candidate)
        except ValueError:
            equivalent = False
    results.append(dict(name=name, input_chars=len(text), candidate_chars=len(candidate),
                        seconds=round(elapsed, 4), equivalent=equivalent))
assert not attempts, attempts
print(json.dumps(dict(version=version("headroom-ai"), results=results, blocked_attempts=attempts)))
"""
    env = {**os.environ, "HOME": str(tmp_path), "XDG_CACHE_HOME": str(tmp_path / "cache"),
           "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "PYTHONDONTWRITEBYTECODE": "1"}
    started = time.monotonic()
    result = subprocess.run([sys.executable, "-B", "-c", probe], env=env,
                            capture_output=True, text=True, timeout=30, check=False)
    assert result.returncode == 0, result.stderr
    evidence = json.loads(result.stdout)
    assert evidence["version"] == "0.37.0"
    assert evidence["blocked_attempts"] == []
    assert not list(tmp_path.rglob("*"))
    assert time.monotonic() - started < 30



def test_child_original_is_engine_owned(ctx, monkeypatch):
    from rsched.grantpolicy import GrantPolicy

    ctx.run_dir = ctx.run_dir / "sub" / "1"
    ctx.run_dir.mkdir(parents=True)
    ctx.routine = ctx.routine.model_copy(update={"dir": ctx.run_dir})
    ctx.depth = 1
    ctx.grants = GrantPolicy()
    monkeypatch.setattr(compression, "_compress", lambda *_: json.dumps(json.loads(DATA), separators=(",", ":")))
    obs = compression.command_output(ctx, "sample", DATA, "", 0)
    path = obs["full_output"]["stdout"]
    result = dispatch({"kind": "write_file", "path": path, "content": "forged"}, ctx)
    assert "engine-owned" in result["error"]
    read = dispatch({"kind": "read_file", "path": path}, ctx)
    assert DATA in format_observation(read)
