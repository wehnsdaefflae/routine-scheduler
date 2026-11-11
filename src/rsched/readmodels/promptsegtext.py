"""The TEXT behind a prompt segment — two kinds of answer, and each says which it is.

The operator, looking at the coloured prompt-distribution bar: *"can we click on it to see the
actual prompt?"* When this module was first written the honest answer was "half of it" — the two
GROWN segments are the message list and are on disk, the always-on sections were recorded
nowhere. He then authorized the other half (**"store the always-on section texts ONCE at boot in
the run dir, so the whole bar becomes clickable and truthful"**), so a run booted since
`engine/promptcapture` shipped answers for every segment:

- the GROWN segments (`history`, `observations`) — REPLAYED from the transcript;
- the always-on sections — the leg's COMPOSED text, captured once at boot;
- a run booted BEFORE the capture shipped — "text not recorded", for those sections only.

Three answers, each labelled, because the one outcome worse than no feature is a text presented
as the prompt that is not the prompt.

**Why only half, measured rather than assumed.** `prompt_segments` carries token COUNTS; the
prompt TEXT is persisted nowhere. `engine/boot.py` holds the composer's `(name, text)` tuples on
one line and keeps `len(t)` plus a token estimate — by design, because
`engine/promptsegments.py` is diagnostic-only: the composed prompt is a caching contract, and a
measurement that rewrote one byte of it would invalidate the provider's cache for the rest of the
run.

**And re-composing a section at READ time is unsafe, not merely approximate** — the finding that
decides this module's shape. The code path that builds the digest MUTATES state before composing:
it drains a shared store, touches the job ledger, and writes `answer` transcript events. A GET
endpoint that re-composed a section would do all of that every time someone tapped the bar on a
phone. So the always-on sections are not re-composed here, at any fidelity, with any label.

**What IS honest.** The two GROWN segments are the message list, and the message list is on disk
as the transcript's own `assistant_action` / `observation` / `user_injection` events.
`engine/history.replay_messages` rebuilds it through the SAME renderers the live path used, and
that function's own docstring says why byte identity there is load-bearing: a resumed leg whose
prefix differs by one label or one newline is re-written to the provider at 1.25x instead of
re-read at 0.1x. So this module reads what that function produces and serves it as a RECORDING —
the one text view this engine can offer without lying and without a side effect.

Two caveats travel with it, both from that same docstring, and the view must show them: media is
not re-attached (a replayed observation is marked `media_replayed`), and a blocking-ask answer is
not a message of its own (its text already sits inside the ask observation).

The unit is the engine's own estimate (`compaction.estimate_input_tokens`), which is ADDITIVE per
message — so a row's token figure sums exactly to the segment total the bar already draws, and
the view is auditable against the bar rather than merely adjacent to it.
"""

from __future__ import annotations

from pathlib import Path

from ..engine import promptcapture
from ..engine.compaction import estimate_input_tokens
from ..engine.promptsegments import GROWN_SEGMENTS
from ..engine.transcript import read_events
from . import memo

#: What a row's `preview` is cut to. The rows answer "which message, how big, roughly what"; the
#: body is a second call for one row, because `history` and `observations` run to hundreds of
#: thousands of tokens on a long run and the operator reads this console on a phone.
PREVIEW_CHARS = 400

#: The sentence a segment with no recorded text answers with. It is a refusal, not a hedge: a
#: section labelled as the prompt that is NOT the prompt is worse than no feature, and the
#: reason is worth stating once where someone will read it.
NOT_RECORDED = ("text not recorded — the prompt is measured, never stored. The composed prompt "
                "is a caching contract, so nothing writes a copy of it; re-composing this "
                "section now would drain a shared store and write transcript events, and it "
                "would not be what was sent anyway. The two segments below it (history, "
                "observations) ARE on disk and are replayed verbatim.")

#: What the view says about the two segments it CAN serve — a recording, with its two caveats.
RECORDED = ("what was sent — replayed from this run's transcript through the engine's own "
            "renderers (media is not re-attached; a blocking ask's answer sits inside its own "
            "observation rather than as a message of its own)")

#: What the view says about an always-on section whose text WAS captured at boot. Precise about
#: which prompt it is: the always-on prompt is composed once per leg and re-sent verbatim every
#: turn, so one capture describes every turn of that leg — and a RESUMED leg re-composes and
#: overwrites it, so what is shown is the prompt the current leg is sending, not the first one.
COMPOSED = ("what is being sent — this leg's composed section, recorded once at boot. The "
            "always-on prompt is re-sent verbatim every turn, so this is every turn of this "
            "leg; a resumed leg re-composes it and records the new text")


def _replayed(run_dir: Path) -> list[dict]:
    """The run's message list, rebuilt exactly as a resume rebuilds it, each message carrying the
    transcript turn it was recorded on.

    The turn is READ, never derived: the transcript stamps `turn` on its events, and the bar is
    one snapshot of the latest measurement, so "which turn" has no honest answer except the one
    the record carries.

    **Matched PER ROLE, which is what makes the assistant side exact.** Only some events produce
    a prompt message — a header, a question, a compaction produce none — so pairing messages
    against the event list positionally is wrong, and wrong by a shifting offset (my first
    version reported turn 1 for turn 2 and turn 0 for turn 1). An `assistant` message comes from
    exactly one event kind, `assistant_action`, in order: so the n-th assistant message IS the
    n-th such event, and its turn is read off it. The `user` side draws from its own queue of
    the kinds that produce a user message (an observation, an injection, an engine note, a
    child-exit announcement). That queue can run one short where the replay synthesizes a
    message from several events, and the remaining rows then carry the last turn seen rather
    than a wrong one — stated here because a turn stamp a reader trusts must not be a guess.
    """
    from ..engine import history

    events, _ = read_events(run_dir / "transcript.jsonl")
    queues: dict[str, list[int]] = {"assistant": [], "user": []}
    last = 0
    for ev in events:
        if isinstance(ev.get("turn"), int):
            last = int(ev["turn"])
        kind = str(ev.get("type") or "")
        if kind == "assistant_action":
            queues["assistant"].append(last)
        elif kind in ("observation", "user_injection", "subrun_end"):
            queues["user"].append(last)
    messages, _last_turn, _records = history.replay_messages(events)
    seen = {"assistant": 0, "user": 0}
    out: list[dict] = []
    for msg in messages:
        role = str(msg.get("role") or "")
        queue = queues.get(role) or []
        i = seen.get(role, 0)
        turn = queue[i] if i < len(queue) else last
        seen[role] = i + 1
        out.append({"turn": turn, "role": role, "content": str(msg.get("content") or "")})
    return out


def _rows(run_dir: Path) -> dict[str, list[dict]]:
    """Every grown segment's rows, newest first, keyed by the segment name the BAR uses.

    One parse per transcript change serves both the row list and a body read, which is what the
    memo below is for: the rail polls this endpoint while a run is live.
    """
    by_segment: dict[str, list[dict]] = {name: [] for name in GROWN_SEGMENTS.values()}
    for i, msg in enumerate(_replayed(run_dir)):
        segment = GROWN_SEGMENTS.get(msg["role"])
        if segment is None:
            continue
        text = msg["content"]
        by_segment[segment].append({
            "i": i, "turn": msg["turn"], "chars": len(text),
            "tokens": estimate_input_tokens([{"content": text}]),
            "preview": text[:PREVIEW_CHARS],
            "truncated": len(text) > PREVIEW_CHARS})
    return {name: list(reversed(rows)) for name, rows in by_segment.items()}


def segment_rows(run_dir: Path) -> dict[str, list[dict]]:
    """`_rows`, memoized on the run tree's transcript fingerprints."""
    return memo.memoized(f"prompt-seg-text:{run_dir}", memo.transcript_paths(run_dir),
                         lambda: _rows(run_dir))


def page(run_dir: Path, name: str, *, offset: int = 0, limit: int = 50) -> dict:
    """One segment's rows, paged — or, for an always-on section, its captured text.

    `total` and `tokens` are the whole segment's, not the page's: the figure a reader compares
    against the bar must not change with the page they are on.
    """
    if name not in set(GROWN_SEGMENTS.values()):
        captured = promptcapture.read(run_dir).get(name)
        if captured is None:
            return {"segment": name, "recorded": False, "note": NOT_RECORDED,
                    "rows": [], "total": 0, "tokens": 0}
        return {"segment": name, "recorded": True, "note": COMPOSED,
                "rows": [{"i": 0, "turn": 0, "chars": captured["chars"],
                          "tokens": estimate_input_tokens([{"content": captured["text"]}]),
                          "preview": captured["text"][:PREVIEW_CHARS],
                          "truncated": captured["chars"] > PREVIEW_CHARS}],
                "offset": 0, "total": 1,
                "tokens": estimate_input_tokens([{"content": captured["text"]}])}
    rows = segment_rows(run_dir).get(name) or []
    start = max(0, int(offset))
    return {"segment": name, "recorded": True, "note": RECORDED,
            "rows": rows[start:start + max(1, int(limit))],
            "offset": start, "total": len(rows),
            "tokens": sum(int(r["tokens"]) for r in rows)}


def body(run_dir: Path, name: str, index: int) -> dict:
    """ONE row's full text — the second call a click on a row makes.

    Identified by the row's `i`, the message's position in the replayed list, because that is
    stable for a finished run and monotonic for a live one: a turn number is not unique (several
    messages share a turn) and a hash of the text would make the view's own link depend on the
    text it is fetching.
    """
    if name not in set(GROWN_SEGMENTS.values()):
        captured = promptcapture.read(run_dir).get(name)
        if captured is None:
            return {"segment": name, "recorded": False, "note": NOT_RECORDED, "text": ""}
        return {"segment": name, "recorded": True, "note": COMPOSED, "i": 0, "turn": 0,
                "tokens": estimate_input_tokens([{"content": captured["text"]}]),
                "truncated": captured["truncated"], "text": captured["text"]}
    row = next((r for r in (segment_rows(run_dir).get(name) or []) if int(r["i"]) == int(index)),
               None)
    if row is None:
        return {"segment": name, "recorded": True, "note": RECORDED, "text": "",
                "missing": True}
    messages = _replayed(run_dir)
    text = messages[int(index)]["content"] if int(index) < len(messages) else ""
    return {"segment": name, "recorded": True, "note": RECORDED, "i": int(index),
            "turn": row["turn"], "tokens": row["tokens"], "text": text}
