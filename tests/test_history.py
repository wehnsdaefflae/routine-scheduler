"""Unit tests for engine.history resume helpers — prior_counters (F131/F132)
and the runner-side queued-status write that must not clobber them (F140)."""

from rsched.daemon.runner_state import _queued_status
from rsched.engine.compaction import (
    clamp_to_cap,
    estimate_input_tokens,
    input_cap_tokens,
    window_ceiling_tokens,
)
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


# Output reservations and compaction use tokens throughout.
def test_input_cap_reserves_output_room_on_small_window():
    for cached in (False, True):
        assert input_cap_tokens(65536, 16384, cached=cached) + 16384 <= 65536
    assert input_cap_tokens(65536, 16384, cached=True) == 49152


def test_input_cap_large_window():
    assert input_cap_tokens(200000, 16384, cached=False) == 120000
    assert input_cap_tokens(200000, 16384, cached=True) == 160000


def test_input_cap_never_negative():
    assert input_cap_tokens(10000, 100000, cached=False) == 0


def test_window_ceiling_reserves_output():
    assert window_ceiling_tokens(65536, 16384) == 49152


def test_estimator_accounts_for_unicode_framing_and_media():
    english = [{"role": "user", "content": "a" * 100}]
    unicode = [{"role": "user", "content": "界" * 100}]
    assert estimate_input_tokens(unicode) > estimate_input_tokens(english)
    assert estimate_input_tokens([{"role": "user", "content": ""}]) > 0
    assert estimate_input_tokens([{**english[0], "media": [{}]}]) > estimate_input_tokens(english)


def test_clamp_forces_short_conversation_floor_under_ceiling():
    for text in ("x" * 9000, "界" * 9000, '{"key":123},' * 900):
        messages = [{"role": "user", "content": text} for _ in range(30)]
        assert estimate_input_tokens(messages) > 49152
        info = clamp_to_cap(messages, 65536, 16384)
        assert info and info["clamped_messages"] > 0
        assert estimate_input_tokens(messages) <= 49152
        assert info["after_estimated_tokens"] <= info["ceiling_tokens"]
        assert len(messages) == 30
        assert any("window clamp" in m["content"] for m in messages)


def test_clamp_never_touches_the_composed_system_prompt():
    """Message 0 is the composed system prompt — the recipe's contract, CAPABILITIES, the
    state digest — and it is the largest body in every real run (~90 KB against the 8 KB
    observation cap). Ordering by size therefore cut it first and cut it EVERY pass
    (token-lab:20260910-073700: 35 clamps, the first 90,982 -> 30,097 chars), which both
    truncated the contract and rewrote the cached prefix from byte zero each turn.
    """
    messages = [{"role": "system", "content": "S" * 90_000}]
    messages += [{"role": "user", "content": "x" * 9000} for _ in range(29)]
    info = clamp_to_cap(messages, 65536, 16384)
    assert info and info["clamped_messages"] > 0
    assert messages[0]["content"] == "S" * 90_000
    assert "window clamp" not in messages[0]["content"]
    assert any("window clamp" in m["content"] for m in messages[1:])


def test_clamp_noop_when_already_under_ceiling():
    messages = [{"role": "user", "content": "x" * 500} for _ in range(30)]
    assert clamp_to_cap(messages, 65536, 16384) is None
    assert all(len(m["content"]) == 500 for m in messages)


def test_clamp_leaves_small_bodies_alone_when_it_cannot_help():
    messages = [{"role": "user", "content": "x" * 100} for _ in range(50)]
    assert clamp_to_cap(messages, 1000, 0) is None


def test_schema_tokens_are_reserved_separately():
    import json
    from types import SimpleNamespace

    from rsched.engine.window import _reserved_tokens

    schema = {"description": "structured action " * 1000}
    loop = SimpleNamespace(action_schema=schema, _schema_off=False)
    ref = SimpleNamespace(max_tokens=16384)
    reserve = _reserved_tokens(loop, ref)
    assert reserve > ref.max_tokens
    messages = [{"role": "system", "content": "prompt"},
                {"role": "user", "content": "observation " * 30000}]
    clamp_to_cap(messages, 65536, reserve)
    schema_estimate = estimate_input_tokens([{"content": json.dumps(schema, ensure_ascii=False)}])
    assert estimate_input_tokens(messages) + schema_estimate + ref.max_tokens <= 65536
    loop._schema_off = True
    assert _reserved_tokens(loop, ref) == ref.max_tokens
