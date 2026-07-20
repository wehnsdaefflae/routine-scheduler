"""Scheduler-managed global utils — the ONLY way routines run code.

Each util is a PEP 723 script at <library>/utils/<name>/main.py, run via `uv run --script`.
A `gu` dispatcher lives at the library root so utils compose by calling each other
(`gu <sibling> --json`). The library repo is git-backed (neutral identity, best-effort push
hook) and can bootstrap from / sync to a remote. It works empty — routines generate the
utils they need.

Routines have NO shell action; all code execution is mediated here, through named,
selftested, git-committed (and optionally human-approved) utils.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

from . import sandbox
from .ids import is_slug

# LLM-auth vars scrubbed from util subprocesses UNCONDITIONALLY (declared or not): a util
# that needs an LLM (e.g. a `gu claude` equivalent) resolves its own credentials; it must
# never inherit the orchestrator's keys and silently mis-bill or use the wrong account.
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


def _git(home: Path, *args: str, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(home), *args], capture_output=True,
                          text=True, timeout=30, check=check)


def ensure_library(home: Path, *, remote: str = "") -> None:
    """Create the util library if absent (dir + dispatcher + git). If `remote` is set and
    the library does not exist yet, clone it to bootstrap; otherwise init empty.
    """
    if home.exists() and (home / ".git").exists():
        _install_dispatcher(home)
        return
    home.parent.mkdir(parents=True, exist_ok=True)
    if remote and not home.exists():
        r = subprocess.run(["git", "clone", "--quiet", remote, str(home)],
                           capture_output=True, text=True, timeout=120, check=False)
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
    When the library root already carries its own richer `gu`, we leave its dispatcher and
    hook untouched and just use them.
    """
    gu = home / "gu"
    if not gu.exists():
        gu.write_text(DISPATCHER, encoding="utf-8")
        gu.chmod(0o755)  # the dispatcher is a shared executable by design
    hook = home / ".git" / "hooks" / "post-commit"
    if (home / ".git").is_dir() and not hook.exists():
        hook.write_text(POST_COMMIT_HOOK, encoding="utf-8")
        hook.chmod(0o755)  # git hooks must be executable


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


_SUMMARY_RE = re.compile(r'"""(.+?)"""', re.DOTALL)


def parse_header(src: str) -> dict:
    """The docstring header — the util's ONLY machine-read surface: summary, usage, tags,
    declared secrets, declared sibling `calls:` (drives transitive secret/net resolution,
    see util_needs), and the `net:` declaration ("outbound" | "none"; "" = undeclared,
    which the sandbox treats as none — fail closed).
    """
    m = _SUMMARY_RE.search(src)
    doc = m.group(1).strip() if m else ""
    lines = [ln.strip() for ln in doc.splitlines() if ln.strip()]
    summary = lines[0] if lines else ""
    usage = next((ln for ln in lines if ln.lower().startswith("usage:")), "")
    tags_line = next((ln for ln in lines if ln.lower().startswith("tags:")), "")
    tags = ([t.strip() for t in tags_line[len("tags:"):].split(",") if t.strip()]
            if tags_line else [])
    sec_line = next((ln for ln in lines if ln.lower().startswith("secrets:")), "")
    secrets = [s.strip() for s in sec_line[len("secrets:"):].split(",")
               if s.strip() and s.strip().lower() != "(none)"] if sec_line else []
    calls_line = next((ln for ln in lines if ln.lower().startswith("calls:")), "")
    calls = [c.strip() for c in calls_line[len("calls:"):].split(",")
             if c.strip() and c.strip().lower() not in ("(none)", "none")
             and is_slug(c.strip())] if calls_line else []
    net_line = next((ln for ln in lines if ln.lower().startswith("net:")), "")
    net = net_line[len("net:"):].strip().lower() if net_line else ""
    return {"summary": summary, "usage": usage, "tags": tags, "secrets": secrets,
            "calls": calls, "net": net}


# env-var names that smell like credentials — used by header_problems to catch a util that
# reads a secret it never declared (the Settings page can only prompt for DECLARED secrets,
# and the sandbox injects only declared ones). Three read shapes are detected:
#   direct    — os.environ["NAME"] / os.environ.get("NAME") / os.getenv("NAME")
#   indirect  — VAR = "NAME"  then  os.environ[VAR] / os.getenv(VAR)  (the `gu claude`
#               pattern: TOKEN_VAR = "CLAUDE_CODE_OAUTH_TOKEN"; a var-keyed read alone can't
#               name the secret, so we resolve module-level string-literal constants)
#   grouped   — KEYS = ("A_PASS", "B_TOKEN", …)  then  for k in KEYS: os.environ.get(k)
#               (the `ftp` pattern: a loop var over a tuple/list of names — when the env key is
#               a var we cannot pin to ONE literal, every credential-shaped name grouped in a
#               module-level tuple/list counts as read; err toward "declare it")
_ENV_READ = r"""os\.(?:environ(?:\.get\(|\[)|getenv\()\s*"""
_SECRETISH = re.compile(_ENV_READ + r"""["']([A-Z][A-Z0-9_]*)["']""")
_ENV_VAR_KEY = re.compile(_ENV_READ + r"""([A-Za-z_][A-Za-z0-9_]*)\b""")
_CONST_ASSIGN = re.compile(r"""^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*["']([A-Z][A-Z0-9_]*)["']""",
                           re.MULTILINE)
_GROUP_ASSIGN = re.compile(r"""^\s*[A-Za-z_][A-Za-z0-9_]*\s*=\s*[(\[]([^)\]]*)[)\]]""",
                           re.MULTILINE)
_LITERAL = re.compile(r"""["']([A-Z][A-Z0-9_]*)["']""")
_SECRET_HINT = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PASS|CREDENTIAL)S?$")


def _secrets_read(content: str) -> set[str]:
    """Credential-shaped env var NAMES the code reads — directly, indirectly through a
    module-level string constant, or (when the env key is a variable we can't pin to one
    literal, e.g. a loop var) any credential-shaped name grouped in a tuple/list constant.
    """
    used = {v for v in _SECRETISH.findall(content) if _SECRET_HINT.search(v)}
    consts = dict(_CONST_ASSIGN.findall(content))
    var_keys = _ENV_VAR_KEY.findall(content)
    for var in var_keys:
        literal = consts.get(var)
        if literal and _SECRET_HINT.search(literal):
            used.add(literal)
    # A var-keyed env read that resolves to no single constant (a loop over a tuple of names):
    # count every credential-shaped literal grouped in a module-level tuple/list, since we can't
    # tell which one the loop touches. Only triggers when such an unresolved read exists.
    if any(var not in consts for var in var_keys):
        for group in _GROUP_ASSIGN.findall(content):
            used |= {lit for lit in _LITERAL.findall(group) if _SECRET_HINT.search(lit)}
    return used


def undeclared_secrets(content: str) -> list[str]:
    """Credential-looking env vars the code reads but the docstring `secrets:` line does
    not declare — the gap header_problems rejects (and the header migration repairs).
    """
    declared = {s.upper() for s in parse_header(content)["secrets"]}
    return sorted(_secrets_read(content) - declared)


def header_problems(content: str) -> list[str]:
    """Doc-standard gate for saving a util. The docstring header is the util's ONLY
    machine-read surface (catalog, Settings secrets page, the sandbox): it must carry a
    summary, a usage: line, at least one tag, a secrets: declaration covering every
    credential-looking env var the code reads, and a net: declaration. Comment-form
    `# secrets:` lines above the docstring are invisible to the parser — that is exactly
    the failure this gate stops.
    """
    h = parse_header(content)
    problems = []
    if not h["summary"]:
        problems.append("no module docstring — the first line must be '<name> — <summary>'")
    if not h["usage"]:
        problems.append("docstring needs a 'usage: gu <name> …' line")
    if not h["tags"]:
        problems.append("docstring needs a 'tags: <tag>, <tag>, …' line (at least one tag)")
    if h["net"] not in ("outbound", "none"):
        problems.append("docstring needs a 'net: outbound' or 'net: none' line — declare "
                        "whether this util opens network connections; the sandbox denies "
                        "all TCP to a util declaring none (or declaring nothing)")
    undeclared = undeclared_secrets(content)
    if undeclared:
        problems.append("code reads credential env var(s) not declared in the docstring's "
                        f"'secrets:' line: {', '.join(undeclared)} — declare them there "
                        "(the Settings page only prompts for declared secrets, and the "
                        "sandbox injects only declared ones)")
    return problems


def catalog_text(home: Path) -> str:
    utils = list_utils(home)
    if not utils:
        return ("(no global utils yet — create one with the write_util action when you need "
                "to run code; there is NO shell action)")
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


def read_util(home: Path, name: str) -> str | None:
    p = util_dir(home, name) / "main.py"
    return p.read_text(encoding="utf-8") if p.is_file() else None


def write_util_file(home: Path, name: str, content: str) -> None:
    d = util_dir(home, name)
    d.mkdir(parents=True, exist_ok=True)
    (d / "main.py").write_text(content, encoding="utf-8")


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
    d = util_dir(home, name)
    if d.exists():
        shutil.rmtree(d)


def util_needs(home: Path, name: str) -> tuple[set[str], bool]:
    """(declared secret env vars, net-outbound?) for one util, resolved TRANSITIVELY across
    its docstring `calls:` siblings — the whole call tree runs inside ONE jail and ONE env,
    so a caller inherits what its callees declared (gmail-body-dump calls gmail → gets the
    GMAIL_* secrets; anything calling a net: outbound sibling needs the network open too).
    Undeclared = not granted: an unknown net line (or none at all) contributes nothing.
    """
    secrets: set[str] = set()
    net = False
    seen: set[str] = set()
    stack = [name]
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        src = read_util(home, current)
        if src is None:
            continue
        header = parse_header(src)
        secrets.update(s.upper() for s in header["secrets"])
        net = net or header["net"] == "outbound"
        stack += header["calls"]
    return secrets, net


def _child_env(home: Path, name: str, extra_secrets: dict[str, str] | None = None) -> dict:
    """A util subprocess's environment: the central secrets store injects ONLY the vars the
    util (or a `calls:` sibling) declares; every other store key is scrubbed even when the
    daemon's own environment carries it — an undeclared secret must not reach the child by
    any route. STRIP_VARS (LLM keys) are removed unconditionally, declared or not.

    `extra_secrets` are non-store secrets the engine resolves per run — today a routine's OAuth
    connection access tokens (<PROVIDER>_ACCESS_TOKEN). They obey the SAME rule: injected only if
    the util declares the var, scrubbed otherwise — the declared-only invariant covers them too.
    """
    from .secrets import load_secrets
    declared, _ = util_needs(home, name)
    env = {**os.environ}
    for key, value in {**load_secrets(), **(extra_secrets or {})}.items():
        if key.upper() in declared:
            env[key] = value
        else:
            env.pop(key, None)
    for k in STRIP_VARS:
        env.pop(k, None)                # never LLM keys: utils bill only via `gu claude`
    return env


def run_util(home: Path, name: str, args: list[str], *, timeout: int = 300,
             policy: sandbox.SandboxPolicy,
             extra_secrets: dict[str, str] | None = None) -> tuple[int, str, str]:
    """Controlled runner: only a named util from THIS library, uv-run, scoped env (declared
    secrets only, plus any `extra_secrets` the engine resolved for this run — same declared-only
    rule), library root on PATH (so the util can call siblings via `gu`), inside the Landlock jail
    `policy` + the util's own `net:` declaration describe (sandbox.wrap; the server `sandbox:` mode
    decides strict/permissive/off). Returns (exit, out, err).
    """
    if not is_slug(name):
        return 2, "", f"invalid util name {name!r}"
    if not exists(home, name):
        return 2, "", f"no util named {name!r} (available: {[u['name'] for u in list_utils(home)]})"
    if not shutil.which("uv"):
        return 2, "", "uv is required to run utils but is not on PATH"
    env = _child_env(home, name, extra_secrets)
    env["PATH"] = f"{home}:{env.get('PATH', '')}"
    # Point the `gu` dispatcher (on PATH, for sibling calls) at THIS library, so a util that
    # shells out to `gu <sibling>` always resolves siblings here.
    env["GLOBAL_UTILS_HOME"] = str(home)
    _, net = util_needs(home, name)
    try:
        cmd = sandbox.wrap(["uv", "run", "--script", str(util_dir(home, name) / "main.py"),
                            *args], policy=policy, utils_home=home, net=net)
    except sandbox.SandboxRefusal as exc:
        return 2, "", str(exc)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           env=env, cwd=str(home), check=False)
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return -1, "", f"util {name!r} timed out after {timeout}s"


def selftest(home: Path, name: str, *, timeout: int = 120,
             policy: sandbox.SandboxPolicy) -> tuple[bool, str]:
    code, out, err = run_util(home, name, ["--selftest"], timeout=timeout, policy=policy)
    return code == 0, (err or out).strip()


def was_deleted(home: Path, name: str) -> bool:
    """Was utils/<name>/main.py ever DELETED from the library's git history? The engine's
    never-recreate rule keys off this (interact.recreate_denial), as does the boot seed-sync
    (a user-deleted seed util is never resurrected). Any deletion counts — the web UI is the
    only deliberate delete path today, and treating every prior deletion as user intent is
    the safe reading. Fails open to False (no repo / git error = nothing to guard).
    """
    try:
        r = _git(home, "log", "--diff-filter=D", "--format=%h", "--",
                 f"utils/{name}/main.py")
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0 and bool(r.stdout.strip())


def git_commit(home: Path, message: str) -> bool:
    try:
        _git(home, "add", "-A")
        r = _git(home, "commit", "-qm", message)
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False
