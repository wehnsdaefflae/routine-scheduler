"""Scheduler-managed global utils — how routines run code, for everything but the one-off.

Each util is a PEP 723 script at <library>/utils/<name>/main.py, run via `uv run --script`.
A `gu` dispatcher lives at the library root so utils compose by calling each other
(`gu <sibling> --json`). The library repo is git-backed (neutral identity, best-effort push
hook) and can bootstrap from / sync to a remote. It works empty — routines generate the
utils they need.

Code execution is mediated here, through named, selftested, git-committed (and optionally
human-approved) utils. The one exception is the `shell` ACTION KIND (engine/actions.py, gated
by the `shell` capability): an ad-hoc command for what a routine runs ONCE, jailed on the same
terms as a util. Anything run twice belongs back here.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path

from . import libgit
from .ids import is_slug
from .paths import atomic_write
from .utils_header import parse_header

log = logging.getLogger("rsched.utils_lib")

# Vars scrubbed from util subprocesses UNCONDITIONALLY (declared or not). LLM-auth: a util
# that needs an LLM (e.g. a `gu claude` equivalent) resolves its own credentials; it must
# never inherit the orchestrator's keys and silently mis-bill or use the wrong account.
# SSH agent: a forwarded agent in the daemon's env would let ANY net-capable util
# authenticate to hosts outside the machine catalog, routing around the per-routine binding —
# so the agent socket never reaches a util (remote machines carry their own scoped keys).

# Exit code 2 = argparse's bad-arguments convention — a USAGE error (the caller sent
# wrong flags), distinct from a real failure. Part of the util CONTRACT, so telemetry
# (engine executor + the Stats read-model) classifies on it.
USAGE_ERROR_EXIT = 2

# Cap on captured stdout/stderr per util run — observations truncate far below this; the
# cap only stops a runaway printer from ballooning engine memory.
OUTPUT_CAP = 1_000_000

# `["gu", "<sibling>"]` exec sites in util code — the one home for this pattern
# (header_problems flags undeclared calls with it; bootstrap's header migration repairs
# them with the same regex, so the two can never drift).

DISPATCHER = '''#!/usr/bin/env python3
"""gu — run a global util: `gu <name> [args...]`, or `gu list`. Utils call each other
through this dispatcher (this directory is on PATH when a util runs)."""
import os, re, sys, shutil

HOME = os.path.dirname(os.path.abspath(__file__))
UTILS = os.path.join(HOME, "utils")


def _summary(name):
    main_py = os.path.join(UTILS, name, "main.py")
    try:
        src = open(main_py, encoding="utf-8").read()
    except OSError:
        return ""
    m = re.search(r'"""(.+?)(?:\\n|""")', src, re.DOTALL)
    return (m.group(1).strip() if m else "").splitlines()[0] if m else ""


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("list", "-h", "--help"):
        # only real utils: skips __pycache__, removal residue, stray files
        names = sorted(d for d in os.listdir(UTILS)
                       if os.path.isfile(os.path.join(UTILS, d, "main.py"))) \\
            if os.path.isdir(UTILS) else []
        for n in names:
            print(f"{n} — {_summary(n)}")
        if not names:
            print("(no utils yet)")
        return 0
    name, rest = args[0], args[1:]
    main_py = os.path.join(UTILS, name, "main.py")
    if not os.path.isfile(main_py):
        print(f"gu: no util named {name!r} (see 'gu list')", file=sys.stderr)
        return 2
    if not shutil.which("uv"):
        print("gu: 'uv' is required to run utils", file=sys.stderr)
        return 2
    os.execvp("uv", ["uv", "run", "--script", main_py, *rest])
    return 2


if __name__ == "__main__":
    sys.exit(main())
'''


GITIGNORE = "__pycache__/\n*.pyc\n"


def ensure_library(home: Path, *, remote: str = "") -> None:
    """Create the util library if absent (dir + dispatcher + git). If `remote` is set and
    the library does not exist yet, clone it to bootstrap; otherwise init empty.

    A library that ONCE WORKED and has lost its `.git` is a damaged repo, never a fresh one, and
    it is refused rather than re-initialised. Re-initialising discarded the whole history (the
    live library carries 844 commits), wrote the two-line seed `.gitignore` over the real one —
    which excludes `.active/`, `INDEX.md` and `.venv/` — and then committed and PUSHED that
    runtime state, because the library repo has a post-commit push hook. Recovery from that is
    manual either way, so the loud refusal is strictly better than the silent rewrite. The caller
    (appwiring's lifespan) already tolerates a library failure, so boot is unaffected.

    The discriminator is the `gu` DISPATCHER, not "the directory has files in it". `gu` is
    installed by this function and by nothing else, so its presence means this call has succeeded
    here before — and therefore that a repo existed. A directory someone has populated but never
    run this against (a seed copy, a restore-in-progress, a test fixture) has no dispatcher and is
    still a fresh tree.
    """
    if home.exists() and (home / ".git").exists():
        _install_dispatcher(home)
        return
    if (home / "gu").exists():
        log.error("library at %s has a dispatcher but no .git — it is a repo that lost its "
                  "history, not a fresh tree, so it will NOT be re-initialised (that would "
                  "discard the history and push runtime state). Restore the repo from a backup "
                  "or its remote, or `git init` it by hand if you really want a new one.", home)
        return
    home.parent.mkdir(parents=True, exist_ok=True)
    if remote and not home.exists():
        r = subprocess.run(["git", "clone", "--quiet", remote, str(home)],
                           capture_output=True, text=True, timeout=120, check=False)
        if r.returncode == 0:
            for key, val in libgit.IDENTITY_PAIRS:
                libgit.git(home, "config", key, val)
            _install_dispatcher(home)
            return
        # clone failed (e.g. empty/absent remote) → fall through to init
    home.mkdir(parents=True, exist_ok=True)
    (home / "utils").mkdir(exist_ok=True)
    (home / ".gitignore").write_text(GITIGNORE, encoding="utf-8")
    _install_dispatcher(home)
    libgit.init_repo(home, remote=remote, first_commit="init util library")


def _install_dispatcher(home: Path) -> None:
    """Install our minimal `gu` dispatcher + push hook — but NEVER overwrite an existing one.
    When the library root already carries its own richer `gu`, we leave its dispatcher and
    hook untouched and just use them.
    """
    gu = home / "gu"
    if not gu.exists():
        gu.write_text(DISPATCHER, encoding="utf-8")
        gu.chmod(0o755)  # the dispatcher is a shared executable by design
    libgit.install_push_hook(home)


def util_dir(home: Path, name: str) -> Path:
    return home / "utils" / name


def exists(home: Path, name: str) -> bool:
    return util_dir(home, name).joinpath("main.py").is_file()


def list_utils(home: Path) -> list[dict]:
    root = home / "utils"
    if not root.is_dir():
        return []
    out = []
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        main_py = d / "main.py"
        if not main_py.is_file():
            continue
        out.append({"name": d.name, **parse_header(main_py.read_text(encoding="utf-8"))})
    return out




def catalog_text(home: Path) -> str:
    utils = list_utils(home)
    if not utils:
        return ("(no global utils yet — create one with the write_util action when you need "
                "to run code)")
    # This IS the discovery surface (the util action's `name=list`) — each entry teaches the
    # parameters too, or the model's first call is a guess. Pass usage flags via `args` as a
    # JSON array of strings.
    lines = []
    for u in utils:
        head = u["summary"] or u["name"]
        if not head.startswith(u["name"]):
            head = f"{u['name']} — {head}"
        lines.append(f"- {head}")
        if u.get("usage"):
            lines.append(f"    {u['usage']}")
    lines.append('\nCall shape: {"say": "…", "kind": "util", "name": "<name>", '
                 '"args": ["<arg>", "--flag"]} — args is a JSON array of strings.')
    lines.append('Read a util\'s source with {"kind": "util", "name": "show", '
                 '"args": ["<name>"]} — do this before revising one with write_util.')
    return "\n".join(lines)


def search_utils(home: Path, query: str, limit: int = 12) -> list[dict]:
    """Keyword-rank the live util catalog against a free-text query — the two-phase
    discovery path (D52 Phase 3): a run names what it needs, gets the handful of most
    relevant utils + summaries, then `util name=list args=["<name>"]` for exact usage.
    PURE in-process ranking over the live catalog (name/tags/summary/usage) — NOT the
    prose FTS5 index (search/index.py), which is daemon-owned and engine subprocesses
    never import. Scoring: each query term matched case-insensitively; a hit in the NAME
    weighs most, then tags, then summary, then usage. Zero-match utils drop; ties break
    on name. Returns the top `limit` catalog entries.
    """
    terms = [t for t in re.split(r"[^a-z0-9]+", query.lower()) if t]
    if not terms:
        return []
    scored: list[tuple[int, str, dict]] = []
    for u in list_utils(home):
        name = u["name"].lower()
        tags = " ".join(u.get("tags") or []).lower()
        summary = (u.get("summary") or "").lower()
        usage = (u.get("usage") or "").lower()
        score = 0
        for t in terms:
            if t in name:
                score += 8
            if t in tags:
                score += 4
            if t in summary:
                score += 2
            if t in usage:
                score += 1
        if score:
            scored.append((score, u["name"], u))
    scored.sort(key=lambda s: (-s[0], s[1]))
    return [u for _score, _name, u in scored[:limit]]


def search_listing(home: Path, query: str, limit: int = 12) -> str:
    """Render search_utils() as the same summary+usage lines catalog_text uses, always
    naming the always-on category floor so a retrieval miss never fully hides a tool
    (the dominant failure mode of any tool-search layer).
    """
    hits = search_utils(home, query, limit=limit)
    floor = ('The FULL catalog is always in your CAPABILITIES section (grouped by domain), '
             'so nothing is hidden — scan it directly if nothing above fits, or write a new '
             'util with write_util. Run `util name=list args=["<name>"]` for a util\'s '
             'exact flags.')
    if not hits:
        return f"No util name/tags/summary/usage matched {query!r}. " + floor
    lines = []
    for u in hits:
        head = u["summary"] or u["name"]
        if not head.startswith(u["name"]):
            head = f"{u['name']} — {head}"
        lines.append(f"- {head}")
        if u.get("usage"):
            lines.append(f"    {u['usage']}")
    lines.append("\nClosest matches only. " + floor)
    return "\n".join(lines)


def read_util(home: Path, name: str) -> str | None:
    p = util_dir(home, name) / "main.py"
    return p.read_text(encoding="utf-8") if p.is_file() else None


def write_util_file(home: Path, name: str, content: str) -> None:
    if not is_slug(name):   # backstop — a non-slug would write OUTSIDE utils/
        raise ValueError(f"invalid util name {name!r}")
    d = util_dir(home, name)
    d.mkdir(parents=True, exist_ok=True)
    # Atomic (tmp+rename): a routine EXECUTING this util concurrently (the curator revising a
    # util another run is calling) sees the old or new main.py whole, never a torn read.
    atomic_write(d / "main.py", content)


def referenced_by(home: Path, name: str) -> list[str]:
    """Utils that declare `name` on their docstring `calls:` line — the reverse-dependents
    that would break if `name` were removed. The engine's remove_util action refuses on a
    non-empty result (mirrors the `gu remove` no-callers refusal).
    """
    return sorted(u["name"] for u in list_utils(home)
                  if u["name"] != name and name in (u.get("calls") or []))


def remove_util_file(home: Path, name: str) -> None:
    """Delete a util's whole <name>/ dir (un-sandboxed, engine-side — the counterpart to
    write_util_file). Committed by the caller via git_commit, so it stays recoverable from
    git history. The no-callers guard lives in the remove_util action handler, not here.
    """
    if not is_slug(name):   # backstop — a non-slug would delete OUTSIDE utils/
        raise ValueError(f"invalid util name {name!r}")
    d = util_dir(home, name)
    if not d.exists():
        return
    # Rename-aside then delete: the dir vanishes atomically for a concurrent reader
    # (list_utils / a `gu <name>` about to exec), never a half-emptied tree. The dotted
    # aside name is invisible to the `*/main.py` glob and outside the scoped `git add`.
    aside = d.with_name(f".{d.name}.removing.{os.getpid()}")
    try:
        d.rename(aside)
    except OSError:
        shutil.rmtree(d, ignore_errors=True)
        return
    shutil.rmtree(aside, ignore_errors=True)


def was_deleted(home: Path, name: str) -> bool:
    """Was utils/<name>/main.py ever DELETED from the library's git history? The engine's
    never-recreate rule keys off this (interact.recreate_denial), as does the boot seed-sync.
    The git question itself is `libgit.path_was_deleted`, shared with the library-doc sync.
    """
    return libgit.path_was_deleted(home, f"utils/{name}/main.py")


def git_commit(home: Path, message: str, *, paths: Sequence[str] | None = None) -> bool:
    """Commit library changes under the shared repo lock (see libgit.commit). `paths` scopes
    the stage to the util(s) this call touched so a concurrent writer's commit can't sweep
    them — write_util / remove_util pass `utils/<name>`.
    """
    return libgit.commit(home, message, paths=paths)
