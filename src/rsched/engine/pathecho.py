"""WHERE a relative path actually landed, when it was not the working directory.

A file action's `path` may be relative, and `paths.resolve_rel(prefer_existing=True)` —
`run_context.resolve_action_path`, the one funnel every file action goes through — looks for it
in the run's granted roots in order: the first root that already holds the path, else the first
that holds its PARENT directory, else the working directory. That rule exists so a relative path
can reach a file sitting in a granted root, which the schema's own promise ("path relative to
your working directory, or an allowed root") had always implied and the resolver did not do.

But the OBSERVATION echoed the relative string back unchanged, so a resolution into another root
was invisible — and the next step reads that string as if it meant the working directory:

* a UTIL is launched with the working directory as its cwd, so the same relative path it was
  handed resolves somewhere else and the util reports the file MISSING. The failure arrives as
  `FileNotFoundError`, indistinguishable from a wrong path, so the natural response is to doubt
  the path and retry variants — which is three turns spent on a correct call;
* and a path handed on to anything else — a script, a report, a later absolute read — carries
  the same wrong assumption about where the bytes are.

So every file action now says where the path went whenever that is NOT under the working
directory. One annotation at the dispatch funnel rather than at each action's return: the
actions differ in what they do and not in this, and six copies of one sentence is how five of
them come to disagree.

`shell` already does exactly this with its own `cwd` key when a command ran somewhere other
than the working directory, and this is that idea for the path a file action resolved.
"""

from __future__ import annotations

from pathlib import Path

from . import run_context

#: The file actions whose `path` goes through `resolve_action_path`. `move` carries `src`/`dst`
#: instead and is annotated through them; an action absent here is never touched.
PATH_ACTIONS = ("read_file", "write_file", "edit_file", "delete", "mkdir")

#: What the run is told. It names the directory the path was taken to be relative to, because
#: that is the thing the caller had wrong — not the path, which was exactly as written.
ELSEWHERE_NOTE = ("this RELATIVE path resolved in a granted root rather than in your working "
                  "directory, because that root already holds it (or its parent) — a util is "
                  "launched with your working directory as its cwd, so hand it THIS absolute "
                  "path, not the relative one")


def _resolved(ctx, rel: object, *, write: bool) -> Path | None:
    """Where `rel` resolved, or None when it is not a relative path this can speak about.

    FAILS OPEN on anything the context cannot answer. This runs before every dispatch, and
    callers hand in context STAND-INS carrying only what their own subject needs: a hard
    `ctx.write_roots()` / `ctx.routine.dir` read here would be an AttributeError that kills
    an action which has nothing to do with path resolution. An annotation is a diagnostic,
    and a diagnostic may never be the reason a working action fails.
    """
    if not isinstance(rel, str) or not rel or Path(rel).is_absolute():
        return None
    try:
        return run_context.resolve_action_path(ctx, rel, write=write)
    except Exception:
        return None


def _elsewhere(ctx, rel: object, *, write: bool) -> str:
    """The absolute path `rel` landed on when that is OUTSIDE the working directory, else "".

    Resolved on BOTH sides (`work_dir` too): the working directory reached through a symlink
    would otherwise read as a different place and annotate every single action. Fails open for
    the same reason as `_resolved`.
    """
    resolved = _resolved(ctx, rel, write=write)
    if resolved is None:
        return ""
    try:
        work = Path(run_context.work_dir(ctx)).resolve()
    except Exception:
        return ""
    return "" if resolved.is_relative_to(work) else str(resolved)


def annotate(action: dict, obs: dict, ctx) -> dict:
    """Add `resolved_path` (and `resolved_src` / `resolved_dst` for a move) to `obs` when a
    relative path landed outside the working directory. Returns the same dict.

    Total by construction rather than by a `try`: this runs on EVERY routed action, so it must
    not be able to fail — and the only thing here that CAN is the context, which is why the
    fail-open lives in `_resolved` (callers hand in stand-ins carrying only what their own
    subject needs). Everything else reads two optional keys off a dict a handler built, and a
    belt-and-braces guard over those was dead code that mypy correctly refused.
    """
    kind = str(action.get("kind") or "")
    write = kind != "read_file"
    if kind in PATH_ACTIONS:
        asked = action.get("paths")
        for rel in (asked if isinstance(asked, list) else [action.get("path")]):
            if where := _elsewhere(ctx, rel, write=write):
                obs["resolved_path"] = where
                obs["resolved_note"] = ELSEWHERE_NOTE
                break
    elif kind == "move":
        for field, key in (("src", "resolved_src"), ("dst", "resolved_dst")):
            if where := _elsewhere(ctx, action.get(field), write=True):
                obs[key] = where
                obs["resolved_note"] = ELSEWHERE_NOTE
    return obs
