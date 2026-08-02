"""Unit tests for engine.history resume helpers — prior_counters (F131/F132)
and the runner-side queued-status write that must not clobber them (F140)."""

from rsched.daemon.runner import _queued_status
from rsched.engine.history import prior_counters


def test_prior_counters_reseeds_histogram_and_integer_counters():
    status = {
        "utils": {"websearch": {"ok": 2}, "shell": {"ok": 1, "error": 1}},
        "asks_deferred": 3, "schema_retries": 2, "schema_forcefails": 1, "referrals": 4,
        # non-counter fields must be ignored (they have their own resume mechanism / no reseed)
        "usage": {"in": 10}, "turn": 9, "state": "finished",
    }
    got = prior_counters(status)
    assert got == {
        "util_stats": {"websearch": {"ok": 2}, "shell": {"ok": 1, "error": 1}},
        "asks_deferred": 3, "schema_retries": 2, "schema_forcefails": 1, "referrals": 4,
    }


def test_prior_counters_deep_copies_util_cells():
    status = {"utils": {"x": {"ok": 1}}}
    got = prior_counters(status)
    got["util_stats"]["x"]["ok"] += 5          # mutating the live ctx value…
    assert status["utils"]["x"]["ok"] == 1     # …must not write back into the read status dict


def test_prior_counters_tolerates_missing_and_malformed():
    assert prior_counters({}) == {}
    # wrong types are skipped, not coerced (a bool is NOT a counter despite isinstance(bool,int))
    assert prior_counters({"utils": "nope", "asks_deferred": "3", "referrals": True}) == {}
    # an empty histogram contributes nothing (leaves ctx.util_stats at its default {})
    assert "util_stats" not in prior_counters({"utils": {}})
    # non-dict util cells are dropped, valid ones kept
    assert prior_counters({"utils": {"a": {"ok": 1}, "b": 5}}) == {"util_stats": {"a": {"ok": 1}}}


# --- F140: the RESUME queued-status write must carry the prior leg's telemetry FORWARD ---
# The boot-time prior_counters reseed reads status.json; runner.resume() overwrites it just
# before the engine boots. If that write drops the histogram/counters, a finish->reopen loses
# the pre-finish leg's util calls (the observed bug: 9 util calls -> status showed 2).

def test_queued_status_resume_preserves_prior_counters():
    prior = {
        "run_id": "r:1", "state": "finished", "turn": 60, "usage": {"in": 99, "out": 88},
        "utils": {"routine-runs": {"ok": 3}, "dir-tree": {"ok": 2}, "shell": {"ok": 2}},
        "asks_deferred": 1, "schema_retries": 2, "schema_forcefails": 0, "referrals": 1,
    }
    got = _queued_status("r:1", "20260721-000000", prior)
    # transient run-state fields are reset for the new leg…
    assert got["state"] == "queued" and got["turn"] == 0 and got["usage"] == {"in": 0, "out": 0}
    # …but the cumulative telemetry the reseed depends on survives untouched.
    assert got["utils"] == prior["utils"]
    for k in ("asks_deferred", "schema_retries", "schema_forcefails", "referrals"):
        assert got[k] == prior[k]


def test_queued_status_fresh_run_carries_no_prior():
    got = _queued_status("r:1", "20260721-000000")           # prior=None -> fresh run
    assert got["state"] == "queued" and got["turn"] == 0
    assert "utils" not in got and "asks_deferred" not in got


def test_queued_status_roundtrip_does_not_defeat_reseed():
    # The regression guard: reseeding from the RESUME queued write must yield exactly what
    # reseeding from the prior leg's own final status would have — i.e. the write is lossless.
    leg1 = {
        "run_id": "r:1", "state": "finished", "turn": 60,
        "utils": {"websearch": {"ok": 2}, "shell": {"ok": 1, "error": 1}},
        "asks_deferred": 3, "schema_retries": 2, "schema_forcefails": 1, "referrals": 4,
    }
    queued = _queued_status("r:1", "20260721-000000", leg1)
    assert prior_counters(queued) == prior_counters(leg1)


# --- F265: the compaction input-cap must RESERVE room for the model's output ---
# The provider counts prompt + requested max_tokens output against ONE window; a small-window
# model (nano-gpt gemma, 65536-token window) reached ~49k input tokens and still requested
# 16384 output → the completion 400'd with context_length_exceeded. input_cap_chars must
# leave max_tokens of output room so compaction fires before that overflow.
from rsched.engine.history import CHARS_PER_TOKEN, input_cap_chars  # noqa: E402


def test_input_cap_reserves_output_room_on_small_window():
    # gemma: 65536-token window ≈ 262144 chars, output reservation 16384 tokens.
    window = 65536 * CHARS_PER_TOKEN
    max_out = 16384
    for cached in (False, True):
        cap = input_cap_chars(window, max_out, cached=cached)
        # After compaction to the cap, prompt + output must fit the window with no overflow.
        assert cap + max_out * CHARS_PER_TOKEN <= window, (cap, cached)
    # And it is the reservation (not the fraction trigger) that binds on the cached path:
    # reserved = 262144 - 65536 = 196608 < 0.8*262144 = 209715.
    assert input_cap_chars(window, max_out, cached=True) == window - max_out * CHARS_PER_TOKEN


def test_input_cap_reproduces_c110156_no_overflow():
    # The exact failing request: est input ~49264 tokens, output 16384, window 65536.
    window = 65536 * CHARS_PER_TOKEN
    cap = input_cap_chars(window, 16384, cached=True)
    failing_input_chars = 49264 * CHARS_PER_TOKEN
    # The observed input EXCEEDED the corrected cap, so compaction WOULD have fired.
    assert failing_input_chars > cap


def test_input_cap_large_window_unchanged_by_reservation():
    # A large window (Claude 200k tokens ≈ 800000 chars): the fraction trigger stays binding,
    # so this fix does not change behaviour for big-window models.
    window = 200_000 * CHARS_PER_TOKEN
    max_out = 16384
    assert input_cap_chars(window, max_out, cached=False) == 0.6 * window
    assert input_cap_chars(window, max_out, cached=True) == 0.8 * window


def test_input_cap_never_negative_on_absurd_reservation():
    # A pathological max_tokens larger than the whole window floors the cap at 0, never negative.
    assert input_cap_chars(10_000, 100_000, cached=False) == 0.0
