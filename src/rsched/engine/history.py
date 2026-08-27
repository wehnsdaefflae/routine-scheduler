"""Prompt-size management: deterministic compaction, LLM-driven history archival, and
transcript replay for resume.

Compaction shrinks only the in-prompt conversation — the transcript on disk keeps
everything. `compact_to_history` reorganizes the elided middle into navigable markdown
files under runs/<ts>/history/ that the model reads back on demand; `maybe_compact` is
the deterministic one-line-digest fallback when the LLM path fails.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path

from .actions import BRIEF_FIELD
from .observations import format_observation

COMPACT_AT_FRACTION = 0.6
# Once the endpoint demonstrably serves cache hits, carrying context is ~10x cheaper than
# re-reading it uncached — but each compaction rewrites the prefix and invalidates the whole
# cache. The economics flip: compact later.
COMPACT_AT_FRACTION_CACHED = 0.8
KEEP_HEAD_MSGS = 6    # system + kickoff + first 2 turn pairs
KEEP_TAIL_MSGS = 24   # ~ last 12 turn pairs


# Rough context cost of one attached image/PDF (base64 is large and the model tokenizes it),
# so compaction thresholds account for a media-carrying turn rather than counting only its
# short text. The file bytes live on disk, never in `content`.
_MEDIA_SIZE_EST = 4_000


def messages_size(messages: list[dict]) -> int:
    return sum(len(m["content"]) + _MEDIA_SIZE_EST * len(m.get("media") or [])
               for m in messages)


# The codebase's standing approximation: context_chars ≈ 4 × the token window (see
# ModelConfig.context_chars). Used to DERIVE the token window back from the char figure.
CHARS_PER_TOKEN = 4

# Conservative INPUT packing density (chars per token) used to size the HARD window ceiling.
# CHARS_PER_TOKEN=4 is fine for deriving the window, but OPTIMISTIC as an input density: real
# tokenizers pack denser, so a ceiling sized at 4 chars/token maps to MORE input tokens than
# `window − output` allows and the completion 400s. F265 recurred FOUR times this way
# (c-20260802-110156, last on 2026-08-03T04:46 UNDER the deployed 0.149.1 clamp): the clamp
# trimmed the prompt to its char ceiling, but that ceiling's ~183.5k chars packed at the
# payload's real ~3.72 chars/token = 49384 input tokens; + 16384 output = 65768 > the 65536
# window (over by 232 tokens). Three prior fixes tuned a flat margin — the wrong lever, since a
# fixed fraction cannot cover a density error that scales with the payload. Sizing the input
# ceiling at this DENSER figure makes `input_tokens + max_output_tokens ≤ window` hold BY
# CONSTRUCTION for any real content at or above this density, not by a hoped-for margin. It
# only bites small-window models; for large windows the 0.6/0.8 fraction trigger stays binding.
INPUT_CHARS_PER_TOKEN = 3.5


def input_cap_chars(context_chars: int, max_output_tokens: int, *, cached: bool) -> float:
    """The largest in-prompt size (chars) allowed before compaction MUST fire — the lower
    of two ceilings:

    - the fraction-of-window trigger (0.6 uncached / 0.8 cached: compact earlier when every
      turn re-reads at full price, later once the provider serves from cache), and
    - the window MINUS the output reservation. The provider counts the prompt AND the
      requested `max_tokens` output against ONE window, so the input must leave
      `max_output_tokens` of room. Without this second ceiling a small-window model lets
      input grow to `fraction × window` and still requests the full output on top, so
      `input + max_tokens > window` and the completion 400s with context_length_exceeded
      (F265: a 65536-token model reached ~49k input tokens and still requested 16384 output
      → overflow). The reservation only bites models whose window is small enough that
      `fraction × window + max_tokens×4 > window`; for large windows the fraction trigger
      stays the binding one, so behaviour there is unchanged.
    """
    fraction = COMPACT_AT_FRACTION_CACHED if cached else COMPACT_AT_FRACTION
    trigger = fraction * context_chars
    return min(trigger, window_ceiling_chars(context_chars, max_output_tokens))


def window_ceiling_chars(context_chars: int, max_output_tokens: int) -> float:
    """The HARD input ceiling (chars): the largest in-prompt size that still leaves the
    provider room to emit `max_output_tokens` inside the SAME window. Computed in the TOKEN
    domain and converted back to chars at the conservative `INPUT_CHARS_PER_TOKEN` density, so
    the ceiling maps to fewer input tokens than `window − output` for any real content at or
    above that density — `input_tokens + max_output_tokens ≤ window` holds BY CONSTRUCTION, not
    by a hoped-for fractional margin. This is the ceiling compaction MUST get the prompt under;
    `input_cap_chars` compacts earlier still (at the 0.6/0.8 fraction), but when compaction
    CANNOT shrink the prompt — the incompressible head+tail floor, or a conversation too short
    to have a middle to elide — `clamp_to_cap` enforces THIS ceiling as the last resort. F265
    recurred FOUR times (c-20260802-110156, last 2026-08-03T04:46 under the 0.149.1 clamp): the
    clamp trimmed to a char ceiling sized at the optimistic 4 chars/token, but the payload
    packed denser (~3.72) so 49384 input + 16384 output = 65768 > the 65536 window. Sizing the
    input budget at the denser figure removes the density error the flat margin could not cover.
    """
    window_tokens = context_chars / CHARS_PER_TOKEN
    input_token_budget = window_tokens - max_output_tokens
    reserved = input_token_budget * INPUT_CHARS_PER_TOKEN
    return max(0.0, reserved)


def maybe_compact(messages: list[dict], turn_records: list[dict], context_chars: int
                  ) -> tuple[list[dict], dict | None]:
    """Deterministic compaction. Returns (messages, compaction_info|None)."""
    if messages_size(messages) <= COMPACT_AT_FRACTION * context_chars:
        return messages, None
    if len(messages) <= KEEP_HEAD_MSGS + KEEP_TAIL_MSGS:
        return messages, None
    head = messages[:KEEP_HEAD_MSGS]
    tail = messages[-KEEP_TAIL_MSGS:]
    elided = len(messages) - len(head) - len(tail)
    # Digest from turn records whose messages fell in the middle: turns 3 .. N-12.
    first_kept_tail_turn = (max((r["turn"] for r in turn_records), default=0)
                            - KEEP_TAIL_MSGS // 2 + 1)
    lines = [f'turn {r['turn']}: {r['kind']} {r['brief']} — say: "{r['say'][:120]}"'
             for r in turn_records if 2 < r["turn"] < first_kept_tail_turn]
    digest = ("CONTEXT COMPACTED — this replaces the elided middle of the conversation "
              f"({elided} messages). One line per elided turn:\n" + "\n".join(lines))
    new_messages = [*head, {"role": "user", "content": digest}, *tail]
    info = {"elided_messages": elided, "digest_chars": len(digest),
            "before_chars": messages_size(messages), "after_chars": messages_size(new_messages)}
    return new_messages, info


# The smallest body clamp_to_cap will ever truncate. A message under this is already tiny; the
# overflow it is fighting is always a handful of LARGE observation bodies, so trimming below
# this would mangle small structural messages for no space gain.
_CLAMP_MIN_BODY = 2_000
_CLAMP_MARKER = ("\n\n[… {n} chars elided by window clamp — the full text is in this run's "
                 "transcript.jsonl; read_file the run's history/ if it was archived …]")


def clamp_to_cap(messages: list[dict], context_chars: int, max_output_tokens: int
                 ) -> dict | None:
    """LAST RESORT: force the in-prompt size under the hard window ceiling by truncating the
    LARGEST message bodies in place, biggest-first, until the total clears the ceiling.

    Compaction (`maybe_compact` / `compact_to_history`) shrinks the prompt by ELIDING the
    middle, but the retained head + tail are an incompressible floor — and a short conversation
    (≤ KEEP_HEAD_MSGS + KEEP_TAIL_MSGS messages) has no middle at all. When that floor's own
    observation bodies exceed the window minus the output reservation, EVERY compaction path
    returns unchanged and the very next completion 400s with context_length_exceeded and DIES
    (non-retryable EndpointError). F265 recurred three times this way despite two margin fixes.

    This trims bodies (never message COUNT — structure and roles are preserved) with a visible
    marker; the full text is always on disk in the transcript, so nothing is lost, and the
    marker makes the truncation fail LOUD in the prompt rather than silently. Returns a
    clamp-info dict (for a transcript event) when it trimmed anything, else None.
    """
    ceiling = window_ceiling_chars(context_chars, max_output_tokens)
    if ceiling <= 0:
        # Degenerate: the output reservation alone fills the window, so there is no positive
        # input budget to clamp TO — trimming to 0 would just destroy all context. Decline and
        # let the (misconfigured) request fail loudly at the endpoint. Real models never hit
        # this; it only arises in artificial tiny-window unit fixtures.
        return None
    before = messages_size(messages)
    if before <= ceiling:
        return None
    # Largest bodies first — each cut buys the most room, so we touch the fewest messages.
    order = sorted(range(len(messages)),
                   key=lambda i: len(messages[i]["content"]), reverse=True)
    trimmed = 0
    for i in order:
        if messages_size(messages) <= ceiling:
            break
        body = messages[i]["content"]
        if len(body) <= _CLAMP_MIN_BODY:
            break   # every remaining body is tiny — nothing worth cutting is left
        overflow = messages_size(messages) - int(ceiling)
        # Cut enough from THIS body to clear the overflow (plus the marker's own cost), but
        # never below the floor; the loop revisits if one body wasn't enough.
        keep = max(_CLAMP_MIN_BODY, len(body) - overflow - 200)
        if keep >= len(body):
            continue
        elided = len(body) - keep
        messages[i]["content"] = body[:keep] + _CLAMP_MARKER.format(n=elided)
        trimmed += 1
    after = messages_size(messages)
    if not trimmed:
        return None
    return {"clamped_messages": trimmed, "before_chars": before, "after_chars": after,
            "ceiling_chars": int(ceiling)}


_HISTORY_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["files", "index"],
    "properties": {
        "files": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["name", "content"],
            "properties": {
                "name": {"type": "string", "description": "kebab-case topic name (no extension)"},
                "content": {"type": "string",
                            "description": "markdown, AT MOST ~100 lines — split into more "
                                           "files if longer"}}}},
        "index": {"type": "string",
                  "description": "INDEX.md markdown: one line per file — what it holds + "
                                 "when to read it"},
    },
}

_HISTORY_PROMPT = """You are archiving the middle of an agent run's conversation so the live context
stays small while NOTHING is lost — the agent will read_file the pieces it needs later.

Reorganize the conversation below into a NAVIGABLE set of markdown files:
- Split it whatever way makes things easiest to find later — chronological, or by task/topic. Each
  file AT MOST ~100 lines; if a part is longer, split it into more files rather than truncating.
- Do NOT summarize heavily. Preserve the actual substance — what was done, decided, found, the key
  observations and outputs — just organized and stripped of obvious noise. The agent navigates to
  what's relevant, so keep the content.
- Write an INDEX.md listing each file with a one-line description of what it holds and when to
  consult it, so a reader can jump straight to the right file.{existing_note}
CONVERSATION (the middle turns being archived):
---
{convo}
---
Return ONLY the JSON object {{files: [{{name, content}}], index}}."""


def _swap_in_history(hist_dir: Path, files: list[dict], index: str, turn: int) -> list[str]:
    """Build the COMPLETE next history (files carried over from earlier compactions + the new
    ones + INDEX.md) in a sibling temp dir, then swap it into place — a reader or a crash never
    sees a half-written history. Returns the new file names.
    """
    tmp = hist_dir.parent / f".{hist_dir.name}.tmp-{os.getpid()}"
    displaced = hist_dir.parent / f".{hist_dir.name}.out-{os.getpid()}"
    written: list[str] = []
    try:
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True)
        if hist_dir.is_dir():
            for p in sorted(hist_dir.glob("*.md")):
                if p.name != "INDEX.md":
                    shutil.copy2(p, tmp / p.name)   # earlier compactions' files carry over
        for f in files:
            raw_name = str(f.get("name", "part")).lower()
            stem = re.sub(r"[^a-z0-9-]+", "-", raw_name).strip("-") or "part"
            name = f"t{turn}-{stem}.md"
            (tmp / name).write_text(str(f["content"]).rstrip() + "\n", encoding="utf-8")
            written.append(name)
        (tmp / "INDEX.md").write_text(index.rstrip() + "\n", encoding="utf-8")
        shutil.rmtree(displaced, ignore_errors=True)
        if hist_dir.is_dir():
            hist_dir.replace(displaced)
        tmp.replace(hist_dir)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        if not hist_dir.exists() and displaced.is_dir():
            displaced.replace(hist_dir)             # restore the pre-swap history
        raise
    shutil.rmtree(displaced, ignore_errors=True)
    return written


def compact_to_history(messages: list[dict], turn_records: list[dict], endpoint, ref,
                       run_dir: Path, hist_rel: str) -> tuple[list[dict], dict] | None:
    """LLM-driven compaction: reorganize the elided middle into a navigable set of markdown files
    (each ~≤100 lines) under runs/<ts>/history/ + INDEX.md, and replace the middle with a short
    pointer telling the agent to consult the index. Returns (new_messages, info), or None on any
    failure (the caller falls back to the deterministic digest).
    """
    head, tail = messages[:KEEP_HEAD_MSGS], messages[-KEEP_TAIL_MSGS:]
    middle = messages[KEEP_HEAD_MSGS:len(messages) - KEEP_TAIL_MSGS]
    if not middle:
        return None
    hist_dir = run_dir / "history"
    index_md = hist_dir / "INDEX.md"
    prior = index_md.read_text(encoding="utf-8") if index_md.exists() else ""
    existing_note = ("\nThere is already a history index — KEEP its entries and add the new "
                     f"files to it:\n---\n{prior}\n---\n" if prior else "\n")
    convo = "\n\n".join(f"[{m['role']}]\n{m['content']}" for m in middle)
    # Archival time scales with the middle being read: a fixed 180s died on a 1.25M-char
    # middle (F376) while the digest fallback took the pass every time. 180s base + 60s
    # per 200k chars, capped at the endpoint default (600s) so a hung CLI still dies.
    timeout = min(600, 180 + 60 * (len(convo) // 200_000))
    comp = endpoint.complete([{"role": "user", "content":
                               _HISTORY_PROMPT.format(existing_note=existing_note, convo=convo)}],
                             model=ref.model, schema=_HISTORY_SCHEMA, effort=ref.effort,
                             temperature=ref.temperature, max_tokens=ref.max_tokens,
                             timeout=timeout,
                             purpose="Compaction · archival", kind="compaction")
    if comp.parsed is not None:
        data = comp.parsed
    else:
        try:
            data = json.loads(comp.text)
        except ValueError:
            # A weak archival model answering prose (or nothing) instead of the schema
            # used to surface as a bare "Expecting value: line 1 column 1" error event
            # (c-20260810-213335, every message) — name the model and the reply so the
            # event teaches, and let the caller's deterministic fallback take the pass.
            raise RuntimeError(
                f"archival model {ref.endpoint}/{ref.model} returned non-JSON "
                f"({len(comp.text or '')} chars: {(comp.text or '')[:80]!r}) — "
                "deterministic compaction takes this pass") from None
    files = [f for f in (data.get("files") or [])
             if isinstance(f, dict) and str(f.get("content", "")).strip()]
    index = str(data.get("index") or "").strip()
    if not files or not index:
        return None
    turn = max((r["turn"] for r in turn_records), default=0)   # unique prefix per compaction
    written = _swap_in_history(hist_dir, files, index, turn)
    pointer = {"role": "user", "content":
        f"CONTEXT COMPACTED — {len(middle)} earlier messages have been archived to an on-disk, "
        f"navigable history. Read `{hist_rel}/INDEX.md` (read_file) to see what's there, then read "
        f"the specific {hist_rel}/*.md files relevant to your current step. Do not rely on "
        f"memory of the archived turns — consult the index."}
    new_messages = [*head, pointer, *tail]
    info = {"elided_messages": len(middle), "history_files": len(written), "mode": "llm-history",
            "model": f"{ref.endpoint}/{ref.model}",
            "before_chars": messages_size(messages), "after_chars": messages_size(new_messages),
            # the compaction call's own spend — the caller folds it into the run's usage
            # (this was invisible before: full-context calls that never hit the books)
            "usage": dict(comp.usage)}
    return new_messages, info


def replay_messages(events: list[dict]) -> tuple[list[dict], int, list[dict]]:
    """Rebuild the (turn-pair) message list from a run's transcript events — for RESUME. Returns
    (messages, last_turn, turn_records); the caller prepends the freshly-composed system message.
    Every turn is replayed (compaction events are ignored — this reconstitutes the full
    conversation and maybe_compact re-compacts it on the next turn if it's too big).

    Two live message feeds have no 1:1 event: child-exit ANNOUNCEMENTS are reconstituted
    from `subrun_end` events (flushed where the live message sat — before the next
    assistant turn; a child whose summary rode a `wait` observation is not re-announced),
    and blocking-ask answers are NOT replayed from `answer` events — the answer text
    already lives inside the ask_user observation, so replaying both would duplicate it.
    """
    from .control import child_finished_message, render_command_result

    messages: list[dict] = []
    records: list[dict] = []
    last_turn = 0
    # subrun_end events whose announcement has not been placed yet: n → payload
    pending_children: dict[int, dict] = {}

    def flush_children() -> None:
        messages.extend({"role": "user", "content": child_finished_message(
            mode=str(p.get("mode") or "parallel"), n=int(p.get("n") or 0),
            label=str(p.get("label") or ""), workflow=str(p.get("workflow") or ""),
            status=str(p.get("status") or "?"), turns=int(p.get("turns") or 0),
            summary=str(p.get("summary") or ""))} for p in pending_children.values())
        pending_children.clear()

    for ev in events:
        kind_ev = ev.get("type")
        p = ev.get("payload") or {}
        if kind_ev == "assistant_action":
            flush_children()   # live, announcements precede the model's next action
            messages.append({"role": "assistant", "content": json.dumps(p, ensure_ascii=False)})
            turn = ev.get("turn")
            if isinstance(turn, int):
                last_turn = turn
                action_kind = str(p.get("kind") or "")
                brief = str(p.get(BRIEF_FIELD.get(action_kind, ""), ""))[:80]
                records.append({"turn": turn, "kind": p.get("kind", "?"),
                                "brief": json.dumps(brief, ensure_ascii=False),
                                "say": p.get("say", "")})
        elif kind_ev == "observation":
            if p.get("kind") == "wait":
                # a child listed in this wait's finished rows was delivered BY it —
                # replaying an announcement on top would double its summary
                for row in p.get("finished") or []:
                    if isinstance(row, dict) and isinstance(row.get("n"), int):
                        pending_children.pop(row["n"], None)
            rendered = (render_command_result(p) if p.get("user_command")
                        else format_observation(p))
            messages.append({"role": "user", "content": rendered})
        elif kind_ev == "user_injection":
            label = ("USER COMMAND (executed directly)" if p.get("command")
                     else "USER MESSAGE (injected mid-run)")
            messages.append({"role": "user", "content": f"{label}: {p.get('text', '')}"})
        elif kind_ev == "subrun_end":
            n = p.get("n")
            if isinstance(n, int):
                pending_children[n] = p
        elif kind_ev == "finish":
            # the run concluded knowing (or not needing) these — never re-announce after
            pending_children.clear()
        # header / question / answer / compaction / error / subrun_start stay out of the prompt
    flush_children()   # a crash between _collect and the next turn still delivers the result
    return messages, last_turn, records


def cut_index_for_turn(events: list[dict], turn: int) -> int | None:
    """The event index that closes `turn` — its assistant_action, plus the observation that
    answered it when there is one. None when the turn is not in the transcript.

    The one definition of a clean TURN BOUNDARY in a transcript, shared by the D69 rewind
    (which truncates there) and conversation branching (which copies up to there, F325). Both
    need a prefix that replays into paired messages; cutting mid-turn leaves an assistant action
    with no result, which `replay_messages` would hand the model as a dangling turn.
    """
    for i, ev in enumerate(events):
        if ev.get("type") == "assistant_action" and ev.get("turn") == turn:
            if i + 1 < len(events) and events[i + 1].get("type") == "observation":
                return i + 1
            return i
    return None


def rewind_transcript(run_dir: Path, keep_through_turn: int) -> dict | None:
    """D69: rewind a conversation to a chosen turn so a dead/derailed run can be RE-OPENED and
    continued from there instead of being lost. Rewrites runs/<ts>/transcript.jsonl to keep
    every event up to and INCLUDING the assistant_action of `keep_through_turn` and the
    observation that immediately followed it — dropping every later turn (and any trailing
    finish/error). The discarded tail is not destroyed: it is moved to a timestamped
    `rewind-<ts>.jsonl` sibling so the rewind is auditable and reversible by hand.

    A subsequent `runner.resume` on the same run dir replays the truncated transcript, so the
    conversation continues live from the kept point with a fresh budget window. Returns a
    summary dict (kept/dropped counts + archive name), or None when there is nothing to do
    (turn not found, or already the last turn — no tail to drop).
    """
    from ..ids import now_iso
    from ..paths import atomic_write
    from .transcript import read_events

    tpath = run_dir / "transcript.jsonl"
    events, _ = read_events(tpath, 0)
    if not events:
        return None
    cut = cut_index_for_turn(events, keep_through_turn)
    if cut is None or cut >= len(events) - 1:
        return None   # turn not found, or nothing after it to drop
    kept = events[: cut + 1]
    dropped = events[cut + 1 :]
    archive = f"rewind-{now_iso().replace(':', '').replace('-', '')}.jsonl"
    atomic_write(run_dir / archive,
                 "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in dropped))
    atomic_write(tpath,
                 "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in kept))
    return {"kept_events": len(kept), "dropped_events": len(dropped),
            "kept_through_turn": keep_through_turn, "archive": archive}


def orphaned_children(events: list[dict]) -> list[dict]:
    """Children that were RUNNING when the run was interrupted — a `subrun_start` (subtask or
    subrun) with no matching `subrun_end`. Children are threads in the parent process, so they do
    NOT survive a restart: on resume these are dead. Returns [{n, label, mode}] so the engine can
    mark them aborted and tell the model, instead of leaving it to `wait` forever for a child that
    will never finish.
    """
    started: dict[int, dict] = {}
    ended: set[int] = set()
    for ev in events:
        p = ev.get("payload") or {}
        n = p.get("n")
        if not isinstance(n, int):
            continue
        if ev.get("type") == "subrun_start":
            started[n] = {"n": n, "label": p.get("label"), "mode": p.get("mode", "parallel"),
                          "workflow": p.get("workflow", "")}
        elif ev.get("type") == "subrun_end":
            ended.add(n)
    return [info for n, info in started.items() if n not in ended]


def prior_usage(events: list[dict]) -> dict:
    """Token spend recorded across ALL prior legs of a run's transcript. A resume starts a
    fresh budget window (ctx.usage), so without this base status.json under-reports resumed
    runs by however much the earlier legs spent. Sums every event that carries usage:
    assistant actions, llm-subcall observations, and compaction calls.
    """
    total: dict = {"in": 0, "out": 0}
    for ev in events:
        etype = ev.get("type")
        if etype == "assistant_action":
            u = ev.get("usage")
        elif ((etype == "observation" and (ev.get("payload") or {}).get("kind") == "llm")
              or etype == "compaction"):
            u = (ev.get("payload") or {}).get("usage")
        else:
            continue
        if not isinstance(u, dict):
            continue
        for key in ("in", "out", "cached_in", "cache_write"):
            if u.get(key):
                total[key] = total.get(key, 0) + int(u[key])
        if u.get("cost"):
            total["cost"] = round(total.get("cost", 0.0) + float(u["cost"]), 6)
    return total


def seen_paths(events: list[dict]) -> list[str]:
    """Path strings (as the actions gave them) that earlier legs read, viewed, or wrote —
    successful read_file / view_image / write_file / edit_file observations. Rebuilds
    write_file's grounding set on resume, so a file read before an interruption stays
    overwritable after it.
    """
    out: list[str] = []
    for ev in events:
        if ev.get("type") != "observation":
            continue
        p = ev.get("payload") or {}
        kind = p.get("kind")
        if kind in ("read_file", "view_image"):
            entries = p.get("files") or ([p] if p.get("path") else [])
            out.extend(str(f["path"]) for f in entries if f.get("path") and not f.get("error"))
        elif kind in ("write_file", "edit_file") and p.get("path") and not p.get("error"):
            out.append(str(p["path"]))
    return out


# Cumulative per-run telemetry counters mirrored to status.json (RunContext.write_status) and
# the finish/workflow-usage record. Tokens and turns already carry a resume base (usage_base /
# budget_base_turn); these did NOT, so a resumed leg reset them to its own tally — a
# finish→reopen showed utils:{} + asks_deferred:0 despite the pre-finish leg's real activity
# (F131/F132). The GLOBAL util-stats snapshot is transcript-derived and was always correct;
# this only repairs the per-run status.json + finish event.
_RESUME_COUNTER_FIELDS = ("asks_deferred", "schema_retries", "schema_forcefails", "referrals")


def prior_counters(status: dict) -> dict:
    """Cumulative telemetry counters to reseed onto the RunContext on RESUME, read from the
    prior leg's status.json (the run dir is reused across legs). Keeps status.json and the
    finish event cumulative across legs — like usage_base for tokens — instead of resetting
    the util histogram and the integer counters to the resumed leg's own tally. Returns a
    dict of {RunContext attribute: value}; missing or malformed fields are skipped, and the
    util cells are deep-copied so mutating the live ctx never writes back into the read dict.
    """
    out: dict = {}
    utils = status.get("utils")
    if isinstance(utils, dict):
        cells = {k: dict(v) for k, v in utils.items() if isinstance(v, dict)}
        if cells:
            out["util_stats"] = cells
    for fld in _RESUME_COUNTER_FIELDS:
        val = status.get(fld)
        if isinstance(val, int) and not isinstance(val, bool):
            out[fld] = val
    return out
