"""LEDGER.md — a routine's PROCESS RECORD, written by the engine and never by a run (P1).

A routine's `LEDGER.md` holds one entry per finished depth-0 run leg. The ENGINE writes it, at
the close-out every ending shares (`engine/loopend.finish_run` → `engine/ledgerentry.py`), from
what the run already produced — its finish summary, its accounting, its `RECIPE REVISION:`
lines. A run spends no turn on it, so the record cannot be skipped by a hurried run, padded by a
diligent one, or rotated by hand.

Why the engine and not the model (measured 2026-11-06, 24 routines, ~10,500 hand-classified
turns): twelve routines spent more turns on bookkeeping than on their task, and writing,
re-reading and byte-rotating this one file by hand was a large share of it in at least eight. A
`decision-record` rule assist and the recipe generator both asked for the write, so a recipe that
said "no ledger" was overruled by the engine's own voice. The record a later run needs — what
this run did, chose and rejected — is exactly the finish summary's `**Process:**` part, which
every run already writes once.

It is SEALED like `.memory/INDEX.md`: the file-action write gate refuses a model's write, edit,
delete or move of it (`engine/storeseal.py`, with `REFUSAL` below as the observation) and the
Landlock own-dir seal withholds it and the archive from every jailed subprocess
(`ownseal.SEALED_FILES` / `SEALED_DIRS` import these names), so a shell heredoc cannot write it
either. The operator's file endpoint refuses it by name (`web/api_routine_files`). The engine
writes it in-process, outside any jail.

## Entry shape

    ### <run_ts> · <status> · <turns> turns              (a resumed leg: `· leg <n> ·` after ts)
    <the finish summary, verbatim — result.md's text>

    Accounting:
    - <one line per accounting entry the finish carried>

    Recipe revisions:
    - <one line per `RECIPE REVISION:` line the summary named>

The two trailing blocks appear only when non-empty. An entry BOUNDARY is a heading of exactly
that shape (`HEADING`), not any `### ` line: a summary may carry Markdown headings of its own,
and a body line that happens to match the heading pattern is indented one space on the way in,
so an entry is always one contiguous block that rotation moves whole.

## The PREAMBLE is never touched

Everything above the first engine heading is the file's PREAMBLE, and the engine only ever
appends below it and rotates entries out from below it — it never moves, edits or re-reads it as
entries. That is where the title line lives, and also what a routine kept in the file before the
engine took it over: birthday-admin keeps its frozen brief there, and every live ledger on
2026-11-06 carried hand-written history in a dozen shapes (`## ` and `### ` entries, `### `
sub-headings inside them, one 25 KB file with no heading at all) — none of it in the engine's
heading shape. Telling those sections apart would be guessing, so they stay where they are,
whole, as the head of the file; the digest shows only the engine's entries
(`digest_tail`), so a long preamble costs no run a token.

## Rotation — engine-owned, by bytes

When the ENTRIES pass `MAX_BYTES` the OLDEST whole entries move to `.ledger/<YYYY-MM>.md` (the
month of each entry's own run stamp), until what stays is at most `KEEP_BYTES` — never fewer than
the newest entry. Only the entries are measured: a preamble the engine may not move must not
trigger a rotation it cannot relieve. 32 KiB is ~8 k tokens: the most a run that reads every
entry should pay, and above every live entry measured (a long summary is 2-5 KiB). The 16 KiB
target is the hysteresis that keeps rotation from running on every finish — it runs once per
several runs and moves several entries each time. Bytes, not entries or lines: entries grow, so a
count lets the file swell unseen (the lesson the `decision-record` rule used to teach by hand).
The archive is written BEFORE the ledger is shortened, so a crash between the two duplicates an
entry rather than losing one.
"""

from __future__ import annotations

import re
from pathlib import Path

from .paths import atomic_write

FILE = "LEDGER.md"
#: The engine-owned archive, one Markdown file per month (`.ledger/2026-11.md`).
ARCHIVE_DIR = ".ledger"
#: What the ledger may hold before rotation, and what rotation shortens it to (module docstring).
MAX_BYTES = 32 * 1024
KEEP_BYTES = 16 * 1024
#: An entry's heading — and therefore its boundary. Group 1 is the run stamp (`ids.run_ts`).
HEADING = re.compile(r"^### (\d{8}-\d{6}) · ")

REFUSAL = ("LEDGER.md is the engine's: it writes one entry per finished run from your finish "
           "summary, with your accounting and RECIPE REVISION lines. Put what you would record "
           "there in your finish summary (its **Process:** part), or in a `note` on any action. "
           "Nothing was written.")
ARCHIVE_REFUSAL = (f"{ARCHIVE_DIR}/ is the engine's archive of older LEDGER.md entries — "
                   "read_file it; the engine moves entries there itself. Nothing was written.")


def split(text: str) -> tuple[str, list[str]]:
    """`(preamble, entries)`: the text before the first entry heading, and each entry as one
    block from its heading up to the next one. Joining them back gives `text` exactly.
    """
    preamble: list[str] = []
    entries: list[list[str]] = []
    # split on the newline byte alone (`str.splitlines` also breaks on U+2028, which a summary
    # may carry raw), keeping each line's own newline so the blocks rejoin to `text` exactly
    for line in re.findall(r"[^\n]*\n|[^\n]+$", text):
        if HEADING.match(line):
            entries.append([line])
        elif entries:
            entries[-1].append(line)
        else:
            preamble.append(line)
    return "".join(preamble), ["".join(e) for e in entries]


def guard_body(text: str) -> str:
    """`text` with every line that would read as an entry heading indented one space."""
    return "\n".join(f" {ln}" if HEADING.match(ln) else ln for ln in text.split("\n"))


def run_ts_of(entry: str) -> str:
    """The run stamp an entry's heading names ("" for a block that is not an entry)."""
    m = HEADING.match(entry)
    return m.group(1) if m else ""


def _month(entry: str) -> str:
    ts = run_ts_of(entry)
    return f"{ts[:4]}-{ts[4:6]}"


def append(routine_dir: Path, entry: str, *, title: str) -> list[str]:
    """Append one entry (heading first) and rotate if the entries outgrew `MAX_BYTES`; the
    preamble is kept as written. A missing ledger is created with a `# LEDGER — <title>`
    line. Returns the archive files rotation wrote, relative to `routine_dir` (empty when
    nothing moved).
    """
    path = routine_dir / FILE
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        text = f"# LEDGER — {title}\n\n"
    text = text.rstrip("\n") + "\n\n" if text.strip() else text   # one blank line above it
    text += entry.rstrip("\n") + "\n\n"
    preamble, entries = split(text)
    moved: dict[str, list[str]] = {}
    size = sum(len(e.encode("utf-8")) for e in entries)
    if size > MAX_BYTES:
        while len(entries) > 1 and size > KEEP_BYTES:
            oldest = entries.pop(0)
            size -= len(oldest.encode("utf-8"))
            moved.setdefault(_month(oldest), []).append(oldest)
    written: list[str] = []
    for month, chunk in moved.items():
        target = routine_dir / ARCHIVE_DIR / f"{month}.md"
        target.parent.mkdir(exist_ok=True)
        try:
            prior = target.read_text(encoding="utf-8")
        except FileNotFoundError:
            prior = f"# LEDGER archive — {title}, {month}\n\n"
        atomic_write(target, prior + "".join(chunk), mode=_mode(target))
        written.append(f"{ARCHIVE_DIR}/{month}.md")
    atomic_write(path, preamble + "".join(entries), mode=_mode(path))
    return written


def _mode(path: Path) -> int:
    """The file's own permission bits, kept across the rewrite; a new file gets 0644 — the bits
    the scaffold's `write_text` gave every ledger before the engine wrote it (a 0600 default
    would be the one file in the routine dir its readers' umask did not decide).
    """
    try:
        return path.stat().st_mode & 0o777
    except FileNotFoundError:
        return 0o644


def digest_tail(routine_dir: Path, *, skip_run: str = "", max_lines: int = 30) -> str:
    """The ledger section of a run's state digest, or "" when there is nothing earlier to show.

    The newest entry is dropped when it is `skip_run`'s — the run whose `result.md` the digest
    already prints in full as "Last run result" — so the same summary is not paid for twice on
    every turn. What remains is the tail of the remaining entries, at most `max_lines` lines.
    """
    try:
        _pre, entries = split((routine_dir / FILE).read_text(encoding="utf-8"))
    except OSError:
        return ""
    if entries and skip_run and run_ts_of(entries[-1]) == skip_run:
        entries = entries[:-1]
    if not entries:
        return ""
    lines = "".join(entries).rstrip("\n").splitlines()
    cut = len(lines) > max_lines
    n = f"{len(entries)} entr{'y' if len(entries) == 1 else 'ies'}"
    head = (f"LEDGER — the engine's record of earlier runs, newest last ({n}"
            f"{', its tail' if cut else ''}; read_file {FILE}, older months in {ARCHIVE_DIR}/):")
    return head + "\n" + "\n".join(lines[-max_lines:])
