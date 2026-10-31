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

**Both shapes are policed, by one allow-list** (D143). The directory holds two record shapes,
and only the first was ever unified: `answer-<qid>.json` had seven hand-rolled write sites in
four modules, each spelling the filename and stamping its own `ts` — the state `msg-*` was in
before F499. They cannot share a writer, because an answer must never be counted as freight
(the one thing that must not buy a run), so `SANCTIONED_WRITERS` names the owner of each shape
WITH the reason it is allowed: adding a direct write is a failure that names the seam to call,
and adding an entry here is a design decision a later reader can check against its reason.
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
#: `queued_message(` is the web layer's resolver of a queued `msg-*` file by id: writing the path
#: it returns is a rewrite of that shape, and a rewrite outside `inbox.rewrite_message` races the
#: drain — the write re-creates a message a run consumed after the resolve, delivered twice.
_MSG_PATH = re.compile(r"""["']msg-|\bqueued_message\(""")

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


#: Every module allowed to write a file into a routine's `inbox/`, and WHY it is — one entry
#: per record shape that lives there, naming the invariants its writer is the one place to
#: hold. A NEW entry is a design decision, not a formality: it says a third shape now shares
#: that directory, and the reason has to survive the reader who meets it next.
SANCTIONED_WRITERS: dict[str, str] = {
    "engine/inbox.py":
        "owns the `msg-*` shape: `file_message` validates `via` against the closed VIAS set "
        "(the delivery-policy switch three subsystems read), stamps one `ts` spelling, holds "
        "the unique-suffix rule, and `rewrite_message` edits a queued record under the "
        "drain's lock (F499)",
    "answerfile.py":
        "owns the `answer-<qid>.json` shape: `answerfile.write` spells the filename from the "
        "qid, carries that qid INTO the record, stamps `ts` once, and refuses a record "
        "neither reader would consume — the half F499 unified for messages and left undone "
        "for answers (D143)",
}

#: An expression naming a FILE inside an inbox: `… / "inbox" / f"answer-{qid}.json"`,
#: `inbox / f"{msg_id}.json"`, `inbox_dir / f"msg-rep-{id}.json"`. The directory alone
#: (`… / "inbox"`, the receiver every scanner globs) is deliberately not a hit: this check is
#: about the files written into it, and `reports.py` and `readmodels/orphans.py` build that
#: directory to ask whether a delivery is still queued.
_INBOX_FILE = re.compile(r"""(?:["']inbox["']|\binbox\w*)\s*/\s*f?["'][^"']+["']""")

#: Binding that expression to a name — `marker = … / "inbox" / …`, or the `for path in (…)`
#: form `api_patch_applied` uses to correct both copies of a receipt in one loop.
_BINDING = re.compile(r"""^\s*(?:for\s+(?P<loop>\w+)\s+in|(?P<name>\w+)\s*=(?!=))""")

#: Handing a BOUND inbox path to a writer. The write sits up to a dozen lines below the
#: expression that built it (`api_question_decline` has a 409 guard, the payload and three
#: lines of comment in between), so a fixed window would miss it — the name is tracked
#: instead, and reset at every `def`, because `path` is bound to something else entirely in
#: the next function of the very same module (`engine/inbox_questions._archive_answer`).
_WRITE_OF = re.compile(r"""\b(?:atomic_write_json|atomic_write)\s*\(\s*(\w+)\s*[,)]"""
                       r"""|\b(\w+)\.(?:write_text|write_bytes)\s*\(""")

_DEF = re.compile(r"^\s*(?:async\s+)?(?:def|class)\s")


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
                    f"or without `name=` for the unique `msg-<ts>-<rand>` form; an in-place "
                    f"edit of a queued one goes through `inbox.rewrite_message`.")
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


def _inbox_writes(path: Path) -> list[tuple[int, str]]:
    """Every line in `path` that writes a file INTO an inbox — (line, the name it wrote).

    Two shapes, because both occur in the live tree: the write inline in the expression that
    builds the path (`api_questions`), and the write of a name bound earlier in the same
    function (`api_question_decline`'s `marker`, nine lines up, past a 409 guard). Bindings are
    dropped at every `def`: `path` names an inbox answer in `inbox_questions.take_answer` and
    the ANSWERED-archive record two functions below, and conflating them would report a
    sanctioned write as a breach.
    """
    found: list[tuple[int, str]] = []
    bound: dict[str, int] = {}
    for n, line in _code_lines(path.read_text(encoding="utf-8")):
        if _DEF.match(line):
            bound.clear()
        inbox_file = _INBOX_FILE.search(line)
        if inbox_file:
            if _WRITE_CALL.search(line):
                found.append((n, inbox_file.group(0).strip()))
            elif (binding := _BINDING.match(line)):
                bound[binding.group("name") or binding.group("loop")] = n
        found.extend((n, written)
                     for written in (m.group(1) or m.group(2)
                                     for m in _WRITE_OF.finditer(line))
                     if written in bound)
    return found


def test_only_the_sanctioned_writers_write_into_a_routines_inbox():
    """D143. A routine's `inbox/` holds two record shapes and each has ONE owner; every other
    module asks that owner. The defect is the EXISTENCE of a hand-rolled write site — no
    exercise of the right one can see it — and the measured cost is drift per endpoint: before
    the answer shape was unified, four modules spelled `answer-<qid>.json` themselves at seven
    sites, each stamping its own `ts` and one of them omitting the `qid` the consumer falls
    back to the filename for.
    """
    problems = []
    for path in _py_files():
        rel = path.relative_to(SRC).as_posix()
        if rel in SANCTIONED_WRITERS:
            continue
        for n, what in _inbox_writes(path):
            problems.append(
                f"src/rsched/{rel}:{n}: writes `{what}` into a routine's inbox/ directly. "
                f"Each shape there has ONE writer, holding invariants a second site drifts "
                f"from: call `answerfile.write(routine_dir, qid, payload)` to file an answer "
                f"(or `answerfile.update(routine_dir, qid, fields)` to correct one already "
                f"queued), and `engine.inbox.file_message(...)` for the `msg-*` shape — "
                f"`inbox.rewrite_message` for an in-place edit of queued freight. A genuinely "
                f"new record shape in that directory is a new entry in SANCTIONED_WRITERS "
                f"WITH its reason, not a write here (D143/F499).")
    assert not problems, "\n".join(problems)


def test_every_sanctioned_writer_exists_and_says_why_it_is_one():
    """The allow-list is the whole instrument: an entry is what makes a direct write legal, so
    a stale one silently unpolices a module and a reasonless one teaches the next author that
    adding a name is a formality. Both halves are asserted — the same reason
    `tests/test_policy.py` makes its checks say what to do instead.
    """
    for rel, reason in SANCTIONED_WRITERS.items():
        assert (SRC / rel).is_file(), (
            f"SANCTIONED_WRITERS names src/rsched/{rel}, which does not exist — a moved or "
            f"deleted writer leaves its module unpoliced. Point the entry at the owner of "
            f"that shape, or drop it.")
        assert len(reason) > 40 and ("msg-" in reason or "answer-" in reason), (
            f"src/rsched/{rel} is allow-listed without a reason that names the SHAPE it owns "
            f"and the invariants it holds for it — write one, or the entry is just an "
            f"exemption, and the next author reads adding a name here as a formality.")
    assert WRITER in SANCTIONED_WRITERS, (
        f"{WRITER} is the one writer of the `msg-*` shape and must be allow-listed as such")
    assert _inbox_writes(SRC / WRITER), (
        f"{WRITER} is allow-listed as an inbox writer but writes no inbox file — either the "
        f"writer moved (point the entry at it) or this check no longer sees a real write, "
        f"which would make the whole scan vacuous")
