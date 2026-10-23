"""`rsched postdeploy` — the AFTER half of a deploy: is the live instance healthy, and is what it
serves what we built?

All the I/O of the checklist in `rsched/postdeploy.py` lives here and nowhere else: the HTTP call
to `/api/status`, the `__version__` read out of the checkout, the walk over the state homes, the
append to the record. Every judgement is the pure module's. That split is `conformance.py` +
`cli_validate.py`'s, and here it also decides what is testable: a test can hand the checklist the
19-nights shape or a root-owned home with no live instance anywhere near it.

`load_server_config` is imported AT MODULE LEVEL deliberately — the suite's `cli_server` fixture
patches that seam on each holding module, and a `cmd_*` module that imports it inside the function
loads the PRODUCTION config and answers about the real instance (`cli_runctl`'s docstring says the
same, for the same reason).

WHERE IT SITS IN A SESSION (deploy/DOCKER.md, "Rebuilding"):

    rsched drain && docker compose up -d && rsched drain --resume
    rsched postdeploy            # ← here, against the box that is now serving

It is READ-ONLY against the instance: one GET, some stats, and one line appended to this
instance's own record. It never restarts anything and never fixes anything — the operator decides
what to do with a FAIL, because every one of the four specimens behind these checks needed a
different fix.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

from .config import load_server_config
from .ids import now_iso
from .paths import append_jsonl, config_file
from .postdeploy import (
    FAIL,
    Check,
    PathStat,
    backup_freshness,
    build_skew,
    exit_code,
    record_row,
    served_identity,
    state_path_ownership,
    summary_line,
)

#: How long one status read may take. A deploy session is a person waiting at a terminal, and an
#: unreachable instance is itself the finding (`served-identity` FAILs on it) — so this is short
#: enough that a wedged box answers the checklist in seconds rather than hanging the session.
STATUS_TIMEOUT_S = 10.0

#: The record every session appends one row to, beside the health and usage streams this instance
#: already keeps (`health_events`, `usage_log`): `<routines_home>/.control/postdeploy.jsonl`.
#: Instance-local state, under the convention that is already there — a deploy record in a new
#: directory of its own is a directory nothing else knows to read, back up or rotate.
RECORD_FILE = ".control/postdeploy.jsonl"

#: The file cap on the ownership walk. The homes hold ~1600 files a day of run state, and a
#: post-deploy check that walks a million inodes over a network share is one nobody waits for.
#: Hitting it is REPORTED (`state_path_ownership(truncated=True)`), never silently passed.
WALK_FILE_CAP = 20000


def _status(url: str, token: str) -> dict | None:
    """`GET <url>/api/status` as a dict, or None when nothing usable came back.

    None covers every way a deploy session gets no answer — wrong host, container down, wedged
    event loop, a token that is not this instance's — because the operator's next move is the same
    for all of them (look at the box), and `served-identity` says so in those words.
    """
    headers = {"User-Agent": "rsched-postdeploy/1",
               **({"Authorization": f"Bearer {token}"} if token else {})}
    try:
        # Inside the try: Request() itself raises ValueError on a url with no scheme, and a
        # mistyped --url must be a FINDING (served-identity's FAIL), never a traceback at the
        # one moment the operator is watching a box he just deployed.
        req = urllib.request.Request(f"{url.rstrip('/')}/api/status", headers=headers)  # noqa: S310
        if req.type not in ("http", "https"):
            return None
        with urllib.request.urlopen(req, timeout=STATUS_TIMEOUT_S) as resp:  # noqa: S310
            body = json.loads(resp.read().decode("utf-8", errors="replace"))
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None
    return body if isinstance(body, dict) else None


def checkout_version(repo: Path | None) -> str:
    """`__version__` as the CHECKOUT's own `src/rsched/__init__.py` spells it, '' if unreadable.

    Read as TEXT, never as `rsched.__version__`: this process imported its modules from wherever
    it was launched, and the question is what the tree the deploy came from says. Those are the
    same file in the normal case and that is exactly the case where the answer must be trusted.
    """
    if not repo:
        return ""
    try:
        text = (Path(repo) / "src" / "rsched" / "__init__.py").read_text(encoding="utf-8")
    except OSError:
        return ""
    m = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
    return m.group(1) if m else ""


def _homes(server) -> list[Path]:
    """The homes the daemon itself must write — the ownership check's subject.

    Taken from the loaded config rather than by parsing `deploy/state-paths.sh`: that inventory
    answers a different question (what a MIGRATION must carry, which includes homes no rsched
    process writes — a browser profile, the messenger session stores), and its lists are bash
    arrays whose one Python reader shells out to bash to read them (`tests/test_deploy_state.py`).
    What the daemon writes, the daemon's own config already names.
    """
    seen: list[Path] = []
    for p in (server.routines_home, server.conversations_home, server.background_home,
              server.libraries_home, config_file().parent):
        if p and Path(p) not in seen:
            seen.append(Path(p))
    return seen


def _owner_stats(homes: list[Path], reference: int) -> tuple[list[PathStat], bool]:
    """Each home, plus every file under it NOT owned by `reference` — and whether the walk was cut.

    Only offenders are collected, not every file: the finding is "this path is owned by a uid the
    daemon cannot write as", and a list of 40000 correct files is not a finding. The walk is what
    makes the 0.373.1 specimen visible at all — that defect was ONE root-owned file inside a home
    whose own directory was perfectly owned.
    """
    stats: list[PathStat] = []
    seen = 0
    truncated = False
    for home in homes:
        try:
            st = home.stat()
        except OSError:
            stats.append(PathStat(str(home), exists=False))
            continue
        stats.append(PathStat(str(home), exists=True, uid=st.st_uid))
        for root, dirs, files in os.walk(home, onerror=lambda _e: None):
            dirs[:] = [d for d in dirs if d not in ("__pycache__", ".venv", "node_modules")]
            for name in files:
                seen += 1
                if seen > WALK_FILE_CAP:
                    truncated = True
                    break
                full = Path(root) / name
                try:
                    uid = full.lstat().st_uid
                except OSError:
                    continue
                if uid != reference:
                    stats.append(PathStat(str(full), exists=True, uid=uid))
            if truncated:
                break
        if truncated:
            break
    return stats, truncated


def _reference_uid(server) -> int:
    """The uid the daemon writes as: whoever owns `routines_home`. -1 when it cannot be read.

    NOT `os.getuid()`. A session that reaches the container with `docker compose exec` and forgets
    `-u mark` runs this as root, and then every root-owned file — the defect — would compare equal
    and the check would pass on exactly the instance it exists to catch (`deploy/DOCKER.md`: "an
    `exec` as root is how root-owned files landed in mark's caches before"). The home install.sh
    created is the instance's uid by construction, and it is the uid `backup.sh` reads as.
    """
    try:
        return server.routines_home.stat().st_uid
    except OSError:
        return -1


def run_checks(server, url: str, source: str) -> tuple[list[Check], dict | None]:
    """The whole checklist against a live instance, in `CHECK_NAMES` order — and the status it read.

    The payload comes back with the checks so the RECORD describes the same reading the operator
    saw on screen. A second read for the record would be a second instant: a restart between the
    two, and the row describes neither.
    """
    from .daemon.backup_watch import staleness

    status = _status(url, server.token)
    reference = _reference_uid(server)
    stats, truncated = _owner_stats(_homes(server), reference) if reference >= 0 else ([], False)
    try:
        state, detail = staleness()
    except OSError as exc:              # a share that vanished mid-read is a finding, not a crash
        state, detail = "unknown", f"reading the backup record raised {exc!r}"
    return ([served_identity(url, status, source),
             build_skew(status, checkout_version(server.source_repo),
                        str(server.source_repo or "")),
             backup_freshness(state, detail),
             state_path_ownership(stats, reference, truncated)], status)


def cmd_postdeploy(args) -> int:
    """Print one line per check, append one row to the record, exit 1 only on a FAIL."""
    server, problems = load_server_config()
    for pr in problems:
        print(f"server config: {pr}", file=sys.stderr)
    url = args.url or f"http://127.0.0.1:{server.port}"
    source = "--url" if args.url else "config bind/port"
    checks, status = run_checks(server, url, source)
    for check in checks:
        print(check.line())
    print(summary_line(checks))
    code = exit_code(checks)
    row = record_row(now_iso(), url, status, checks)
    if not args.no_record:
        path = server.routines_home / RECORD_FILE
        try:
            append_jsonl(path, row)
            print(f"recorded: {path}", file=sys.stderr)
        except OSError as exc:          # the checks are the product; the record is best-effort
            print(f"NOT recorded ({path}): {exc}", file=sys.stderr)
    if code:
        print(f"exit {code}: act on the {FAIL} line(s) above before you walk away from this "
              "deploy", file=sys.stderr)
    return code


def add_parser(sub) -> None:
    """Register `postdeploy` on the CLI's subparsers — called from `cli.main`."""
    pd = sub.add_parser("postdeploy",
                        help="check the LIVE instance after a deploy: served build, backup "
                             "freshness, state-path ownership — and record the session")
    pd.add_argument("--url", default="",
                    help="the instance to check (default: http://127.0.0.1:<config port>); "
                         "printed with every verdict, so a session can never be silently about "
                         "the wrong machine")
    pd.add_argument("--no-record", action="store_true",
                    help=f"print the checklist without appending to {RECORD_FILE}")
    pd.set_defaults(fn=cmd_postdeploy)
