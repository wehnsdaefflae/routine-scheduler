"""D223: a thread whose composed prompt floor exceeds its model's window is HARD-COMPACTED at
boot — the middle dropped to whatever fits — and starts, with the loss stated.

The second half of F539. The first half shipped in 0.474.0: a run served by a smaller model is
told its window, durably (`engine/substitution.py`). The precondition stayed open — the floor is
computable before the first model call, and nothing compared it to anything, so a run that was
unrunnable from turn 0 still started and died mid-run (`llmsectest-weekday:20260922-060001`,
turn 106, $1.03 spent, on *"prompt is too long and cannot be shrunk further: 57,250 estimated
tokens … provider maximum 32,768"*).

The operator's FIRST answer (2026-10-30) was to record it and change nothing. That earned the
evidence for his second: `prompt_floor_over_window` fired NINE times for ONE conversation,
`c-20261014-134208` — 1,063,175 → 1,073,520 → 1,085,503 estimated tokens on 2026-11-08 and
1,089,162 → 1,138,237 on 2026-11-10, against a 1,000,000-token window, every attempt bigger
than the last, after a week of `prompt_oversize_shrunk` passes that had stopped being able to
help. 1,333 turns on disk, a thread that could no longer be added to, and he was still trying.

SO HE DECIDED IT AGAIN (D223, 2026-11-10), verbatim: *"start it, but have the engine's FIRST act
be a hard compaction that drops the middle to whatever fits, so the thread continues with a
stated loss"* — chosen AGAINST the refusal he was also offered. These tests pin the three things
that answer makes load-bearing:

- the prompt is actually SHRUNK before turn one, by dropping the MIDDLE (head and tail are what
  keep a thread answerable), reusing `compaction.maybe_compact` and `clamp_to_cap`;
- the loss is STATED where it is read — in the live prompt the next turn's model sees AND as a
  transcript `user_injection` the conversation and run pages render (`chat.js`,
  `transcript.js`). A middle discarded in silence makes a thread answer from a context it no
  longer has, sounding exactly as certain as one that can see everything;
- a floor that CANNOT be got under the window (head, tail and system prompt are incompressible)
  neither loops nor raises out of boot: it is recorded with `fits: false` and said once.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from rsched.engine.promptfloor import check as floor_check


def _events(home) -> list[dict]:
    path = home / ".control" / "health-events.jsonl"
    if not path.exists():
        return []
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


class _Transcript:
    """Records what the engine wrote — the same surface `chat.js` and `transcript.js` render."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def event(self, kind: str, payload: dict) -> None:
        self.events.append((kind, payload))

    def injections(self) -> list[dict]:
        return [p for k, p in self.events if k == "user_injection"]


def _loop(tmp_path, *, window: int, messages: list[dict], turns: int = 0):
    """A loop-shaped stand-in: the registry answers with a model of `window` tokens."""
    ref = SimpleNamespace(endpoint="ep", model="m", name="m", context_tokens=window,
                          max_tokens=0, multimodal=False)
    ctx = SimpleNamespace(
        routine=SimpleNamespace(dir=tmp_path / "r", slug="victim", models={}, efforts={}),
        server=SimpleNamespace(routines_home=tmp_path),
        registry=SimpleNamespace(for_model=lambda *_a, **_k: (SimpleNamespace(), ref)),
        transcript=_Transcript(), run_id="victim:20261111-140004", depth=0)
    records = [{"turn": n, "kind": "util", "brief": f"call {n}", "say": f"step {n}"}
               for n in range(1, turns + 1)]
    return SimpleNamespace(ctx=ctx, messages=messages, turn_records=records, resume=False)


def _thread(n: int, chars: int) -> list[dict]:
    """A replayed conversation: a system message, then `n` turn messages of `chars` each.

    `boot` replays the whole transcript on a resume (`history.replay_messages`), which is the
    shape the nine-times specimen arrives in: a long thread with a middle to drop.
    """
    return [{"role": "system", "content": "S" * chars},
            *({"role": "assistant" if i % 2 else "user", "content": f"m{i} " * (chars // 4)}
              for i in range(n))]


def _fresh_boot(system_chars: int) -> list[dict]:
    """A FRESH boot: two messages, the composed system prompt and the kickoff. No middle."""
    return [{"role": "system", "content": "S" * system_chars},
            {"role": "user", "content": "go" * 200}]


def test_the_middle_is_dropped_so_the_thread_fits(tmp_path):
    """His answer's first half: *a hard compaction that drops the middle to whatever fits*."""
    (tmp_path / ".control").mkdir(parents=True)
    loop = _loop(tmp_path, window=32_768, messages=_thread(60, 3_000), turns=60)
    head, tail = loop.messages[:6], loop.messages[-24:]
    before = len(loop.messages)

    info = floor_check(loop)

    assert info is not None and info["elided_messages"] > 0
    assert len(loop.messages) < before          # the thread really got smaller
    assert info["after_tokens"] < info["floor_tokens"]
    assert info["fits"] is True
    # the OPENING TASK and the RECENT TURNS are what keep a thread answerable, so they stay
    assert loop.messages[:6] == head
    assert loop.messages[-25:-1] == tail        # the engine note is appended after the tail
    assert "CONTEXT COMPACTED" in loop.messages[6]["content"]


def test_the_loss_is_stated_in_the_prompt_and_on_the_conversation_surface(tmp_path):
    """His answer's load-bearing half: *so the thread continues with a stated loss*.

    The objection to compacting was that a thread whose middle is silently discarded answers
    from a context it no longer has. So the statement must reach BOTH readers: the next turn's
    model (the live prompt) and the person in the conversation (the rendered injection).
    """
    (tmp_path / ".control").mkdir(parents=True)
    loop = _loop(tmp_path, window=32_768, messages=_thread(60, 3_000), turns=60)

    info = floor_check(loop)

    note = loop.messages[-1]
    assert note["role"] == "user" and note["content"].startswith("ENGINE NOTE: ")
    text = note["content"]
    assert "HARD COMPACTION BEFORE TURN ONE" in text
    assert f"{info['elided_messages']} messages are no longer in your context" in text
    assert "32,768-token window" in text
    assert "transcript.jsonl" in text           # where the dropped middle can still be read
    # the SAME text is recorded as the engine-authored injection both pages render
    injected = loop.ctx.transcript.injections()
    assert len(injected) == 1
    assert injected[0]["source"] == "engine"
    assert injected[0]["text"] == text.removeprefix("ENGINE NOTE: ")


def test_the_event_records_what_the_compaction_did(tmp_path):
    """The health event is the surface that says how often this happens — it keeps firing, and
    now carries what was DONE about it rather than that nothing was.
    """
    (tmp_path / ".control").mkdir(parents=True)
    loop = _loop(tmp_path, window=32_768, messages=_thread(60, 3_000), turns=60)

    floor_check(loop)

    rows = [e for e in _events(tmp_path) if e["event"] == "prompt_floor_over_window"]
    assert len(rows) == 1, _events(tmp_path)
    row = rows[0]
    assert row["routine"] == "victim" and row["run_id"] == "victim:20261111-140004"
    # FIELDS, not only prose: "how often, and by how much" is what a sweep must filter on
    assert row["window_tokens"] == 32_768
    assert row["floor_tokens"] > 32_768
    assert row["after_tokens"] < row["floor_tokens"]
    assert row["elided_messages"] > 0
    assert row["fits"] is True
    assert "hard-compacted" in row["detail"] and "32,768" in row["detail"]
    assert "m" in row["detail"]


def test_oversized_bodies_are_trimmed_when_there_is_no_middle(tmp_path):
    """A short thread has no middle to elide (`maybe_compact` returns unchanged below 30
    messages), so the fit falls through to `clamp_to_cap`, which trims the big bodies in place
    with a visible marker. This is F265's path, reused rather than reimplemented.
    """
    (tmp_path / ".control").mkdir(parents=True)
    messages = [{"role": "system", "content": "S" * 1_000},
                {"role": "user", "content": "go"},
                {"role": "user", "content": "O" * 150_000},
                {"role": "user", "content": "P" * 150_000}]
    loop = _loop(tmp_path, window=32_768, messages=messages)

    info = floor_check(loop)

    assert info["elided_messages"] == 0 and info["clamped_messages"] > 0
    assert info["fits"] is True
    assert "elided by window clamp" in loop.messages[2]["content"]
    assert "oversized message bodies were truncated" in loop.messages[-1]["content"]


def test_a_floor_nothing_can_shrink_is_recorded_and_said_without_looping(tmp_path):
    """The incompressible case: a fresh boot is two messages, so there is no middle, and
    `clamp_to_cap` never touches message 0 — the composed system prompt, the largest body in
    every run. Nothing can fit this, which must not become a loop or an exception out of boot:
    the run starts (his answer), `fits: false` records it, and the note says so once.
    """
    (tmp_path / ".control").mkdir(parents=True)
    loop = _loop(tmp_path, window=32_768, messages=_fresh_boot(200_000))

    info = floor_check(loop)

    assert info["fits"] is False
    assert info["elided_messages"] == 0 and info["clamped_messages"] == 0
    # the fit really RAN: its target is the ceiling less the note's own room, which a check
    # that merely reported the floor back could not produce
    assert info["target_tokens"] < info["window_tokens"]
    assert info["after_tokens"] == info["floor_tokens"]   # nothing was shrinkable
    assert "It still does not fit" in loop.messages[-1]["content"]
    assert "the opening prompt alone is over the window" in loop.messages[-1]["content"]
    row = next(e for e in _events(tmp_path) if e["event"] == "prompt_floor_over_window")
    assert row["fits"] is False
    assert "still over the window afterwards" in row["detail"]


def test_the_statement_is_budgeted_for_so_the_compaction_is_not_undone(tmp_path):
    """The note is part of the prompt, so it is part of the budget: compacting to the exact
    ceiling and THEN stating the loss would put the thread back over the window it was just
    fitted to. The target reserves room for it (`_NOTE_TOKENS`).
    """
    from rsched.engine.compaction import estimate_input_tokens
    from rsched.engine.promptfloor import _NOTE_TOKENS

    (tmp_path / ".control").mkdir(parents=True)
    loop = _loop(tmp_path, window=32_768, messages=_thread(60, 3_000), turns=60)

    info = floor_check(loop)

    assert info["target_tokens"] <= info["window_tokens"] - _NOTE_TOKENS
    assert estimate_input_tokens(loop.messages) <= info["window_tokens"]


def test_a_prompt_that_fits_is_left_exactly_as_it_was(tmp_path):
    """Every ordinary run. The layer must cost nothing in the common case — no compaction, no
    note (an engine note here would be the layer talking about itself), and no event, which
    would make the surface's own count meaningless.
    """
    (tmp_path / ".control").mkdir(parents=True)
    loop = _loop(tmp_path, window=200_000, messages=_thread(40, 2_000), turns=40)
    before = [dict(m) for m in loop.messages]

    assert floor_check(loop) is None
    assert loop.messages == before
    assert loop.ctx.transcript.injections() == []
    assert _events(tmp_path) == []


def test_a_window_the_registry_cannot_answer_changes_nothing(tmp_path):
    """No window, no comparison, and above all no compaction: shrinking a thread on a figure
    that could not be resolved would destroy context for no reason.
    """
    (tmp_path / ".control").mkdir(parents=True)
    loop = _loop(tmp_path, window=0, messages=_thread(60, 3_000), turns=60)
    before = [dict(m) for m in loop.messages]

    def boom(*_a, **_k):
        raise RuntimeError("no such role")

    loop.ctx.registry = SimpleNamespace(for_model=boom)
    assert floor_check(loop) is None
    assert loop.messages == before
    assert _events(tmp_path) == []


def test_the_check_never_raises_whatever_it_is_handed(tmp_path):
    """It runs inside boot, before turn one: an exception here would turn the fit into the
    reason a run never starts, which is the opposite of the answer chosen. Best-effort stays —
    what changed is that a compaction which RUNS and fails is recorded (`fits: false`) instead
    of being silently skipped.
    """
    assert floor_check(SimpleNamespace(ctx=None, messages=None)) is None
    assert floor_check(SimpleNamespace()) is None
    assert floor_check(SimpleNamespace(ctx=SimpleNamespace(), messages=[{"content": "x"}])) is None


def test_the_event_is_in_the_documented_vocabulary():
    """An event at a call site but not in the header enum is invisible by construction — the
    `prompt_oversize_shrunk` failure, which tests/test_health_events.py exists to catch. Pinned
    here too, next to the thing that emits it, so the registration is not a separate chore.
    """
    from rsched import health_vocab

    assert "prompt_floor_over_window" in health_vocab.EVENTS
    assert "prompt_floor_over_window:" in (health_vocab.__doc__ or "")
    # the vocabulary must state the CURRENT decision, not the superseded recording-only one
    assert "hard compaction" in (health_vocab.__doc__ or "")
