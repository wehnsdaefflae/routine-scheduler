"""History RECALL — bringing the archived middle back at the moment it is relevant.

Compaction is lossless: the elided middle goes to `runs/<ts>/history/` as navigable files with
an INDEX.md, and nothing is summarized away. But the archive is *just-in-case* — the run has to
remember it exists and choose to read it — which is the same forgets-to-consult failure the
rest of this layer exists to close. So the archive becomes one more store the relevance trigger
feeds from: when what the run is doing overlaps an archived topic, say so.

The third feeder into the observation tail, and it is not a rule ASSIST for one concrete
reason: an assist's payload is a static line authored in rule frontmatter, and this payload is
COMPUTED — which file, from which turn, matched against what the run just did.

**What this is worth, measured rather than assumed.** Over four real archived runs, scoring
`canon(action)` + the surrounding `say` against the index: at least one useful file in the top
3 for 23% of the moments a run actually went looking, 37% in the top 5. Split by archive size
that average hides the real result — for archives up to ~23 files, 7 of 8 moments hit the top
3; for a 101-file archive, 1 of 22. Deterministic overlap works while the archive is small and
degrades as it grows.

Two things follow. It surfaces ONE file, never a list, and only above a floor score — a wrong
pointer costs a read and teaches the run to ignore the layer, which is worse than silence. And
it is a pointer, never a fetch: the run decides whether the file is worth a turn.

The semantic path (embeddings) is deliberately not here. It would recall better and it pulls in
a dependency this framework otherwise avoids, so it waits behind evidence that the cheap path
is insufficient — which the numbers above will eventually provide, per archive size.
"""

from __future__ import annotations

import re
from pathlib import Path

#: Tokens too common in an action string or an index line to carry any signal.
_STOPWORDS = frozenset({
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with", "from", "at", "by",
    "is", "it", "this", "that", "what", "when", "read", "file", "files", "run", "turn", "md",
    "state", "util", "write", "json", "py", "self", "new", "get", "set", "how", "was", "were",
})
_MIN_TOKEN = 3

#: The floor a match must clear to be worth a line. Tuned against the measurement in the module
#: docstring: below this the top hit is noise more often than not.
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

    Only parses the shape the engine writes — a backticked filename, an em dash, a
    description — which is why the index had to become engine-owned before recall was
    worth building: a model-authored index named files that did not exist, so 36% of
    history reads returned ENOENT and any pointer built on it would have inherited
    exactly that.
    """
    index = Path(hist_dir) / "INDEX.md"
    try:
        text = index.read_text(encoding="utf-8")
    except OSError:
        return []
    out = []
    for line in text.splitlines():
        if not line.startswith("- `"):
            continue
        name, _, about = line[3:].partition("` — ")
        if name.endswith(".md"):
            out.append((name, about.strip()))
    return out


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
