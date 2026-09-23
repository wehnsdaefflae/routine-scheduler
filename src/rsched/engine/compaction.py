"""COMPACTION — keeping a long run inside its window without losing what it learned.

Split out of `history.py` (F393): deriving facts from a finished transcript (which stayed) and
shrinking a LIVE message list are different jobs on different data.

Two mechanisms, in order of preference: archive the middle to `history/` files the run can read
back on demand (the model writes the digest, so nothing is silently dropped), and failing that
clamp oversized message bodies in place. Both rewrite the message list, which is a deliberate
break in the prompt-caching contract and one of only three places allowed to make it.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path

COMPACT_AT_FRACTION = 0.6

# Once the endpoint demonstrably serves cache hits, carrying context is ~10x cheaper than
# re-reading it uncached — but each compaction rewrites the prefix and invalidates the whole
# cache. The economics flip: compact later.
COMPACT_AT_FRACTION_CACHED = 0.8

# ANTICIPATORY COMPACTION. The gate above is a SIZE check and is indifferent to WHERE in the work
# it trips, so it can rewrite the prefix in the middle of a multi-action step — the worst moment for
# both coherence and the cache. At a boundary the engine already detects (the run entering a new
# stage module — `ctx.phase` changing on a `stages/<name>.md` read), a prompt merely APPROACHING the
# gate is archived early, so the clean between-steps pass pre-empts the forced mid-step one. Only
# the TRIGGER moves; every anti-thrash guard still applies, so this can never cause an extra pass
# that the normal gate would not eventually have made anyway.
ANTICIPATE_AT = 0.85

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

def maybe_compact(messages: list[dict], turn_records: list[dict], cap_tokens: float
                  ) -> tuple[list[dict], dict | None]:
    """Deterministic compaction against the cap the CALLER decided. Returns
    (messages, compaction_info|None).

    The cap is an argument, not a constant re-derived here. It used to re-test
    `COMPACT_AT_FRACTION * context_tokens` — a second, stricter gate behind the caller's own —
    so every pass the caller triggered below 60% of the window returned None: uncached
    anticipation (0.51 × window at a stage boundary) and the ">10% of the remaining token
    budget" gate both spent the eviction-warning turn and then archived nothing.
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
    lines = [f'turn {r['turn']}: {r['kind']} {r['brief']} — say: "{r['say'][:120]}"'
             for r in turn_records if 2 < r["turn"] < first_kept_tail_turn]
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
     Compaction (`maybe_compact`, plus the background `archive_middle`) shrinks the prompt by
    ELIDING the middle, but the retained head + tail are an incompressible floor — and a short
    conversation (≤ KEEP_HEAD_MSGS + KEEP_TAIL_MSGS messages) has no middle at all. When that
    floor's own observation bodies exceed the window minus the output reservation, EVERY
    compaction path returns unchanged and the very next completion 400s with
    context_length_exceeded and DIES
    (non-retryable EndpointError). F265 recurred three times this way despite two margin fixes.

    This trims bodies (never message COUNT — structure and roles are preserved) with a visible
    marker; the full text is always on disk in the transcript, so nothing is lost, and the
    marker makes the truncation fail LOUD in the prompt rather than silently. Returns a
    clamp-info dict (for a transcript event) when it trimmed anything, else None.

    Message 0 — the COMPOSED SYSTEM PROMPT — is never a candidate. It is the largest body in
    every run (~90 KB against the 8 KB observation cap), so ordering by size cut it FIRST and
    cut it EVERY pass: token-lab:20260910-073700 clamped one message 90,982 → 30,097 chars on
    turn 1 and re-cut the same message 34 more times. That took the recipe's own contract,
    CAPABILITIES and the STATE DIGEST out of the prompt while the marker pointed at a
    transcript that never carried them (nothing writes the composed prompt to transcript.jsonl),
    and it rewrote the cached prefix from byte zero on every single turn. The kickoff at index 1
    stays clampable: a conversation's pasted document lives there and is legitimately the
    biggest thing in the run.
    """
    ceiling = window_ceiling_tokens(context_tokens, max_output_tokens)
    if ceiling <= 0:
        # Degenerate: the output reservation alone fills the window, so there is no positive
        # input budget to clamp TO — trimming to 0 would just destroy all context. Decline and
        # let the (misconfigured) request fail loudly at the endpoint. Real models never hit
        # this; it only arises in artificial tiny-window unit fixtures.
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

# The model supplies the CONTENT and a one-line description per file; the ENGINE supplies
# the filenames and therefore writes INDEX.md. That split is not tidiness — it is the fix for
# a defect measured on the live instance. The model used to hand back a free-text index it had
# written against its OWN names, and `_swap_in_history` then renamed every file to
# `t<turn>-<slug>.md`: the index was a map to files that did not exist. One archive cited 102
# filenames of which ZERO resolved; 36% of all history reads across the instance returned
# ENOENT, and runs paid a stereotyped three-turn recovery (read INDEX, read the bare names and
# fail, read the prefixed names) over and over. `.memory/INDEX.md` has always worked this way —
# each write supplies `about`, the engine maintains the index — and for exactly this reason.
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

#: An INDEX.md line, and the parser for reading one back. The engine writes every line, so the
#: shape is guaranteed rather than hoped for — which is what makes carrying entries forward
#: across compaction passes deterministic instead of a request the model silently drops.
_INDEX_HEAD = ("# History index — the archived middle of this run, one file per topic.\n"
               "# Read the file whose line matches what you need; the `t<turn>-` prefix is "
               "the turn it was archived at.\n")


def _index_line(name: str, about: str) -> str:
    return f"- `{name}` — {' '.join(str(about).split()) or '(no description)'}"


def _parse_index(text: str) -> dict[str, str]:
    """Filename -> description, from an index this engine wrote."""
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

    The invariant is that EVERY file on disk has exactly one line. The old design asked the
    model to "KEEP its entries and add the new files"; it silently dropped them (one live
    archive lists 13 of its 23 files, an entire generation gone), and re-feeding a growing
    index through the model each pass cost a 20 KB prompt by the 23rd. Carrying them here is
    deterministic and costs nothing.
    """
    known = _parse_index(prior)
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

    Touches no message list, only the model and the filesystem — which is exactly what makes
    it safe to run in a thread, and it always does: `engine/archival.py` is the ONLY caller.
    (There was a synchronous sibling that elided and archived in one blocking call. It was
    the whole path until 0.308.0 moved archival off the hot path, and then it was reachable
    only from its own tests — deleted, and the tests repointed here.) Returns None when the
    model gives back nothing usable; raises when it gives back non-JSON, so the caller can
    report the reason.
    """
    convo = "\n\n".join(f"[{m['role']}]\n{m['content']}" for m in middle)
    # Archival time scales with the middle being read: a fixed 180s died on a 1.25M-char
    # middle (F376) while the digest fallback took the pass every time. 180s base + 60s
    # per 200k chars, capped at the endpoint default (600s) so a hung CLI still dies.
    timeout = min(600, 180 + 60 * (len(convo) // 200_000))
    comp = endpoint.complete(archival_messages(middle),
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
    if not files:
        return None
    written = _swap_in_history(run_dir / "history", files, turn)
    return {"elided_messages": len(middle), "history_files": len(written),
            "mode": "llm-history", "model": f"{ref.endpoint}/{ref.model}",
            # the compaction call's own spend — the caller folds it into the run's usage
            # (this was invisible before: full-context calls that never hit the books)
            "usage": dict(comp.usage)}
