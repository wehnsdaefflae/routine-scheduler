"""Scheduler-managed global-util library — the ONLY way routines run code.

Mirrors the original global-utils pattern: each util is a PEP 723 script at
<utils_home>/utils/<name>/main.py, run via `uv run --script`. A `gu` dispatcher lives at
the library root so utils compose by calling each other (`gu <sibling> --json`). The
library is git-backed (neutral identity, best-effort push hook) and can bootstrap from /
sync to a remote. It works empty — routines generate the utils they need.

Routines have NO shell action; all code execution is mediated here, through named,
selftested, git-committed (and optionally human-approved) utils.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

from .ids import is_slug
from .paths import expand

# LLM-auth vars scrubbed from util subprocesses: a util that needs an LLM (e.g. a
# `gu claude` equivalent) resolves its own credentials; it must never inherit the
# orchestrator's keys and silently mis-bill or use the wrong account.
STRIP_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_KEY", "ANTHROPIC_AUTH_TOKEN",
              "ANTHROPIC_BASE_URL", "ANTHROPIC_CUSTOM_HEADERS",
              "OPENROUTER_KEY", "OPENROUTER_API_KEY", "OPENAI_API_KEY")

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
        names = sorted(d for d in os.listdir(UTILS)) if os.path.isdir(UTILS) else []
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

POST_COMMIT_HOOK = """#!/usr/bin/env bash
branch="$(git symbolic-ref --short HEAD 2>/dev/null)" || exit 0
git remote get-url origin >/dev/null 2>&1 || exit 0
timeout 20 git push --quiet origin "$branch" 2>&1 || true
exit 0
"""

GITIGNORE = "__pycache__/\n*.pyc\n"


def utils_home(server) -> Path:
    return server.utils_home


def _git(home: Path, *args: str, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(home), *args], capture_output=True,
                          text=True, timeout=30, check=check)


def ensure_library(home: Path, *, remote: str = "") -> None:
    """Create the util library if absent (dir + dispatcher + git). If `remote` is set and
    the library does not exist yet, clone it to bootstrap; otherwise init empty."""
    if home.exists() and (home / ".git").exists():
        _install_dispatcher(home)
        return
    home.parent.mkdir(parents=True, exist_ok=True)
    if remote and not home.exists():
        r = subprocess.run(["git", "clone", "--quiet", remote, str(home)],
                           capture_output=True, text=True, timeout=120)
        if r.returncode == 0:
            _configure_repo(home)
            _install_dispatcher(home)
            return
        # clone failed (e.g. empty/absent remote) → fall through to init
    home.mkdir(parents=True, exist_ok=True)
    (home / "utils").mkdir(exist_ok=True)
    (home / ".gitignore").write_text(GITIGNORE, encoding="utf-8")
    _install_dispatcher(home)
    _git(home, "init", "-q", "-b", "main")
    _configure_repo(home)
    if remote:
        _git(home, "remote", "add", "origin", remote)
    _git(home, "add", "-A")
    _git(home, "commit", "-qm", "init util library")


def _configure_repo(home: Path) -> None:
    _git(home, "config", "user.name", "routine-scheduler")
    _git(home, "config", "user.email", "noreply@routine-scheduler.local")


def _install_dispatcher(home: Path) -> None:
    """Install our minimal `gu` dispatcher + push hook — but NEVER overwrite an existing one.
    When utils_home points at a pre-existing library (e.g. the user's ~/.local/share/global-utils
    with its own richer `gu`), we leave its dispatcher and hook untouched and just use them."""
    gu = home / "gu"
    if not gu.exists():
        gu.write_text(DISPATCHER, encoding="utf-8")
        os.chmod(gu, 0o755)
    hook = home / ".git" / "hooks" / "post-commit"
    if (home / ".git").is_dir() and not hook.exists():
        hook.write_text(POST_COMMIT_HOOK, encoding="utf-8")
        os.chmod(hook, 0o755)


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
        out.append({"name": d.name, **_parse_header(main_py.read_text(encoding="utf-8"))})
    return out


_SUMMARY_RE = re.compile(r'"""(.+?)"""', re.DOTALL)


def _parse_header(src: str) -> dict:
    m = _SUMMARY_RE.search(src)
    doc = m.group(1).strip() if m else ""
    lines = [ln.strip() for ln in doc.splitlines() if ln.strip()]
    summary = lines[0] if lines else ""
    usage = next((ln for ln in lines if ln.lower().startswith("usage:")), "")
    tags_line = next((ln for ln in lines if ln.lower().startswith("tags:")), "")
    tags = [t.strip() for t in tags_line[len("tags:"):].split(",") if t.strip()] if tags_line else []
    return {"summary": summary, "usage": usage, "tags": tags}


def catalog_text(home: Path) -> str:
    utils = list_utils(home)
    if not utils:
        return ("(no global utils yet — create one with the write_util action when you need "
                "to run code; there is NO shell action)")
    # Names + one-line summaries only (no usage lines): keeps the prompt lean and avoids
    # priming weak models toward a tool-call format. Full usage is one `util name=list` away.
    lines = []
    for u in utils:
        head = u["summary"] or u["name"]
        if not head.startswith(u["name"]):
            head = f"{u['name']} — {head}"
        lines.append(f"- {head}")
    return "\n".join(lines)


def read_util(home: Path, name: str) -> str | None:
    p = util_dir(home, name) / "main.py"
    return p.read_text(encoding="utf-8") if p.is_file() else None


def write_util_file(home: Path, name: str, content: str) -> None:
    d = util_dir(home, name)
    d.mkdir(parents=True, exist_ok=True)
    (d / "main.py").write_text(content, encoding="utf-8")


def _child_env() -> dict:
    from .secrets import load_secrets
    env = {**os.environ, **load_secrets()}      # central secrets store → utils (env-first)
    for k in STRIP_VARS:
        env.pop(k, None)                         # …but never LLM keys: utils bill only via `gu claude`
    return env


def run_util(home: Path, name: str, args: list[str], *, timeout: int = 300
             ) -> tuple[int, str, str]:
    """Controlled runner: only a named util from THIS library, uv-run, scrubbed env,
    library root on PATH (so the util can call siblings via `gu`). Returns (exit, out, err)."""
    if not is_slug(name):
        return 2, "", f"invalid util name {name!r}"
    if not exists(home, name):
        return 2, "", f"no util named {name!r} (available: {[u['name'] for u in list_utils(home)]})"
    if not shutil.which("uv"):
        return 2, "", "uv is required to run utils but is not on PATH"
    env = _child_env()
    env["PATH"] = f"{home}:{env.get('PATH', '')}"
    try:
        r = subprocess.run(["uv", "run", "--script", str(util_dir(home, name) / "main.py"), *args],
                           capture_output=True, text=True, timeout=timeout, env=env, cwd=str(home))
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return -1, "", f"util {name!r} timed out after {timeout}s"


def selftest(home: Path, name: str, *, timeout: int = 120) -> tuple[bool, str]:
    code, out, err = run_util(home, name, ["--selftest"], timeout=timeout)
    return code == 0, (err or out).strip()


def git_commit(home: Path, message: str) -> bool:
    try:
        _git(home, "add", "-A")
        r = _git(home, "commit", "-qm", message)
        return r.returncode == 0
    except OSError:
        return False
