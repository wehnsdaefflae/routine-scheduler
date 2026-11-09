"""The prompt's per-turn SEGMENT breakdown (D270) — and the one thing it must never do.

`engine/promptsegments.py` measures what the prompt is MADE OF: the composer's own
`(name, text)` sections plus the two segments the message list grows (the model's replies,
the observations it was handed). The breakdown is diagnostic: a run reads it to see where its
window is going.

The decisive test here is the last one. The composed prompt is a CACHING CONTRACT — appended
to, never mutated (engine/window.py's header) — so a measurement that changed one byte of
what is sent would invalidate the provider's prompt cache for the rest of the run. That is
money, not tidiness: a cold 1M-token window has cost over $100 on this instance. So the
measurement is proved to leave both halves of the request untouched — the joined system
prompt and the message list, media included.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from conftest import mk_run
from rsched.engine import promptsegments
from rsched.engine.compaction import estimate_input_tokens
from rsched.engine.composer import join_sections
from rsched.engine.window import note_prompt_size
from rsched.paths import atomic_write_json, read_json
from test_token_calibration import REF, _loop_with_prompt

#: A stand-in for `composer.system_sections`' return: (name, text) in PROMPT ORDER.
SECTIONS = [("harness", "h" * 7000), ("schema", "s" * 700), ("digest", "d" * 70)]


def _loop(messages: list[dict], segments: dict | None = None) -> SimpleNamespace:
    """The two attributes `note` touches: the message list, and the run context it records on."""
    return SimpleNamespace(messages=messages,
                           ctx=SimpleNamespace(prompt_segments=dict(segments or {})))


def test_every_always_on_section_is_measured_in_prompt_order() -> None:
    """One entry per composer section, in the order the prompt sends them — the breakdown is
    the composer's own segmentation, so it cannot disagree with what `join_sections` joins.
    """
    sizes = promptsegments.of_sections(SECTIONS)
    assert list(sizes) == ["harness", "schema", "digest"]
    assert sizes["harness"] > sizes["schema"] > sizes["digest"] > 0


def test_the_growing_segments_are_split_by_who_said_it() -> None:
    """`history` is what the model said, `observations` what the engine handed back — the
    split that answers whether a run's window is going on its own reasoning or on what it
    read. The system message is already counted section by section and is never counted again.
    """
    loop = _loop([{"role": "system", "content": "S" * 9000},
                  {"role": "assistant", "content": "A" * 700},
                  {"role": "user", "content": "O" * 7000}],
                 promptsegments.of_sections(SECTIONS))
    promptsegments.note(loop)
    segs = loop.ctx.prompt_segments
    assert segs["history"] < segs["observations"]
    assert segs["observations"] == estimate_input_tokens([{"content": "O" * 7000}])
    assert segs["harness"] == promptsegments.of_sections(SECTIONS)["harness"]


def test_a_turn_with_nothing_said_yet_measures_zero_not_the_system_prompt() -> None:
    """Turn 1: the message list holds only the system prompt, so both growing segments are
    the empty measurement — never the system prompt leaking into `observations`.
    """
    loop = _loop([{"role": "system", "content": "S" * 9000}])
    promptsegments.note(loop)
    assert loop.ctx.prompt_segments["history"] == estimate_input_tokens([])
    assert loop.ctx.prompt_segments["observations"] == estimate_input_tokens([])


def test_the_measurement_changes_not_one_byte_of_what_is_sent() -> None:
    """The hard constraint from the order: diagnostic only, never in the cached prompt.

    Both halves of the request are compared before and after: the JOINED system prompt (what
    `build_system_prompt` sends) and the MESSAGE LIST itself, media attachments included.
    """
    sent_system = join_sections(SECTIONS)
    messages = [{"role": "system", "content": sent_system},
                {"role": "assistant", "content": '{"kind": "read_file"}'},
                {"role": "user", "content": "OBSERVATION",
                 "media": [{"path": "runs/20260101-000000/shot.png"}]}]
    sent_messages = json.dumps(messages, sort_keys=True)

    loop = _loop(messages, promptsegments.of_sections(SECTIONS))
    promptsegments.note(loop)

    assert join_sections(SECTIONS) == sent_system
    assert json.dumps(loop.messages, sort_keys=True) == sent_messages
    assert loop.messages is messages          # measured in place, never rebuilt
    # ...and the measurement RAN. Without this line every assertion above is "nothing
    # changed", which a `note` that did nothing at all would satisfy perfectly.
    assert loop.ctx.prompt_segments["observations"] > 0


def test_the_turn_that_calibrates_the_window_records_the_breakdown(make_routine) -> None:
    """The wiring, through the PRODUCTION entry point: `window.note_prompt_size` is the one
    writer of both readings of the prompt the provider just counted, so the breakdown lands on
    the run context on every turn that calibrates — not only when a test calls `note` itself.
    """
    loop = _loop_with_prompt(make_routine)
    loop._schema_off = True
    # not yet recorded: the attribute is written by boot and by this seam, and the fixture
    # loop has had neither — so the assertions below are about a value THIS call produced
    assert not getattr(loop.ctx, "prompt_segments", None)
    note_prompt_size(loop, REF, {"in": 160_000})
    segs = loop.ctx.prompt_segments
    assert segs["observations"] > 0 and segs["history"] > 0
    # the grown segments are the message list's own halves, measured in the window's unit
    assert segs["history"] + segs["observations"] <= estimate_input_tokens(loop.messages)


def test_the_run_detail_carries_the_breakdown_to_the_run_view(api_client, make_routine) -> None:
    """The measurement is only diagnostic if a reader can SEE it: the run view's bar
    (static/components/promptsegbar.js) reads `prompt_segments` off GET /api/runs/<id>, so the
    route has to carry what status.json recorded — in the composer's prompt ORDER, which is
    what makes the bar readable as the prompt rather than as a set of numbers.
    """
    c, tmp = api_client
    make_routine(slug="segr")
    run = mk_run(tmp / "routines" / "segr", "20260906-100000", "running")
    status = read_json(run / "status.json")
    status["prompt_segments"] = {"harness": 2000, "schema": 700, "digest": 70,
                                 "history": 400, "observations": 9000}
    atomic_write_json(run / "status.json", status)

    detail = c.get("/api/runs/segr:20260906-100000").json()
    assert list(detail["prompt_segments"]) == ["harness", "schema", "digest",
                                               "history", "observations"]
    assert detail["prompt_segments"]["observations"] == 9000


def test_a_run_that_measured_nothing_carries_an_empty_breakdown(api_client,
                                                                make_routine) -> None:
    """A run recorded before D270 — or one no provider ever reported a prompt size for — has
    no breakdown, and the route says so with an empty mapping rather than omitting the key:
    the bar's own absence is then a decision the component makes, not a KeyError.
    """
    c, tmp = api_client
    make_routine(slug="oldr")
    mk_run(tmp / "routines" / "oldr", "20260906-110000", "finished")
    detail = c.get("/api/runs/oldr:20260906-110000").json()
    assert detail["prompt_segments"] == {}
