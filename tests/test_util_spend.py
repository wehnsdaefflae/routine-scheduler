"""A util's MODEL SPEND is booked against the run that called it (D288 step 1, engine/utilspend.py). The operator's resolution was "Convert fully" — `llm` and `decide` stop being action kinds and one util carries both capabilities — and `llmaction.do_llm`/`decideaction.do_decide` each call `ctx.add_usage`, while a util call booked nothing: converting without this would make every run's inference spend vanish from its own meter, from the usage stream and from the per-routine cost the operator reads, which is a regression dressed as a cleanup."""

from __future__ import annotations

import json
from pathlib import Path

from rsched.engine import utilspend


class _Ctx:
    """A run context that records what was booked — the only thing this seam does to a run."""

    def __init__(self) -> None:
        self.booked: list[dict] = []

    def add_usage(self, usage: dict) -> None:
        self.booked.append(usage)


def _sink(tmp_path: Path, *rows: object) -> Path:
    path = tmp_path / "usage.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


def test_the_provider_spelling_is_read_because_that_is_what_a_util_has(tmp_path):
    """An OpenAI-compatible provider answers `prompt_tokens`/`completion_tokens`, which is the
    shape a util holds; the engine's own `Completion.usage` uses `in`/`out`. Both are accepted
    because both are already in the house, and a util must not have to translate."""
    got = utilspend.collect(_sink(tmp_path, {"prompt_tokens": 120, "completion_tokens": 30}))
    assert got == {"in": 120, "out": 30}


def test_the_engines_own_spelling_is_read_too(tmp_path):
    got = utilspend.collect(_sink(tmp_path, {"in": 7, "out": 11, "cache_read": 3}))
    assert got == {"in": 7, "out": 11, "cache_read": 3}


def test_several_calls_in_one_invocation_are_summed(tmp_path):
    """One util invocation may make several model calls (a decide over a batch of questions, a
    rewrite that retries), and each appends its own line — so the file is JSONL, not one object."""
    got = utilspend.collect(_sink(tmp_path, {"in": 10, "out": 2}, {"in": 5, "out": 1}))
    assert got == {"in": 15, "out": 3}


def test_a_missing_sink_books_nothing_and_raises_nothing(tmp_path):
    """The overwhelming majority of util calls: no model call, so the file never exists. The
    engine creates nothing, and absence is the normal case rather than an error."""
    assert utilspend.collect(tmp_path / "never-written.jsonl") == {}
    assert utilspend.collect(None) == {}


def test_a_damaged_sink_books_nothing_and_raises_nothing(tmp_path):
    """A util's accounting must not be able to take down the util's work: the cost of a dropped
    reading is one unbooked call, the cost of a raise here is the run. A half-written line, a
    JSON scalar and a list are each read as nothing, and a good line beside them still counts."""
    path = tmp_path / "usage.jsonl"
    path.write_text('{"in": 4, "out": 1}\n{"in": 9\n"just a string"\n[1, 2]\n\n',
                    encoding="utf-8")
    assert utilspend.collect(path) == {"in": 4, "out": 1}


def test_a_negative_count_is_dropped_rather_than_clamped(tmp_path):
    """It cannot be a real reading, and booking it would let a util REDUCE a run's measured
    spend — the one direction an accounting seam must never allow."""
    got = utilspend.collect(_sink(tmp_path, {"in": 50, "out": -40}))
    assert got == {"in": 50}, "a negative count reached the meter"


def test_a_boolean_is_not_a_token_count(tmp_path):
    """`True` is an int in Python, so a careless reader books 1 token for `{"in": true}`."""
    assert utilspend.collect(_sink(tmp_path, {"in": True, "out": 5})) == {"out": 5}


def test_run_and_book_hands_the_util_a_sink_and_books_what_it_wrote(tmp_path):
    """The whole seam end to end: the sink reaches the util as an ENGINE-INJECTED ENV VALUE in
    `extra_secrets` — the channel RSCHED_API_TOKEN and RSCHED_ROUTINE already ride, so the util
    runner needs no parameter of its own (it is at its size ceiling) — whatever is written there
    is collected, and the reading reaches the run's meter. The call and its accounting in one."""
    ctx = _Ctx()
    seen: dict = {}

    def fake_run(*, extra_secrets: dict, **kwargs: object) -> tuple[int, str, str]:
        seen.update(kwargs, env=extra_secrets)
        Path(extra_secrets[utilspend.SINK_VAR]).write_text(
            json.dumps({"prompt_tokens": 80, "completion_tokens": 20}) + "\n", encoding="utf-8")
        return 0, "answer", ""

    code, out, err = utilspend.run_and_book(ctx, fake_run, name="llm-complete", args=["hi"])
    assert (code, out, err) == (0, "answer", "")
    assert seen["name"] == "llm-complete" and seen["args"] == ["hi"]
    assert ctx.booked == [{"in": 80, "out": 20}]


def test_the_callers_own_injected_env_is_not_lost(tmp_path):
    """The sink shares `extra_secrets` with every other engine-injected value, so it must be
    ADDED to what the caller resolved — replacing that dict would strip a util of its routine's
    secrets, its API token and its inference endpoints, which is a far worse bug than an
    unbooked call."""
    ctx = _Ctx()
    seen: dict = {}

    def fake_run(*, extra_secrets: dict, **kwargs: object) -> tuple[int, str, str]:
        seen.update(extra_secrets)
        return 0, "", ""

    utilspend.run_and_book(ctx, fake_run, extra_secrets={"RSCHED_API_TOKEN": "t",
                                                         "RSCHED_ROUTINE": "r"})
    assert seen["RSCHED_API_TOKEN"] == "t" and seen["RSCHED_ROUTINE"] == "r"
    assert seen[utilspend.SINK_VAR], "the sink did not travel beside the caller's own env"


def test_the_sink_is_cleaned_up_after_the_call(tmp_path):
    """A temporary directory per call, so one util's reading can never be read as another's."""
    ctx = _Ctx()
    paths: list[Path] = []

    def fake_run(*, extra_secrets: dict, **kwargs: object) -> tuple[int, str, str]:
        sink = Path(extra_secrets[utilspend.SINK_VAR])
        paths.append(sink)
        sink.write_text('{"in": 1, "out": 1}\n', encoding="utf-8")
        return 0, "", ""

    utilspend.run_and_book(ctx, fake_run)
    assert paths and not paths[0].exists(), "the spend sink outlived its call"
    assert not paths[0].parent.exists()


def test_a_util_that_bought_nothing_books_nothing(tmp_path):
    """Almost every call. Nothing is written, so `add_usage` is never reached — a run's meter
    must not gain a zero reading per util call."""
    ctx = _Ctx()
    utilspend.run_and_book(ctx, lambda *, extra_secrets, **kw: (0, "", ""))
    assert ctx.booked == []


def test_a_context_stand_in_without_booking_still_runs_the_util():
    """FAIL OPEN, for the reason `exec_env._extra_secrets` does: this path runs for EVERY util
    call and some callers hand in a context stand-in carrying only what their own subject needs,
    so a hard `ctx.add_usage` would turn an unbooked reading into an AttributeError that kills
    unrelated util calls (8 tests in 6 files red in 0.532.0's second gate, the same cause)."""

    class _Bare:
        pass

    def fake_run(*, extra_secrets: dict, **kwargs: object) -> tuple[int, str, str]:
        Path(extra_secrets[utilspend.SINK_VAR]).write_text('{"in": 3, "out": 2}\n',
                                                           encoding="utf-8")
        return 0, "it still ran", ""

    code, out, _err = utilspend.run_and_book(_Bare(), fake_run)
    assert (code, out) == (0, "it still ran")


def test_the_sink_variable_is_the_name_utils_read():
    """The env var NAME is the contract — utils read it env-first, like every other
    engine-injected value, so renaming it silently stops every util reporting."""
    assert utilspend.SINK_VAR == "RSCHED_USAGE_SINK"
