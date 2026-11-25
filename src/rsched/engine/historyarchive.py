"""HISTORY ARCHIVAL — a model reorganizing a run's elided middle into files it can read back.

The second half of what `engine/compaction.py` used to hold, and its own responsibility: that
module decides WHEN a window must shrink and clamps bodies in place, deterministically and with
no model involved. This one is the model-driven path — the schema and prompt an archival model
answers, the index the run reads, and the atomic swap that installs the result — so a change to
the window arithmetic and a change to what the archival model is asked for stop sharing a file.

`engine/archival.py` is the only caller of `archive_middle`; `engine/recall.py` reads the index
and `engine/window.py` asks `archival_fits` whether the middle will fit at all.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path

# The ONE thing the archival path borrows from the window arithmetic: whether the middle it is
# about to send will fit. The dependency runs this way only — compaction never imports back.
from .compaction import estimate_input_tokens

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
    if comp.is_empty():
        # F921: an EMPTY reply is its own failure, and saying "non-JSON" of it sends the next
        # reader looking for a parse bug that is not there.
        raise RuntimeError(
            f"archival model {ref.endpoint}/{ref.model} returned an EMPTY completion "
            f"(stop_reason={comp.stop_reason or 'unreported'}) — deterministic compaction "
            "takes this pass") from None
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
