"""`GET /api/credential-screen` — does this file carry a credential this instance stores?
Findings and coverage, never a value (D296, operator decision 2026-11-19, option a).

THE GAP IT CLOSES. D171 gave `rsched export` the NAMES of every stored credential
(`api_credential_names.py`) because the util's sandbox hides the stores themselves. Names
cannot be matched against file content — there is no value to compare — so the export has been
reporting `withheld_coverage: partial` and exiting 0 having compared NOTHING by value for the
central store and the OAuth connections. The daemon can compare, because it reads those stores
already; this route is that comparison, and `credscreen`'s module docstring holds the threat
model it was built against.

WHY A GET, AND WHY IT MATTERS. A screen reads; it writes nothing and prunes nothing — the
caller decides what to publish once it knows. So this is an ordinary read for the routine token
the export runs under, and `app.ROUTINE_TOKEN_MUTATIONS` stays empty (six tests and three docs
pin that tuple, and every entry in it is a hole in the "config is the user's" seal). The
daemon's job ends at the verdict; acting on it — withholding, pruning, refusing to push — is
the export's, where the policy already lives.

WHY IT IS A SIBLING OF `/api/settings`, NOT A CHILD. Same reason as the names route: the
routine token is refused that whole subtree (`app.ROUTINE_TOKEN_DENIED_READS`), and a screen
its only caller cannot reach is no screen at all.

THE DIGEST IS THE POINT OF THE CONTRACT. Each result carries the sha256 of the exact bytes
screened, so the caller can prove the file it publishes is the file that was screened — the
"bind a path plus checked bytes/digest to the operation" the decision asks for. A file that
changed under the read, or that the byte bound cut short, is reported as such and is never
`clean`.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request

from ..credscreen import authorized_roots, screen_report

router = APIRouter(tags=["settings"])

#: Paths in one call. The stores are read once per call, so a batch is far cheaper than N
#: calls — but an unbounded list is a way to hold the daemon's thread for a very long time.
MAX_PATHS = 500


@router.get("/credential-screen")
def credential_screen(request: Request,
                      path: Annotated[list[str] | None, Query()] = None) -> dict:
    """Screen each `path` for stored credential values.

    Answers `screened` (one result per path: disposition, the NAMES of what it carries, the
    digest of the bytes read, whether it was truncated or changed underneath), `refused` (a
    path outside every authorized root, a symlink, a non-regular file), and `sources` (which
    credential sources the daemon could read, so a caller can tell an empty finding list over
    full coverage from one over partial).

    No response field can carry a credential value: the screen answers with names and counts
    only, and the names are what D171 already serves.
    """
    if not path:
        raise HTTPException(status_code=400,
                            detail="name at least one absolute path to screen (?path=…)")
    if len(path) > MAX_PATHS:
        raise HTTPException(status_code=400,
                            detail=f"at most {MAX_PATHS} paths per call, asked {len(path)}")
    roots = authorized_roots(request.app.state.server)
    if not roots:
        raise HTTPException(status_code=503,
                            detail="this instance has no authorized root to screen inside")
    return screen_report(path, roots)
