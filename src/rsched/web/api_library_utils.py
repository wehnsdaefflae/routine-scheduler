"""The Library tab's GLOBAL UTIL surface — read one, save one (selftest-gated), delete one.

Split out of `api_workflows.py` (F584), which was at its size ceiling: a util is the one
library kind whose save EXECUTES something, so its endpoint carries a gate none of the prose
kinds has, and that gate is this module's whole responsibility.

These take no routes of their own. A second route under `/library/{kind}/{slug}` would be
shadowed by the one `api_workflows` registers first, so the kind dispatch there calls in here
— the arrangement that was already true before the split.
"""

from __future__ import annotations

from fastapi import HTTPException, Request
from pydantic import BaseModel

from .. import utils_header
from ..ids import is_slug


def delete_util(request: Request, name: str) -> dict:
    """Delete a global util — its whole <name>/ dir, committed, so it is recoverable from
    git history. Routines discover utils live; the catalog shrinks at their next run.
    """
    from .. import utils_lib

    server = request.app.state.server
    if not utils_lib.exists(server.libraries_home, name):
        raise HTTPException(404, f"no util {name!r}")
    utils_lib.remove_util_file(server.libraries_home, name)   # atomic rename-aside + delete
    utils_lib.git_commit(server.libraries_home, f"delete util {name} via web",
                         routines_home=server.routines_home, paths=[f"utils/{name}"])
    return {"ok": True}


def util_detail(request: Request, name: str) -> dict:
    # reached via /library/{kind}/{slug} with kind="utils" (library_doc_detail dispatches
    # here) — a direct route registration would be shadowed by that pattern anyway
    from .. import utils_lib

    server = request.app.state.server
    content = utils_lib.read_util(server.libraries_home, name)
    if content is None:
        raise HTTPException(404, f"no util {name!r}")
    return {"name": name, "content": content}


class UtilBody(BaseModel):
    content: str
    impact_digest: str | None = None


def put_util(request: Request, name: str, body: UtilBody,
             require_digest) -> dict:
    """Create or edit a global util (selftest-gated, committed) — the web twin of the
    write_util engine action, and like it the selftest gates the LIBRARY BEFORE it holds the
    text (`utils_stage.write_selftested`, F584/D161): the candidate is tested under a
    throwaway slug and `utils/<name>` is written only on green, so no routine's `gu <name>`
    can reach an untested script while the selftest runs. Earlier shapes installed the text
    first and rolled it back on failure — correct at the end, but every routine's `gu <name>`
    landing inside the selftest window (a 120 s timeout, plus a dependency prewarm) ran code
    nothing had tested.

    `require_digest` is `api_workflows._require_digest` passed in rather than imported: the
    impact-confirm token is the LIBRARY TAB's rule for every kind, so it stays where the other
    kinds enforce it and this module does not import its own caller.
    """
    from .. import sandbox, utils_lib, utils_stage
    from ..engine.observations import truncate

    server = request.app.state.server
    if not is_slug(name):   # write_util_file's backstop would raise it as a 500
        raise HTTPException(400, f"invalid util name {name!r} — lowercase a-z, 0-9 and -")
    if name.startswith(utils_lib.STAGING_PREFIX):
        # The prefix a candidate is selftested under is hidden from the catalog and swept by
        # the next author's stale sweep, so a util may not be named that way.
        raise HTTPException(400, f"{utils_lib.STAGING_PREFIX!r} is reserved for utils being "
                                 "selftested before install — pick another name")
    problems = utils_header.header_problems(body.content)
    if problems:
        raise HTTPException(422, "header problems (not saved): " + "; ".join(problems))
    require_digest(request, "utils", name, body.content, body.impact_digest)
    utils_lib.ensure_library(server.libraries_home, remote=server.libraries_remote)
    ok, output = utils_stage.write_selftested(
        server.libraries_home, name, body.content, policy=sandbox.base_policy(server),
        message=f"revise {name} via web", routines_home=server.routines_home)
    if not ok:
        # head+tail, as the engine reports it (R93): a traceback's END is the repair material
        raise HTTPException(422, "selftest failed (never installed, not committed):\n"
                                 + truncate(output, cap=2000)[0])
    return {"ok": True}
