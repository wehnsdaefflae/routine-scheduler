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
from rsched.engine.history import (  # noqa: E402
    CHARS_PER_TOKEN,
    OUTPUT_RESERVE_SAFETY,
    input_cap_chars,
)


def test_input_cap_reserves_output_room_on_small_window():
    # gemma: 65536-token window ≈ 262144 chars, output reservation 16384 tokens.
    window = 65536 * CHARS_PER_TOKEN
    max_out = 16384
    for cached in (False, True):
        cap = input_cap_chars(window, max_out, cached=cached)
        # After compaction to the cap, prompt + output must fit the window with no overflow.
        assert cap + max_out * CHARS_PER_TOKEN <= window, (cap, cached)
    # The reservation (not the fraction trigger) binds on the cached path, and it now reserves
    # the output room PLUS the OUTPUT_RESERVE_SAFETY margin (F265 recurrence fix): the cap sits
    # a safety margin BELOW window - max_out*CHARS_PER_TOKEN, not flush against it.
    cached_cap = input_cap_chars(window, max_out, cached=True)
    assert cached_cap == (window - max_out * CHARS_PER_TOKEN
                          - OUTPUT_RESERVE_SAFETY * window)
    assert cached_cap < window - max_out * CHARS_PER_TOKEN


def test_input_cap_reproduces_c110156_no_overflow():
    # The exact failing request: est input ~49264 tokens, output 16384, window 65536.
    window = 65536 * CHARS_PER_TOKEN
    cap = input_cap_chars(window, 16384, cached=True)
    failing_input_chars = 49264 * CHARS_PER_TOKEN
    # The observed input EXCEEDED the corrected cap, so compaction WOULD have fired.
    assert failing_input_chars > cap


def test_input_cap_survives_real_tokenizer_undershoot():
    # F265 RECURRED after the zero-margin 0.148.1 fix: CHARS_PER_TOKEN=4 is OPTIMISTIC, so a
    # prompt whose CHAR count sat at/under the old cap (window - max_out*4) still counted more
    # real tokens than budgeted and overflowed (c-20260802-110156: 49326 real input tokens +
    # 16384 output = 65710 > 65536). Model the real pack density observed in that failure and
    # assert that a prompt filling the CURRENT cap leaves the output room even so.
    window = 65536 * CHARS_PER_TOKEN
    max_out = 16384
    cap = input_cap_chars(window, max_out, cached=True)
    # Densest pack seen in the F265 failure: 49326 real tokens for ~49264*4 estimated chars →
    # ~3.996 chars/token. Use a conservatively denser 3.9 chars/token.
    real_chars_per_token = 3.9
    real_input_tokens = cap / real_chars_per_token
    assert real_input_tokens + max_out <= 65536, (real_input_tokens, cap)


def test_input_cap_zero_margin_would_have_overflowed():
    # Guard the FIX's necessity: the OLD zero-margin reservation (window - max_out*4) packed at
    # the observed real density DID overflow — this asserts the margin is what prevents it, so
    # dropping OUTPUT_RESERVE_SAFETY back to 0 re-breaks the test.
    window = 65536 * CHARS_PER_TOKEN
    max_out = 16384
    old_zero_margin_cap = window - max_out * CHARS_PER_TOKEN
    real_chars_per_token = 3.9
    assert old_zero_margin_cap / real_chars_per_token + max_out > 65536


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
