"""The util HEADER contract — what a util must declare about itself, and what it may read.

Split out of `utils_lib.py` (F393). This is the authoring contract, not the store: the six-line
docstring header every util carries (summary, usage, calls, tags, secrets, net) and the checks
that make a header honest. `header_problems` is what gates `write_util`, so an over-tolerant
reading here silently admits a util nobody can audit.

The `secrets:` line is the load-bearing one — it is the ONLY thing that decides which
credentials the subprocess env carries (utils_lib.scoped_env), so `undeclared_secrets` reads
the SOURCE for names the header does not declare rather than trusting the header alone. A
trailing `?` marks a secret as OPTIONAL: withheld silently when the user has not granted it,
because a degraded call beats a stalled run.
"""

from __future__ import annotations

import re

from .ids import is_slug

_SUMMARY_RE = re.compile(r'"""(.+?)"""', re.DOTALL)
GU_CALL_RE = re.compile(r"""\[\s*["']gu["']\s*,\s*["']([a-z0-9][a-z0-9-]*)["']""")

def parse_header(src: str) -> dict:
    """The docstring header — the util's ONLY machine-read surface: summary, usage, tags,
    declared secrets, declared sibling `calls:` (drives transitive secret/net resolution,
    see util_needs), and the `net:` declaration ("outbound" | "none"; "" = undeclared,
    which the sandbox treats as none — fail closed).

    A `secrets:` entry may carry a trailing `?` (e.g. `secrets: FOO_KEY, BAR_TOKEN?`) to mark
    an OPTIONAL secret (D51): injected when the store has it, but the Settings page does not
    prompt for it and its absence is not a "missing credential". The stripped name still
    appears in `secrets` (so injection and the undeclared-read gate are unchanged); the marked
    names are also collected in `optional_secrets`.
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
    sec_raw = [s.strip() for s in sec_line[len("secrets:"):].split(",")
               if s.strip() and s.strip().lower() != "(none)"] if sec_line else []
    # A trailing '?' marks an OPTIONAL secret (D51): the sandbox injects it when the store has it
    # but the Settings page never prompts for it and its absence is not a missing credential. The
    # name (marker stripped) still appears in `secrets`, so injection and the undeclared-read gate
    # are unchanged — optionality is purely about consent/prompting, not access.
    secrets = [s[:-1].strip() if s.endswith("?") else s for s in sec_raw]
    optional_secrets = [s[:-1].strip() for s in sec_raw if s.endswith("?")]
    calls_line = next((ln for ln in lines if ln.lower().startswith("calls:")), "")
    calls = [c.strip() for c in calls_line[len("calls:"):].split(",")
             if c.strip() and c.strip().lower() not in ("(none)", "none")
             and is_slug(c.strip())] if calls_line else []
    net_line = next((ln for ln in lines if ln.lower().startswith("net:")), "")
    net = net_line[len("net:"):].strip().lower() if net_line else ""
    return {"summary": summary, "usage": usage, "tags": tags, "secrets": secrets,
            "optional_secrets": optional_secrets, "calls": calls, "net": net, "doc": doc}


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
    declared_calls = set(h["calls"])
    undeclared_calls = sorted({c for c in GU_CALL_RE.findall(content)
                               if c not in declared_calls})
    if undeclared_calls:
        problems.append("code execs sibling util(s) not declared on the docstring's "
                        f"'calls:' line: {', '.join(undeclared_calls)} — declare them "
                        "(the sandbox resolves secrets and net access transitively over "
                        "declared calls; an undeclared sibling runs without its needs)")
    return problems
