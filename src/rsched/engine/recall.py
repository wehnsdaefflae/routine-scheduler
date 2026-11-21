"""History RECALL — bringing the archived middle back at the moment it is relevant.

Compaction archives the elided middle to `runs/<ts>/history/` (navigable files + INDEX.md), but
a run has to remember the archive exists. So when what the run is doing overlaps an archived
topic, the observation tail says so. Not a rule ASSIST: this payload is COMPUTED (which file,
from which turn, matched against what the run just did).

Deterministic token overlap works while the archive is small and degrades as it grows. Hence it
surfaces ONE file, only above a floor score — a wrong pointer teaches the run to ignore the
layer — and it is a pointer, never a fetch. Embeddings are deliberately not used (a dependency
this framework otherwise avoids) until the cheap path proves insufficient.
"""

from __future__ import annotations

import re
from pathlib import Path

from .compaction import parse_index

#: Tokens too common in an action string or an index line to carry any signal.
_STOPWORDS = frozenset({
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with", "from", "at", "by",
    "is", "it", "this", "that", "what", "when", "read", "file", "files", "run", "turn", "md",
    "state", "util", "write", "json", "py", "self", "new", "get", "set", "how", "was", "were",
})
_MIN_TOKEN = 3

#: The floor a match must clear to be worth a line: below it the top hit is mostly noise.
_MIN_SCORE = 3.0
#: The filename stem is a topic the archival model CHOSE, so a hit there is worth more than one
#: in the body of its description.
_NAME_WEIGHT = 3.0
#: Turns between pointers. The archive does not change within a run except at a compaction, so
#: a run working one topic should be told once, not on every observation.
_COOLDOWN = 8


def configure(loop) -> None:
    """This layer's run state: which archived files have been named, and the cooldown."""
    loop._recalled = set()
    loop._recall_after = 0


def _tokens(text: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", str(text).lower())
            if len(t) >= _MIN_TOKEN and t not in _STOPWORDS}


def index_entries(hist_dir: Path) -> list[tuple[str, str]]:
    """(filename, description) for every line of an engine-written INDEX.md.

    Only parses the shape the engine writes, via the same `compaction.parse_index` that carries
    entries forward — the index being engine-owned is what makes its filenames trustworthy.
    """
    try:
        text = (Path(hist_dir) / "INDEX.md").read_text(encoding="utf-8")
    except OSError:
        return []
    return list(parse_index(text).items())


def _score(entry: tuple[str, str], subject: set[str]) -> float:
    name, about = entry
    stem = name[:-3]
    stem = stem.split("-", 1)[1] if re.match(r"^t\d+-", stem) else stem
    return (_NAME_WEIGHT * len(_tokens(stem) & subject)) + len(_tokens(about) & subject)


def best_match(entries: list[tuple[str, str]], subject: set[str],
               seen: set[str]) -> tuple[str, str] | None:
    """The one archived file most worth naming for this subject, or None."""
    ranked = [(_score(e, subject), e) for e in entries if e[0] not in seen]
    if not ranked:
        return None
    score, entry = max(ranked, key=lambda pair: pair[0])
    return entry if score >= _MIN_SCORE else None


def at_observation(loop, action: dict, obs: dict) -> str:
    """The tail naming an archived file worth reading — "" when nothing is worth naming.

    Costs no turn: it rides the observation the run was getting anyway, and names a file the
    run may or may not choose to open.
    """
    if not loop._history_active or loop.ctx.turn < loop._recall_after:
        return ""
    entries = index_entries(loop.ctx.run_dir / "history")
    if not entries:
        return ""
    from .actionschema import canon

    subject = _tokens(canon(action)) | _tokens(action.get("say", "")) | _tokens(
        obs.get("kind", ""))
    match = best_match(entries, subject, loop._recalled)
    if match is None:
        return ""
    name, about = match
    loop._recalled.add(name)
    loop._recall_after = loop.ctx.turn + _COOLDOWN
    turn = re.match(r"^t(\d+)-", name)
    where = f" (archived at turn {turn.group(1)})" if turn else ""
    return (f"\n[HISTORY: you archived this{where} — `{loop._hist_rel}/{name}`: {about}. "
            "read_file it if you need the detail; the live context no longer has it.]")
