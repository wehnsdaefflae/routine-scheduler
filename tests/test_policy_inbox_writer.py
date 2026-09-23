"""The inbox's `msg-*` shape has ONE writer, and every inbox scanner names that stem (F499).

`engine/inbox.file_message` has called itself the one writer of that shape since it was
written, and for months seven modules wrote the filename themselves anyway — one of them
character-for-character. Each hand-rolled site chose its own `ts` spelling and its own
uniqueness rule, so the shape drifted per endpoint; two of them also assumed an `inbox/`
directory that only `file_message` creates, and silently lost a trigger event or a one-shot's
wake-up reason delivered to a routine that had never been given one.

The read side had the mirror defect: a scanner that selected "every file that is not
`answer-*`" also matched `paths.atomic_write`'s in-flight `.msg-….json.XXXX.tmp`. On a fresh
boot the drain reached that temp file, could not parse it, logged "not a message file" and
RENAMED it into `consumed/` — so the writer's own `replace()` raised and the message was lost.

Both halves were unified across 0.365.0. Neither is expressible as a runtime assertion: the
defect is the EXISTENCE of a second call site, which no amount of exercising the first one can
see. So it is a source scan, in the company of this repo's other conventions that broke
silently once (`tests/test_policy.py`'s premise) — the one instrument that fails the moment a
future change re-opens the seam, rather than months later, in production, as a lost message.

**Scope, deliberately narrow.** Both checks look only at expressions that name an INBOX. The
first draft of this file asked "does this module mention an inbox, and does it glob anything?"
and returned ten hits, every one a false positive: `daemon/detached.py` globbing its request
spool, `composer.py` globbing `runs/*/result.md`, `search/sources.py` and `inbox.py` globbing
`questions/pending/*.json`, and two `iterdir()` slug listings that skip dotfiles. A check that
reports on directories it never meant to read teaches its reader to skim past it, so the
receiver has to be an inbox for the row to mean anything.

Both checks report the offending `path:line` and name the call that replaces it, because a
policy failure whose repair the reader has to work out is a policy that gets deleted.
"""

import re
from pathlib import Path

import rsched

SRC = Path(rsched.__file__).parent
REPO = Path(__file__).resolve().parent.parent

#: The ONE writer. Everything else that needs an inbox message calls it — with `name=` for a
#: deterministic idempotency key, `extra=` for the keys one channel adds on top of that shape.
WRITER = "engine/inbox.py"

#: Building an inbox filename that starts `msg-`: `… / f"msg-{stem}.json"`, or any of the
#: hand-rolled spellings the unification removed (`f"msg-rep-{id}.json"`, `f"msg-bg-{task}.json"`,
#: `"msg-trig-…"`, `"msg-once-…"`). A READ-side existence check builds the same expression, so
#: this pattern cannot separate the two on its own — the write test looks at what is DONE with it.
_MSG_PATH = re.compile(r"""["']msg-""")

#: Handing that path to a writer: the repo's atomic writers, plus the raw escape hatches.
_WRITE_CALL = re.compile(r"\b(atomic_write_json|atomic_write|write_text|write_bytes)\s*\(")

#: A glob whose RECEIVER is an inbox — `inbox.glob(…)`, `(routine_dir / "inbox").glob(…)`,
#: `inbox_dir.glob(…)`. Anything else in these modules (a request spool, `runs/*/result.md`,
#: `questions/pending/*.json`) is a different directory with a different contract.
_INBOX_GLOB = re.compile(
    r"""(?:\binbox\w*|["']inbox["']\s*\))\s*\.glob\(\s*f?["']([^"']+)["']\s*\)""")

#: The banned read shape: selecting inbox entries by excluding answers. Requires `inbox` in the
#: same expression, so a slug listing that merely skips dotfiles is not a hit.
_BANNED_EXCLUSION = re.compile(
    r"""inbox\w*[^\n]*?(?:startswith\(\s*["']answer-|!=\s*["']answer-)"""
    r"""|(?:startswith\(\s*["']answer-|!=\s*["']answer-)[^\n]*?inbox\w*""")


def _py_files() -> list[Path]:
    return sorted(p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts)


def _code_lines(text: str) -> list[tuple[int, str]]:
    """Every line that is not inside a docstring.

    The prose ABOUT this contract quotes the banned forms verbatim — `inbox.py`'s own module
    header does, and so does this file — so a scan that read docstrings would fail on the
    documentation of the rule it enforces.
    """
    out: list[tuple[int, str]] = []
    in_doc = False
    fence = ""
    for n, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if in_doc:
            if fence in stripped:
                in_doc = False
            continue
        if stripped.startswith(('"""', "'''")):
            fence = stripped[:3]
            rest = stripped[3:]
            if not rest.endswith(fence) or not rest:
                in_doc = True
            continue
        out.append((n, line))
    return out


def test_only_one_module_writes_the_msg_star_shape():
    """A second writer of `msg-*` is the defect F499 names, and it is invisible at runtime."""
    problems = []
    for path in _py_files():
        rel = path.relative_to(SRC).as_posix()
        if rel == WRITER:
            continue
        lines = _code_lines(path.read_text(encoding="utf-8"))
        for i, (n, line) in enumerate(lines):
            if not _MSG_PATH.search(line):
                continue
            # The write is the path's USE, which may sit a few lines below the expression that
            # built it (`path = … f"msg-…"`, then `atomic_write_json(path, …)`).
            window = " ".join(text for _, text in lines[i:i + 5])
            if _WRITE_CALL.search(window):
                problems.append(
                    f"src/rsched/{rel}:{n}: builds a `msg-*` inbox filename and writes it — "
                    f"`engine.inbox.file_message` is the ONE writer of that shape (F499). "
                    f"Call it instead: `file_message(routine_dir, text, via=…, "
                    f"name='<deterministic-key>')` when the filename is an idempotency key, "
                    f"or without `name=` for the unique `msg-<ts>-<rand>` form.")
    assert not problems, "\n".join(problems)


def test_every_inbox_scanner_names_the_one_writers_stem():
    """A scanner that excludes `answer-*` instead of selecting `msg-*` admits temp files."""
    problems = []
    for path in _py_files():
        rel = path.relative_to(SRC).as_posix()
        for n, line in _code_lines(path.read_text(encoding="utf-8")):
            for pattern in _INBOX_GLOB.findall(line):
                if pattern.startswith(("msg-", "answer-")):
                    continue
                problems.append(
                    f"src/rsched/{rel}:{n}: globs `{pattern}` on an inbox — select "
                    f"`msg-*.json`, the stem the one writer produces, so an in-flight "
                    f"`.msg-….json.XXXX.tmp` is never read as a message (F499).")
            if _BANNED_EXCLUSION.search(line):
                problems.append(
                    f"src/rsched/{rel}:{n}: selects inbox entries by EXCLUDING `answer-*` — "
                    f"that also admits `atomic_write`'s in-flight temp file, which the drain "
                    f"then renames into consumed/, so the writer's own replace() fails and "
                    f"the message is lost. Select `msg-*.json` positively (F499).")
    assert not problems, "\n".join(problems)
