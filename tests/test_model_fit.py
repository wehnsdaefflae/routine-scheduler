"""web/model_fit: the picker-side window-fit derivation (R112/R128). Effective figures
resolve like EndpointRegistry (model value, else endpoint default) without a transport;
fit classifies against the engine's own window_ceiling_chars math, so what the picker
labels "impossible" is exactly what dies with context_length_exceeded on turn one."""

from types import SimpleNamespace

from rsched.config import EndpointConfig, ModelConfig
from rsched.engine.compaction import window_ceiling_chars
from rsched.web.model_fit import (
    TIGHT_INPUT_CHARS,
    effective_window_pair,
    fit_fields,
    model_window_problem,
    window_meta,
)


def _server(models: dict, endpoints: dict) -> SimpleNamespace:
    return SimpleNamespace(models=models, endpoints=endpoints)


EP = EndpointConfig(kind="openai", context_chars=100_000, max_tokens=8_192)


def test_effective_window_resolves_like_the_registry():
    own = ModelConfig(endpoint="e", model="x", context_chars=1_048_576, max_tokens=32_000)
    inherit = ModelConfig(endpoint="e", model="y")
    assert effective_window_pair(own, EP) == (1_048_576, 32_000)
    assert effective_window_pair(inherit, EP) == (100_000, 8_192)
    # no endpoint config at all → the codebase's standing defaults, never a crash
    assert effective_window_pair(inherit, None) == (100_000, 16_384)


def test_fit_classification_tracks_the_engine_ceiling():
    big = ModelConfig(endpoint="e", model="x", context_chars=1_048_576, max_tokens=32_000)
    assert fit_fields(big, EP)["fit"] == "ok"
    # 65_536 chars ≈ 16_384 tokens, all of it reserved for output → ceiling 0 → impossible
    dead = ModelConfig(endpoint="e", model="x", context_chars=65_536, max_tokens=16_384)
    f = fit_fields(dead, EP)
    assert f["fit"] == "impossible" and f["input_ceiling_chars"] == 0
    assert f["context_tokens"] == 16_384 and f["max_output_tokens"] == 16_384
    # positive but small input budget (25k-token window, 16.4k reserved) → tight, and the
    # figure agrees with the engine math to the char
    small = ModelConfig(endpoint="e", model="x", context_chars=100_000, max_tokens=16_384)
    f = fit_fields(small, EP)
    assert f["fit"] == "tight"
    assert 0 < f["input_ceiling_chars"] < TIGHT_INPUT_CHARS
    assert f["input_ceiling_chars"] == int(window_ceiling_chars(100_000, 16_384))


def test_model_window_problem_names_numbers_and_fix():
    dead = ModelConfig(name="dead", endpoint="e", model="x",
                       context_chars=65_536, max_tokens=16_384)
    ok = ModelConfig(name="ok", endpoint="e", model="y", context_chars=1_048_576)
    server = _server({"dead": dead, "ok": ok}, {"e": EP})
    assert model_window_problem(server, "ok") is None
    msg = model_window_problem(server, "dead")
    assert msg and "cannot run a single turn" in msg
    assert "16,384" in msg and "Settings" in msg


def test_window_meta_covers_the_whole_catalog():
    dead = ModelConfig(name="dead", endpoint="e", model="x",
                       context_chars=65_536, max_tokens=16_384)
    ok = ModelConfig(name="ok", endpoint="e", model="y", context_chars=1_048_576)
    meta = window_meta(_server({"dead": dead, "ok": ok}, {"e": EP}))
    assert set(meta) == {"dead", "ok"}
    assert meta["dead"]["fit"] == "impossible" and meta["ok"]["fit"] == "ok"
