"""The HOST paths every jailed command gets whatever the routine was granted — and the one
reading of "is this path inside the jail at all?".

Split out of `sandbox.py`, which had no room left under the 350-line house bar and which names
this as a responsibility of its own: these tables are not about a run's grants. They are the
system trees a command needs to EXECUTE (interpreters, certificates, DNS), the temp space it
writes scratch into, and the few HOME-scoped toolchain paths utils doing git or LLM work need.
`sandbox.wrap` composes them with what the run holds; nothing here reads a policy.

`inside_the_jail` lives with them because it answers over exactly this list plus the policy's
granted roots, and a SECOND copy of that arithmetic is how an observation comes to tell a run
its path was outside a jail that in fact contained it. The engine's own diagnostic
(`engine/jailnotes.blocked_paths`) asks here rather than rebuilding the list.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

# System trees every util needs to EXECUTE at all (read+execute): interpreters, libraries,
# /etc (DNS, SSL certs, locale), /proc + /sys (uv, headless chromium), /run (resolved DNS),
# /var/log (host service logs, read through a `shell` command). DAC still applies underneath —
# Landlock only ever SUBTRACTS access. The daemon-user's HOME is deliberately NOT here: the
# high-value targets (~/.config/routine-scheduler/secrets.env, ~/.credentials, ~/.ssh) stay
# invisible.
SYSTEM_RO = ("/usr", "/bin", "/sbin", "/lib", "/lib32", "/lib64", "/etc", "/opt",
             "/run", "/sys", "/proc", "/var/log")
SYSTEM_RW = ("/tmp", "/var/tmp", "/dev")  # noqa: S108 — jail roots, not temp-file handling
# HOME-scoped toolchain, read-only: launchers, the git/gh identity utils need to push with,
# and the host `claude` CLI install (a symlink into .local/share/claude on host installs; in
# the container it lives under /usr, already covered). The price of utils doing git/LLM work;
# documented in docs/sandboxing.md.
HOME_RO = (".local/bin", ".local/share/claude", ".gitconfig", ".config/git", ".config/gh")
# HOME-scoped tool state, read-write: uv's script envs + managed pythons, XDG cache/state
# (playwright browsers, tool caches), and the claude CLI's session state — sanctioned tools
# of the system (used by independent Claude CLI utilities).
HOME_RW = (".cache", ".local/share/uv", ".local/state", ".claude", ".claude.json")


def toolchain() -> tuple[list[str], list[str]]:
    """`(read-only, read-write)` host paths every jailed command gets, this interpreter's own
    prefixes and `uv`'s directory among them.
    """
    home = Path.home()
    ro = [*SYSTEM_RO, sys.prefix, sys.base_prefix,
          *(str(home / rel) for rel in HOME_RO)]
    if uv := shutil.which("uv"):
        ro.append(str(Path(uv).resolve().parent))
    rw = [*SYSTEM_RW, tempfile.gettempdir(), *(str(home / rel) for rel in HOME_RW)]
    return ro, rw


def mounted_roots(policy) -> list[str]:
    """Every host path a command under `policy` can reach: the toolchain above, the routine's
    own dir, and the roots the run was granted.

    Deliberately WIDER than the real ruleset and never narrower, because its only consumer is
    a diagnostic that says "this path is outside your jail": the own dir is mounted
    child-by-child with some children sealed (`ownseal`), and a declared private store is
    admitted per util — so answering over the parent dir can only make this say LESS about a
    path, never wrongly accuse the jail of blocking one it allows. A false "outside the jail"
    sends a reader after the wrong repair; a missing note costs nothing that was not already
    missing.
    """
    ro, rw = toolchain()
    roots = [*ro, *rw]
    if (own := getattr(policy, "own_dir", None)) is not None:
        roots.append(str(own))
    for attr in ("read_roots", "write_roots"):
        roots += [str(p) for p in (getattr(policy, attr, ()) or ())]
    return roots
