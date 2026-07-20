"""Unit tests for engine.history resume helpers — prior_counters (F131/F132)."""

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
