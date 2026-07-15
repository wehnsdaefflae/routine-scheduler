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
    comp = endpoint.complete([{"role": "user", "content":
                               _HISTORY_PROMPT.format(existing_note=existing_note, convo=convo)}],
                             model=ref.model, schema=_HISTORY_SCHEMA, effort=ref.effort,
                             temperature=ref.temperature, timeout=180,
                             purpose="Compaction · archival", kind="compaction")
    data = comp.parsed if comp.parsed is not None else json.loads(comp.text)
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
    """
    messages: list[dict] = []
    records: list[dict] = []
    last_turn = 0
    for ev in events:
        kind_ev = ev.get("type")
        p = ev.get("payload") or {}
        if kind_ev == "assistant_action":
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
            rendered = (f"COMMAND ERROR: {p.get('error')}" if p.get("kind") == "user_command"
                        else format_observation(p))
            messages.append({"role": "user", "content": rendered})
        elif kind_ev == "user_injection":
            label = ("USER COMMAND (executed directly)" if p.get("command")
                     else "USER MESSAGE (injected mid-run)")
            messages.append({"role": "user", "content": f"{label}: {p.get('text', '')}"})
        elif kind_ev == "answer":
            messages.append({"role": "user", "content": f"ANSWER: {p.get('text', '')}"})
        # header / question / compaction / finish / error / subrun_* are not part of the prompt
    return messages, last_turn, records


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
            started[n] = {"n": n, "label": p.get("label"), "mode": p.get("mode", "parallel")}
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
