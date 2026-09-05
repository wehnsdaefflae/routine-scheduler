"""The archive as a store the relevance layer feeds from: the one-turn warning before the
middle is evicted, and the pointer that brings an archived file back when it matters."""

from types import SimpleNamespace

import pytest

from rsched.engine import recall


def _index(tmp_path, *entries) -> object:
    hist = tmp_path / "history"
    hist.mkdir(parents=True, exist_ok=True)
    lines = ["# History index — the archived middle of this run, one file per topic."]
    for name, about in entries:
        (hist / name).write_text("body\n", encoding="utf-8")
        lines.append(f"- `{name}` — {about}")
    (hist / "INDEX.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return hist


# --- reading the index ---------------------------------------------------------------------

def test_index_entries_parses_what_the_engine_writes(tmp_path):
    hist = _index(tmp_path, ("t12-oauth-token-refresh.md", "how the token refresh was fixed"),
                  ("t40-invoice-parsing.md", "the invoice parser's field mapping"))
    assert recall.index_entries(hist) == [
        ("t12-oauth-token-refresh.md", "how the token refresh was fixed"),
        ("t40-invoice-parsing.md", "the invoice parser's field mapping")]


def test_a_missing_or_model_written_index_yields_nothing(tmp_path):
    """Recall reads only the shape the ENGINE writes. A pre-0.307 index was authored by the
    model against its own filenames — every entry a map to a file that does not exist — so
    parsing one would inherit exactly the defect the engine-owned index fixed."""
    assert recall.index_entries(tmp_path / "nope") == []
    hist = tmp_path / "history"
    hist.mkdir()
    (hist / "INDEX.md").write_text("- oauth-notes: what we found\n- INDEX.md\n", encoding="utf-8")
    assert recall.index_entries(hist) == []


# --- matching --------------------------------------------------------------------------------

def test_the_best_match_is_the_archived_topic_the_action_is_about(tmp_path):
    entries = recall.index_entries(_index(
        tmp_path,
        ("t12-oauth-token-refresh.md", "how the token refresh was fixed"),
        ("t40-invoice-parsing.md", "the invoice parser field mapping"),
        ("t55-weather-digest.md", "the digest layout")))
    subject = recall._tokens("util:oauth-client refresh --token") | recall._tokens(
        "Refreshing the oauth token again")
    assert recall.best_match(entries, subject, set())[0] == "t12-oauth-token-refresh.md"


def test_a_weak_overlap_is_silence_not_a_guess(tmp_path):
    """A wrong pointer costs a read AND teaches the run to ignore the layer — worse than
    saying nothing. Measured recall is 23% top-3 over real archives, so the floor matters."""
    entries = recall.index_entries(_index(
        tmp_path, ("t12-oauth-token-refresh.md", "how the token refresh was fixed")))
    subject = recall._tokens("write_file path=state/unrelated-thing.json")
    assert recall.best_match(entries, subject, set()) is None


def test_a_file_already_named_is_not_named_again(tmp_path):
    entries = recall.index_entries(_index(
        tmp_path, ("t12-oauth-token-refresh.md", "how the token refresh was fixed")))
    subject = recall._tokens("util:oauth-client refresh --token")
    assert recall.best_match(entries, subject, set()) is not None
    assert recall.best_match(entries, subject, {"t12-oauth-token-refresh.md"}) is None


def test_the_stopwords_keep_structural_tokens_from_matching(tmp_path):
    """`read`, `file`, `state`, `util` appear in nearly every action string; a match on one
    is not evidence of anything."""
    entries = recall.index_entries(_index(
        tmp_path, ("t9-read-the-state-file.md", "how to read a state file")))
    assert recall.best_match(entries, recall._tokens("read_file path=state/x.json"),
                             set()) is None


# --- the tail --------------------------------------------------------------------------------

def _loop(tmp_path, *, turn=20, active=True, after=0):
    ctx = SimpleNamespace(turn=turn, run_dir=tmp_path)
    return SimpleNamespace(ctx=ctx, _history_active=active, _recalled=set(),
                           _recall_after=after, _hist_rel="runs/x/history")


def test_the_tail_names_the_file_and_the_turn_it_was_archived_at(tmp_path):
    _index(tmp_path, ("t12-oauth-token-refresh.md", "how the token refresh was fixed"))
    loop = _loop(tmp_path)
    tail = recall.at_observation(
        loop, {"kind": "util", "name": "oauth-client", "args": ["refresh", "--token"],
               "say": "Refreshing the oauth token."}, {"kind": "util", "exit": 0})
    assert "[HISTORY:" in tail
    assert "archived at turn 12" in tail          # the turn lives in the FILENAME and nowhere else
    assert "runs/x/history/t12-oauth-token-refresh.md" in tail
    assert "how the token refresh was fixed" in tail
    assert "read_file it" in tail                 # a pointer, never a fetch
    # …and it does not repeat: one pointer per file, then a cooldown
    assert recall.at_observation(loop, {"kind": "util", "name": "oauth-client",
                                        "args": ["refresh"], "say": "again"},
                                 {"kind": "util", "exit": 0}) == ""


def test_nothing_is_surfaced_before_a_compaction_has_happened(tmp_path):
    _index(tmp_path, ("t12-oauth-token-refresh.md", "how the token refresh was fixed"))
    loop = _loop(tmp_path, active=False)
    assert recall.at_observation(loop, {"kind": "util", "name": "oauth-client",
                                        "args": ["refresh", "--token"], "say": "x"},
                                 {"kind": "util", "exit": 0}) == ""


def test_the_cooldown_holds_between_pointers(tmp_path):
    _index(tmp_path, ("t12-oauth-token-refresh.md", "the token refresh"),
           ("t14-oauth-scope-widening.md", "the oauth scope change"))
    loop = _loop(tmp_path, turn=5)
    action = {"kind": "util", "name": "oauth-client", "args": ["refresh", "--token"],
              "say": "oauth token refresh"}
    assert recall.at_observation(loop, action, {"kind": "util", "exit": 0}) != ""
    assert loop._recall_after > 5
    loop.ctx.turn = 6
    assert recall.at_observation(loop, action, {"kind": "util", "exit": 0}) == ""
    loop.ctx.turn = loop._recall_after
    assert recall.at_observation(loop, action, {"kind": "util", "exit": 0}) != ""


# --- the one-turn warning before eviction -----------------------------------------------------

def _warn_loop(messages, *, warned=False):
    return SimpleNamespace(
        _evict_warned=warned, messages=list(messages),
        ctx=SimpleNamespace(transcript=SimpleNamespace(event=lambda *a, **k: None)))


@pytest.mark.parametrize(("size_factor", "expect_warning"), [(0.5, True), (0.99, False)])
def test_the_warning_fires_only_when_there_is_a_turn_of_slack(size_factor, expect_warning):
    """The gate is min(fraction × window, ceiling). When the FRACTION binds there is 20–40% of
    the window before the hard ceiling — a whole turn of slack, so deferring the archive is
    free. When the CEILING binds there is none, and a warning that overflowed the window would
    cost the run the very turns it was protecting."""
    from rsched.engine.compaction import window_ceiling_chars
    from rsched.engine.window import _warn_before_eviction

    ref = SimpleNamespace(context_chars=400_000, max_tokens=8_000)
    ceiling = window_ceiling_chars(ref.context_chars, ref.max_tokens)
    loop = _warn_loop([{"role": "user", "content": "x"}])
    assert _warn_before_eviction(loop, ceiling * size_factor, ref) is expect_warning
    assert (len(loop.messages) == 2) is expect_warning
    if expect_warning:
        note = loop.messages[-1]["content"]
        assert "about to be ARCHIVED" in note
        assert "Retention is positional, not semantic" in note
        assert "`note`" in note and "memory_write" in note and "LEDGER" in note


def test_the_warning_is_given_once_per_run():
    """A second warning would be the layer talking about itself."""
    from rsched.engine.compaction import window_ceiling_chars
    from rsched.engine.window import _warn_before_eviction

    ref = SimpleNamespace(context_chars=400_000, max_tokens=8_000)
    small = window_ceiling_chars(ref.context_chars, ref.max_tokens) * 0.5
    loop = _warn_loop([])
    assert _warn_before_eviction(loop, small, ref) is True
    assert _warn_before_eviction(loop, small, ref) is False
    assert len(loop.messages) == 1
