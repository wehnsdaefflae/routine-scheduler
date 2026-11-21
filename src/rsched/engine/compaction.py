"""COMPACTION — keeping a long run inside its window without losing what it learned.

Two mechanisms, in order of preference: elide the middle behind the engine's one-line-per-turn
digest while a model reorganizes that same middle into `history/` files the run can read back
on demand (so nothing is silently dropped), and failing that clamp oversized message bodies in
place. Both rewrite the message list, which is a deliberate break in the prompt-caching
contract and one of only three places allowed to make it.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path

from .actionschema import brief_value

COMPACT_AT_FRACTION = 0.6

# Once the endpoint demonstrably serves cache hits, carrying context is ~10x cheaper than
# re-reading it uncached — but each compaction rewrites the prefix and invalidates the whole
# cache. The economics flip: compact later.
COMPACT_AT_FRACTION_CACHED = 0.8

KEEP_HEAD_MSGS = 6    # system + kickoff + first 2 turn pairs

KEEP_TAIL_MSGS = 24   # ~ last 12 turn pairs

# Media and text costs are estimates, not provider token counts. UTF-8 bytes avoid
# treating non-ASCII text as cheaply as English. Actual provider usage remains authoritative.
_MEDIA_TOKENS_EST = 1_200

# Minimum UTF-8 body size worth truncating. Smaller bodies are structural messages;
# cutting them would mangle the conversation for little space gain.
_CLAMP_MIN_BODY = 2_000

_CLAMP_MARKER = ("\n\n[… {n} chars elided by window clamp — the full text is in this run's "
                 "transcript.jsonl; read_file the run's history/ if it was archived …]")



def estimate_input_tokens(messages: list[dict]) -> int:
    """Estimate at 3.5 UTF-8 bytes/token plus framing and media; not a tokenizer count."""
    return sum((len(m["content"].encode("utf-8")) * 2 + 6) // 7 + 16
               + _MEDIA_TOKENS_EST * len(m.get("media") or []) for m in messages)


def input_cap_tokens(context_tokens: int, max_output_tokens: int, *, cached: bool) -> float:
    """Compaction trigger in estimated input tokens, with output reserved separately."""
    fraction = COMPACT_AT_FRACTION_CACHED if cached else COMPACT_AT_FRACTION
    return min(fraction * context_tokens, window_ceiling_tokens(context_tokens, max_output_tokens))


def window_ceiling_tokens(context_tokens: int, max_output_tokens: int) -> int:
    """Available input tokens. Input estimates carry their own conservative packing margin."""
    return max(0, context_tokens - max_output_tokens)


def turn_record(turn: int, action: dict) -> dict:
    """One turn as the digest below remembers it once its messages are elided.

    ONE builder for both writers (the live loop and `history.replay_messages`), so a resumed
    digest reads like the live one. The brief is stored WHOLE because the assist predicates read
    a written path off it; `_digest_line` cuts its own copy.
    """
    return {"turn": turn, "kind": action.get("kind", "?"), "brief": brief_value(action),
            "say": action.get("say", "")}


def _digest_line(record: dict) -> str:
    """One elided turn as the compaction digest shows it: the brief cut to 80 characters and
    quoted, the `say` to 120.
    """
    brief = json.dumps(str(record["brief"])[:80], ensure_ascii=False)
    return f'turn {record["turn"]}: {record["kind"]} {brief} — say: "{record["say"][:120]}"'


def maybe_compact(messages: list[dict], turn_records: list[dict], cap_tokens: float
                  ) -> tuple[list[dict], dict | None]:
    """Deterministic compaction against the cap the CALLER decided. Returns
    (messages, compaction_info|None).

    The cap is an argument, never re-derived here: a second, stricter gate would make passes the
    caller triggered do nothing. `boundary.assess` uses a cap of 0 as a dry run.
    """
    if estimate_input_tokens(messages) <= cap_tokens:
        return messages, None
    if len(messages) <= KEEP_HEAD_MSGS + KEEP_TAIL_MSGS:
        return messages, None
    head = messages[:KEEP_HEAD_MSGS]
    tail = messages[-KEEP_TAIL_MSGS:]
    elided = len(messages) - len(head) - len(tail)
    # Digest from turn records whose messages fell in the middle: turns 3 .. N-12.
    first_kept_tail_turn = (max((r["turn"] for r in turn_records), default=0)
                            - KEEP_TAIL_MSGS // 2 + 1)
    lines = [_digest_line(r) for r in turn_records if 2 < r["turn"] < first_kept_tail_turn]
    digest = ("CONTEXT COMPACTED — this replaces the elided middle of the conversation "
              f"({elided} messages). One line per elided turn:\n" + "\n".join(lines))
    new_messages = [*head, {"role": "user", "content": digest}, *tail]
    info = {"elided_messages": elided, "digest_chars": len(digest),
            "before_estimated_tokens": estimate_input_tokens(messages),
            "after_estimated_tokens": estimate_input_tokens(new_messages)}
    return new_messages, info

def clamp_to_cap(messages: list[dict], context_tokens: int, max_output_tokens: int
                 ) -> dict | None:
    """LAST RESORT: bring estimated input below the available token ceiling by truncating
    the largest message bodies in place until the estimate clears the ceiling.

    Eliding the middle cannot shrink the retained head + tail, and a short conversation has no
    middle; when that floor alone exceeds the window, the next completion would fail
    non-retryably. This trims bodies (never message count) with a visible marker; the full text
    stays in the transcript. Returns a clamp-info dict when it trimmed anything, else None.

    Message 0 — the composed system prompt, the largest body in every run — is never a
    candidate: cutting it removes the recipe's contract and rewrites the cached prefix from byte
    zero, and the transcript does not hold it. The kickoff at index 1 stays clampable.
    """
    ceiling = window_ceiling_tokens(context_tokens, max_output_tokens)
    if ceiling <= 0:
        # The output reservation alone fills the window: trimming to 0 would destroy all
        # context, so let the misconfigured request fail loudly at the endpoint.
        return None
    before = estimate_input_tokens(messages)
    if before <= ceiling:
        return None
    # Largest bodies first — each cut buys the most room, so we touch the fewest messages.
    order = sorted(range(1, len(messages)),
                   key=lambda i: len(messages[i]["content"].encode("utf-8")), reverse=True)
    trimmed = 0
    for i in order:
        if estimate_input_tokens(messages) <= ceiling:
            break
        body = messages[i]["content"]
        if len(body.encode("utf-8")) <= _CLAMP_MIN_BODY:
            break   # every remaining body is tiny — nothing worth cutting is left
        overflow = estimate_input_tokens(messages) - int(ceiling)
        # Cut enough from THIS body to clear the overflow (plus the marker's own cost), but
        # never below the floor; the loop revisits if one body wasn't enough.
        # Binary search the longest prefix that meets this message's token budget.
        target = max(0, estimate_input_tokens([messages[i]]) - overflow)
        low = len(body.encode("utf-8")[:_CLAMP_MIN_BODY].decode("utf-8", errors="ignore"))
        high = len(body)
        while low < high:
            mid = (low + high + 1) // 2
            candidate = {**messages[i],
                         "content": body[:mid] + _CLAMP_MARKER.format(n=len(body) - mid)}
            if estimate_input_tokens([candidate]) <= target:
                low = mid
            else:
                high = mid - 1
        keep = low
        if keep >= len(body):
            continue
        elided = len(body) - keep
        messages[i]["content"] = body[:keep] + _CLAMP_MARKER.format(n=elided)
        trimmed += 1
    after = estimate_input_tokens(messages)
    if not trimmed:
        return None
    return {"clamped_messages": trimmed, "before_estimated_tokens": before,
            "after_estimated_tokens": after,
            "ceiling_tokens": int(ceiling)}

# The model supplies the CONTENT and a one-line `about` per file; the ENGINE supplies the
# filenames and therefore writes INDEX.md — a model-written index names files the engine then
# renames, so it would point at files that do not exist. Same split as `.memory/INDEX.md`.
_HISTORY_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["files"],
    "properties": {
        "files": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["name", "about", "content"],
            "properties": {
                "name": {"type": "string", "description": "kebab-case topic name (no extension)"},
                "about": {"type": "string",
                          "description": "ONE line: what this file holds and when to consult "
                                         "it — this becomes its INDEX.md entry"},
                "content": {"type": "string",
                            "description": "markdown, AT MOST ~100 lines — split into more "
                                           "files if longer"}}}},
    },
}

#: An INDEX.md line's shape is engine-written, so carrying entries forward across passes is
#: deterministic.
_INDEX_HEAD = ("# History index — the archived middle of this run, one file per topic.\n"
               "# Read the file whose line matches what you need; the `t<turn>-` prefix is "
               "the turn it was archived at.\n")


def _index_line(name: str, about: str) -> str:
    return f"- `{name}` — {' '.join(str(about).split()) or '(no description)'}"


def parse_index(text: str) -> dict[str, str]:
    """Filename -> description, from an index this engine wrote — `_index_line`'s inverse,
    and the one reader of that shape: the next pass carries entries forward through it and
    history recall (`recall.index_entries`) matches against it.
    """
    out = {}
    for line in text.splitlines():
        if not line.startswith("- `"):
            continue
        name, _, about = line[3:].partition("` — ")
        if name.endswith(".md"):
            out[name] = about.strip()
    return out


def _build_index(hist_dir: Path, prior: str, new: dict[str, str]) -> str:
    """INDEX.md for every file in the archive — carried entries plus this pass's.

    Invariant: EVERY file on disk has exactly one line. Prior entries are carried here, never
    re-fed to the model, which would drop them and pay for a growing prompt.
    """
    known = parse_index(prior)
    lines = []
    for path in sorted(hist_dir.glob("*.md")):
        if path.name == "INDEX.md":
            continue
        if path.name in new:
            about = new[path.name] or "(no description)"
        else:
            about = known.get(path.name) or "(archived in an earlier pass)"
        lines.append(_index_line(path.name, about))
    return _INDEX_HEAD + "\n".join(lines) + "\n"

_HISTORY_PROMPT = """You are archiving the middle of an agent run's conversation so the live context
stays small while NOTHING is lost — the agent will read_file the pieces it needs later.

Reorganize the conversation below into a NAVIGABLE set of markdown files:
- Split it whatever way makes things easiest to find later — chronological, or by task/topic. Each
  file AT MOST ~100 lines; if a part is longer, split it into more files rather than truncating.
- Do NOT summarize heavily. Preserve the actual substance — what was done, decided, found, the key
  observations and outputs — just organized and stripped of obvious noise. The agent navigates to
  what's relevant, so keep the content.
- Give each file an `about`: ONE line saying what it holds and when to consult it. The engine
  builds the index from those lines and owns the filenames, so describe the CONTENT and
  never refer to a file by name — not yours, not another's.

CONVERSATION (the middle turns being archived):
---
{convo}
---
Return ONLY the JSON object {{files: [{{name, about, content}}]}}."""

def _swap_in_history(hist_dir: Path, files: list[dict], turn: int) -> list[str]:
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
        prior = ""
        if (existing := hist_dir / "INDEX.md").is_file():
            prior = existing.read_text(encoding="utf-8")
        described: dict[str, str] = {}
        for f in files:
            raw_name = str(f.get("name", "part")).lower()
            stem = re.sub(r"[^a-z0-9-]+", "-", raw_name).strip("-") or "part"
            name = f"t{turn}-{stem}.md"
            (tmp / name).write_text(str(f["content"]).rstrip() + "\n", encoding="utf-8")
            described[name] = str(f.get("about") or "")
            written.append(name)
        # written LAST, from the temp dir's actual contents: the index can only describe files
        # that are really there, under the names they were really written with
        (tmp / "INDEX.md").write_text(_build_index(tmp, prior, described), encoding="utf-8")
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

#: The `purpose` every archival completion carries — one constant because `archive_middle`
#: stamps it and `archival.settle()` terminates open calls by it; drift would fail silently.
ARCHIVAL_PURPOSE = "Compaction · archival"


def archival_messages(middle: list[dict]) -> list[dict]:
    """The exact archival input, shared by selection fit checks and the completion."""
    convo = "\n\n".join(f"[{m['role']}]\n{m['content']}" for m in middle)
    return [{"role": "user", "content": _HISTORY_PROMPT.format(convo=convo)}]


def archival_fits(middle: list[dict], ref) -> bool:
    """Conservative dedicated-model fit, including prompt, schema and output reserve."""
    messages = archival_messages(middle)
    schema = [{"content": json.dumps(_HISTORY_SCHEMA, ensure_ascii=False)}]
    required = estimate_input_tokens(messages) + estimate_input_tokens(schema) + ref.max_tokens
    return required <= ref.context_tokens * 0.7


def archive_middle(middle: list[dict], endpoint, ref,
                   run_dir: Path, turn: int) -> dict | None:
    """Reorganize `middle` into the navigable on-disk history, and return the info dict.

    Touches no message list, only the model and the filesystem, so it is safe in a thread;
    `engine/archival.py` is the ONLY caller. Returns None when the model gives back nothing
    usable; raises on non-JSON so the caller can report the reason.
    """
    messages = archival_messages(middle)
    # Timeout scales with the middle: 180s base + 60s per 200k chars, capped at the endpoint
    # default (600s) so a hung CLI still dies.
    timeout = min(600, 180 + 60 * (len(messages[0]["content"]) // 200_000))
    comp = endpoint.complete(messages,
                             model=ref.model, schema=_HISTORY_SCHEMA, effort=ref.effort,
                             temperature=ref.temperature, max_tokens=ref.max_tokens,
                             timeout=timeout,
                             purpose=ARCHIVAL_PURPOSE, kind="compaction")
    if comp.parsed is not None:
        data = comp.parsed
    else:
        try:
            data = json.loads(comp.text)
        except ValueError:
            # Name the model and the reply so the event teaches; the caller's deterministic
            # fallback takes the pass.
            raise RuntimeError(
                f"archival model {ref.endpoint}/{ref.model} returned non-JSON "
                f"({len(comp.text or '')} chars: {(comp.text or '')[:80]!r}) — "
                "deterministic compaction takes this pass") from None
    files = [f for f in (data.get("files") or [])
             if isinstance(f, dict) and str(f.get("content", "")).strip()]
    if not files:
        return None
    written = _swap_in_history(run_dir / "history", files, turn)
    return {"elided_messages": len(middle), "history_files": len(written),
            "mode": "llm-history", "model": f"{ref.endpoint}/{ref.model}",
            # the compaction call's own spend — the caller folds it into the run's usage
            "usage": dict(comp.usage)}
