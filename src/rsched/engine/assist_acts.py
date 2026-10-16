"""Assist predicates over what a run DOES to the world — the second half of the registry.

`assist_predicates.py` holds the checks that read the run's own state (a failure count, a
user reply, the ledger's mtime) and the registry itself; this file holds the ones that read
an ACT: a message about to go out under the operator's name, a web search that came back,
code written, a tracked file removed, a tool built, feedback read. Split out when the
registry outgrew one file; `assist_predicates.PREDICATES` merges `ACTS` in, so the linter and
the runtime still see one vocabulary.

Every check keys on what the LIBRARY declares about the thing acted on — a permission doc's
tags, a util's own header — never on a util's name. That is what keeps a rule from naming a
tool (a rule declares a predicate; the predicate reads the catalog), and what lets a new
channel join a moment by being described, not by being listed here. Two tag contracts follow
from it, documented in docs/rule-assists.md:

- a PERMISSION doc tagged `as-operator` reserves a channel that speaks to a person AS THE
  OPERATOR — mail, the messengers, a news post. A dedicated tag and not `communication` +
  `outbound`, which also describe the bot channel that posts notices TO him
  (`messaging-discord`) and the push to his own device (`notifications`);
- a UTIL whose header declares `net: outbound` and tags both `web` and `search` or `research`
  looks facts up on the web, and a util tagged `feedback` reads what readers said.

Precision is the budget here as everywhere in the registry: each of these misses rather than
guesses — a send through a util no permission reserves, a page fetched without a search, are
not seen, and that is the right failure.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .assist_predicates import Situation

#: The permission tag that marks a channel speaking to a person as the operator.
SPEAKS_AS_OPERATOR = "as-operator"

#: A util looks up the outside world when it reaches the network and is tagged `web` plus one
#: of these. `search` alone also names local code search, `research` alone a model call.
_LOOKUP_TAGS = frozenset({"search", "research"})

#: The files a later run executes or a test runner loads — "code later runs rely on".
CODE_SUFFIXES = (".py", ".sh", ".js", ".mjs", ".ts", ".php")

#: A routine's own scratch, records and engine-owned trees — removing or writing there settles
#: nothing another reader relies on.
_SCRATCH = ("state/", "artifacts/", "runs/")


def _ok(s: Situation) -> bool:
    """The action ran and did not fail — a held action ran nothing, and a backgrounded call
    has not come back yet.
    """
    from .hold import is_hold
    from .observations import is_failure

    obs = s.obs or {}
    return bool(obs) and not is_hold(obs) and not is_failure(obs) \
        and not (s.action or {}).get("background")


def _resolved(s: Situation, raw: str) -> Path | None:
    """An action's path resolved where the engine resolved it (`work_dir`, the run's roots),
    or None when it names nothing the run could reach.
    """
    if not raw:
        return None
    from .run_context import resolve_action_path

    try:
        return resolve_action_path(s.ctx, raw, write=True)
    except (OSError, ValueError):
        return None


def _scratch(s: Situation, path: Path) -> bool:
    """Is `path` inside the routine's own scratch or records?"""
    routine_dir = s.ctx.routine.dir.resolve()
    return path.is_relative_to(routine_dir) and \
        f"{path.relative_to(routine_dir).as_posix()}/".startswith(_SCRATCH)


def _util_header(s: Situation) -> dict[str, Any] | None:
    """The header of the util this action calls, or None for any other action."""
    action = s.action or {}
    if action.get("kind") != "util" or not action.get("name"):
        return None
    from ..utils_header import parse_header
    from ..utils_lib import read_util

    src = read_util(s.ctx.server.libraries_home, str(action["name"]))
    return parse_header(src) if src else None


def _tags(header: dict[str, Any]) -> set[str]:
    return {str(t).strip().lower() for t in header.get("tags") or []}


def _reserving_docs(s: Situation) -> list[str]:
    """The permission docs reserving this exact util call — the whole util, or its verb."""
    action = s.action or {}
    grants = getattr(s.ctx, "grants", None)
    if action.get("kind") != "util" or grants is None:
        return []
    name = str(action.get("name") or "")
    args = action.get("args") or []
    verb = str(args[0]) if args and isinstance(args[0], str) else ""
    whole = list(grants.gated_utils.get(name) or ())
    return whole or list((grants.gated_verbs.get(name) or {}).get(verb) or ())


def _speaks_as_operator(s: Situation) -> bool:
    """This util call sends words to a person under the operator's name."""
    from .. import library_docs

    for slug in _reserving_docs(s):
        raw = library_docs.read_doc(s.ctx.server.permissions_home, slug)
        meta = library_docs.parse_lenient(raw)[0] if raw else {}
        tags = meta.get("tags")
        if isinstance(tags, list) and SPEAKS_AS_OPERATOR in {str(t).strip().lower() for t in tags}:
            return True
    return False


def _outbound_message(s: Situation) -> bool:
    """A message is about to go out under the operator's name.

    write-as-the-principal's and verify-independently's moment, and a HOLD: a sent message
    cannot be recalled, so the line has to land before the send — the rung the design note
    reserves for an irreversible cost to skipping. Asked once per scope, so only the first
    send of a run is held; what EVERY send owes — that the operator has not already sent it
    himself — is the channel permission's own conduct note, in the prompt of every holder.
    """
    return _speaks_as_operator(s)


def _outbound_message_sent(s: Situation) -> bool:
    """A message just went out under the operator's name.

    correspondence's moment: the thread is open from this send on, and the record of it is
    cheapest to make now, while the run still has the message in front of it.
    """
    return _ok(s) and _speaks_as_operator(s)


def _web_lookup_returned(s: Situation) -> bool:
    """A web search just came back.

    web-research's moment. Whether to look something up is the rule's first half and no
    situation shows it; what to do with what came back — corroborate the load-bearing fact,
    cite only what was fetched, keep the source beside it — starts here.
    """
    header = _util_header(s)
    if header is None or header.get("net") != "outbound" or not _ok(s):
        return False
    tags = _tags(header)
    return "web" in tags and bool(tags & _LOOKUP_TAGS)


def _feedback_read(s: Situation) -> bool:
    """A util that reads readers' feedback just returned it.

    feedback-loop's moment: every item that came back is a task for THIS run, which is
    easiest to hold to while the list is on screen.
    """
    header = _util_header(s)
    return header is not None and "feedback" in _tags(header) and _ok(s)


def _code_written(s: Situation) -> bool:
    """This run just wrote code a later run relies on: a util, or a source file.

    make-failure-visible's moment — the handler, the fallback and the test are all being
    decided right now. A file under the routine's own scratch is working state, not code
    anything relies on.
    """
    action = s.action or {}
    kind = action.get("kind")
    if kind == "write_util":
        return _ok(s)
    if kind not in ("write_file", "edit_file") or not _ok(s):
        return False
    path = _resolved(s, str(action.get("path") or ""))
    return path is not None and path.name.lower().endswith(CODE_SUFFIXES) \
        and not _scratch(s, path)


def _util_created(s: Situation) -> bool:
    """A new util — a tool whose approach nothing has proven yet — now exists.

    work-order's moment. Building something unproven is not visible in general; creating a
    tool is, and it is the point where the thin end-to-end proof is still cheap.
    """
    return (s.action or {}).get("kind") == "write_util" and bool((s.obs or {}).get("created")) \
        and _ok(s)


def _tracked_path_removed(s: Situation) -> bool:
    """A file or directory under version control was just deleted or moved away.

    change-scope's moment: whatever named the old path — a document, a test, a recipe — now
    names nothing, and the change is finished only when each of those is settled. A routine's
    scratch and records are no one's reference; a path outside any git work tree has no
    history saying who relied on it, and is left alone rather than guessed at.
    """
    action = s.action or {}
    kind = action.get("kind")
    if kind not in ("delete", "move") or not _ok(s):
        return False
    path = _resolved(s, str((action.get("path") if kind == "delete" else action.get("src"))
                            or ""))
    if path is None or _scratch(s, path):
        return False
    return any((p / ".git").exists() for p in path.parents)


#: name -> (moment, check, what fired). `assist_predicates.PREDICATES` wraps each in its
#: `Predicate`; a tuple here keeps this module free of an import cycle with the registry.
ACTS: dict[str, tuple[str, Any, str]] = {
    "outbound-message": (
        "pre-action", _outbound_message,
        ("this sends a message to a person under the user's name, and a sent message cannot "
         "be recalled")),
    "outbound-message-sent": (
        "observation", _outbound_message_sent,
        "a message just went out under the user's name"),
    "web-lookup-returned": (
        "observation", _web_lookup_returned, "a web search just came back"),
    "feedback-read": (
        "observation", _feedback_read, "readers' feedback just came back"),
    "code-written": (
        "observation", _code_written, "this run just wrote code a later run relies on"),
    "util-created": (
        "observation", _util_created, "this run just created a new tool"),
    "tracked-path-removed": (
        "observation", _tracked_path_removed,
        "a file under version control was just deleted or moved away"),
}
