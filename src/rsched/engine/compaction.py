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

# Rough context cost of one attached image/PDF (base64 is large and the model tokenizes it),
# so compaction thresholds account for a media-carrying turn rather than counting only its
# short text. The file bytes live on disk, never in `content`.
_MEDIA_SIZE_EST = 4_000

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

# The smallest body clamp_to_cap will ever truncate. A message under this is already tiny; the
# overflow it is fighting is always a handful of LARGE observation bodies, so trimming below
# this would mangle small structural messages for no space gain.
_CLAMP_MIN_BODY = 2_000

_CLAMP_MARKER = ("\n\n[… {n} chars elided by window clamp — the full text is in this run's "
                 "transcript.jsonl; read_file the run's history/ if it was archived …]")



def messages_size(messages: list[dict]) -> int:
    return sum(len(m["content"]) + _MEDIA_SIZE_EST * len(m.get("media") or [])
               for m in messages)

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
    convo = "\n\n".join(f"[{m['role']}]\n{m['content']}" for m in middle)
    # Archival time scales with the middle being read: a fixed 180s died on a 1.25M-char
    # middle (F376) while the digest fallback took the pass every time. 180s base + 60s
    # per 200k chars, capped at the endpoint default (600s) so a hung CLI still dies.
    timeout = min(600, 180 + 60 * (len(convo) // 200_000))
    comp = endpoint.complete([{"role": "user", "content":
                               _HISTORY_PROMPT.format(convo=convo)}],
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
    turn = max((r["turn"] for r in turn_records), default=0)   # unique prefix per compaction
    written = _swap_in_history(hist_dir, files, turn)
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
