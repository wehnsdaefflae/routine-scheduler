"""Search sources: which files feed the instance-wide index, and what searchable prose
comes out of each. Pure functions over the two homes — no sqlite, no state here.

The unit of (re)indexing is the FILE: each source file maps to a list of doc rows, so
the indexer can fingerprint, reindex, and prune per file (registry.py's stat-memo model).
Indexed surfaces: run transcripts (gz included, subrun trees included — model-authored
say/note/finish prose, questions + answers, user messages), result.md, compaction
history/ archives, LEDGER.md, .memory/ notes, durable decision records
(questions/pending), and recipe files (main.md / stages / traits / instruction.md).
NOT indexed: config (routine.yaml, tuning.yaml, server config), state/, inbox/
(transient), artifacts/attachments (deliverables, often binary), and secrets — the
index never sees them, so it can never leak them.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from ..config import ServerConfig
from ..engine.transcript import read_events
from ..paths import read_json

# One doc row = one searchable unit of prose. `kind` is the search-result vocabulary
# (rendered as a chip in the UI) — extend, never repurpose.
DOC_KINDS = ("say", "note", "finish", "question", "answer", "user_message", "decision",
             "ledger", "memory", "recipe", "instruction", "history", "result")

# A single text file becomes multiple docs chunked at paragraph boundaries: nothing is
# dropped from a long LEDGER, and snippets stay anchored near their match.
CHUNK_CHARS = 8_000


@dataclass(frozen=True)
class SourceFile:
    """One indexable file plus the metadata every doc extracted from it inherits."""

    path: Path
    home: str            # "routine" | "conversation"
    slug: str
    run_ts: str = ""     # "" = routine-level file (ledger, memory, recipe, …)
    sub: str = ""        # subrun path ("2" or "2/1"), "" = the top-level run
    kind: str = ""       # doc kind for plain files; "transcript" for event files


@dataclass(frozen=True)
class Doc:
    """One searchable unit of prose extracted from a source file."""

    kind: str
    text: str
    turn: int | None = None
    phase: str = ""
    ts: str = ""         # event timestamp (ISO) or the decision's asked stamp


def iter_sources(server: ServerConfig) -> Iterator[SourceFile]:
    """Every indexable file across the routines AND conversations homes, in a stable
    order. Dot-dirs (`.control`, `.wizard-*`) are skipped like the registry skips them;
    background_home is transient by design (dirs are deleted after delivery) and its
    results land in the owner conversation's transcript, so it is not walked.
    """
    for home_kind, home in (("routine", server.routines_home),
                            ("conversation", server.conversations_home)):
        if not home.is_dir():
            continue
        for d in sorted(home.iterdir()):
            if not d.is_dir() or d.name.startswith("."):
                continue
            if not (d / "routine.yaml").exists():
                continue
            yield from _routine_sources(home_kind, d)


def _md_files(directory: Path) -> list[Path]:
    return sorted(directory.glob("*.md")) if directory.is_dir() else []


def _routine_sources(home_kind: str, d: Path) -> Iterator[SourceFile]:
    slug = d.name
    for name, kind in (("LEDGER.md", "ledger"), ("instruction.md", "instruction"),
                       ("main.md", "recipe")):
        if (d / name).is_file():
            yield SourceFile(d / name, home_kind, slug, kind=kind)
    for subdir in ("stages", "steps", "traits"):   # steps/ is the pre-0.49 stage dir name
        for p in _md_files(d / subdir):
            yield SourceFile(p, home_kind, slug, kind="recipe")
    for p in _md_files(d / ".memory"):
        if p.name != "INDEX.md":   # the index is derived from the notes it points at
            yield SourceFile(p, home_kind, slug, kind="memory")
    pending = d / "questions" / "pending"
    if pending.is_dir():
        for p in sorted(pending.glob("*.json")):
            yield SourceFile(p, home_kind, slug, kind="decision")
    runs = d / "runs"
    if runs.is_dir():
        for run_dir in sorted(p for p in runs.iterdir() if p.is_dir()):
            yield from _run_sources(home_kind, slug, run_dir)


def _run_sources(home_kind: str, slug: str, run_dir: Path) -> Iterator[SourceFile]:
    ts = run_dir.name
    yield from _transcript_tree(home_kind, slug, run_dir, ts, sub="")
    if (run_dir / "result.md").is_file():
        yield SourceFile(run_dir / "result.md", home_kind, slug, ts, kind="result")
    for p in _md_files(run_dir / "history"):
        yield SourceFile(p, home_kind, slug, ts, kind="history")


def _transcript_tree(home_kind: str, slug: str, run_dir: Path, ts: str,
                     sub: str) -> Iterator[SourceFile]:
    """This run level's transcript (plain or retention-gzipped — never both on disk)
    plus, recursively, every subrun's under sub/<n>/.
    """
    for name in ("transcript.jsonl", "transcript.jsonl.gz"):
        if (run_dir / name).is_file():
            yield SourceFile(run_dir / name, home_kind, slug, ts, sub=sub, kind="transcript")
            break
    subdir = run_dir / "sub"
    if subdir.is_dir():
        for child in sorted((p for p in subdir.iterdir()
                             if p.is_dir() and p.name.isdigit()), key=lambda p: int(p.name)):
            child_sub = f"{sub}/{child.name}" if sub else child.name
            yield from _transcript_tree(home_kind, slug, child, ts, sub=child_sub)


def extract(src: SourceFile) -> list[Doc]:
    """The searchable docs inside one source file. Never raises on unreadable or
    malformed content — a vanished/broken file simply yields nothing this pass and the
    fingerprint diff retries it on the next one.
    """
    if src.kind == "transcript":
        return _extract_transcript(src.path)
    if src.kind == "decision":
        return _extract_decision(src.path)
    return _extract_text(src)


def _extract_text(src: SourceFile) -> list[Doc]:
    try:
        text = src.path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return []
    return [Doc(kind=src.kind, text=chunk) for chunk in _chunks(text)]


def _chunks(text: str) -> list[str]:
    """Split long prose at paragraph boundaries so every part of a big file stays
    searchable and snippets anchor near their match.
    """
    if len(text) <= CHUNK_CHARS:
        return [text] if text else []
    out: list[str] = []
    buf: list[str] = []
    size = 0
    for para in text.split("\n\n"):
        if size + len(para) > CHUNK_CHARS and buf:
            out.append("\n\n".join(buf))
            buf, size = [], 0
        buf.append(para)
        size += len(para) + 2
    if buf:
        out.append("\n\n".join(buf))
    return [c for c in out if c.strip()]


def _extract_decision(path: Path) -> list[Doc]:
    """A durable decision record (questions/pending/<qid>.json — the ONE record shape
    asks and util approvals share): question + options + default, one doc.
    """
    rec = read_json(path)
    if not isinstance(rec, dict):
        return []
    parts = [str(rec.get("question") or "")]
    parts += [str(o) for o in rec.get("options") or [] if str(o)]
    if rec.get("default"):
        parts.append(f"default: {rec['default']}")
    text = "\n".join(p for p in parts if p).strip()
    return [Doc(kind="decision", text=text, ts=str(rec.get("asked") or ""))] if text else []


@dataclass
class _EventDocs:
    """Accumulates docs from one transcript's events (keeps the per-event dispatch flat)."""

    docs: list[Doc] = field(default_factory=list)

    def add(self, kind: str, value: object, turn: int | None, phase: str, ts: str) -> None:
        text = str(value or "").strip()
        if text:
            self.docs.append(Doc(kind=kind, text=text, turn=turn, phase=phase, ts=ts))


def _extract_transcript(path: Path) -> list[Doc]:
    """Model-authored prose + the user's side of one transcript. Deliberately NOT the
    observations: they carry file contents and tool output wholesale — bulky, derivative,
    and where leaked secrets would live. The prose channels are what "which run mentioned
    X?" means.
    """
    events, _ = read_events(path)
    acc = _EventDocs()
    for ev in events:
        payload = ev.get("payload") or {}
        if not isinstance(payload, dict):
            continue
        turn = ev.get("turn") if isinstance(ev.get("turn"), int) else None
        phase = str(ev.get("phase") or "")
        ts = str(ev.get("ts") or "")
        etype = ev.get("type")
        if etype == "assistant_action":
            acc.add("say", payload.get("say"), turn, phase, ts)
            acc.add("note", payload.get("note"), turn, phase, ts)
        elif etype == "finish":
            acc.add("finish", payload.get("summary"), turn, phase, ts)
        elif etype == "question":
            acc.add("question", payload.get("question"), turn, phase, ts)
        elif etype == "answer":
            acc.add("answer", payload.get("text"), turn, phase, ts)
        elif etype == "user_injection" and payload.get("source") != "engine":
            acc.add("user_message", payload.get("text"), turn, phase, ts)
    return acc.docs
