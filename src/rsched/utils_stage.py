"""Util candidate STAGING — selftest a util OUT of the live path, install it only on green.

F584 / D161. `write_util` and the web Library editor share ONE transaction: put the candidate
somewhere, run its `--selftest`, commit on green. Until this module that somewhere was
`utils/<name>` ITSELF, restored to the previous text on failure. The rollback was prompt and
correct; the EXPOSURE WINDOW was the defect. For the whole selftest — a 120 s timeout, plus a
dependency prewarm for a `net: outbound` script, and six failing selftests were measured
back-to-back about 6 s apart in global-utils-review:20260928-052504 — the live library held
UNTESTED code, and any routine whose `gu <name>` landed in that window ran it. The library is
the one artefact every routine shares and library-sync mirrors it nightly, so the window was
open on every util revision the fleet ever made.

WHY A CANDIDATE NAME AND NOT A TEMPORARY LIBRARY HOME. The candidate is staged as a real util
under a throwaway slug inside the LIVE library (`staging--<name>-<pid>-<token>`), and
`utils/<name>` is written only after the selftest passes. A selftest is entitled to the real
thing, and everything it is entitled to is keyed on (library home, util name):

* sibling calls — a util may exec `gu <sibling>` for anything on its `calls:` line, and `gu`
  resolves siblings from the dispatcher's OWN directory (`utils_lib.DISPATCHER`), which
  `utils_run.run_util` puts on PATH as `GLOBAL_UTILS_HOME`;
* its declared needs — secrets, `net:`, `fs:` and verb scope come from
  `utils_run.util_needs(home, name)` / `utils_verbs`, which READ THE SOURCE under that name.
  Staged inside the library they are read from the CANDIDATE, so a creation is tested under
  the declarations it brings rather than under those of a util that does not exist yet;
* the jail — `sandbox.wrap` mounts `libraries_home` read-only and subtracts the private
  stores the library's other utils declare.

A separate temporary home would have to reproduce all of that — the dispatcher, every sibling,
the private-store subtraction — and a good util failing its selftest because a sibling went
missing is a worse defect than the one this fixes. A staged dir is hidden from every discovery
surface instead (`utils_lib.list_utils` and the `gu` dispatcher both skip the prefix), so no
caller meets a candidate in the catalog, and the install is `write_util_file`'s atomic
tmp+rename: `gu <name>` goes from the old selftested script to the new selftested one with
nothing in between.

ON EVERY FAILURE PATH THE LIBRARY IS UNTOUCHED BY CONSTRUCTION. There is no `previous` text to
restore any more, because nothing was ever written to `utils/<name>`: a failing assertion, a
timeout, an aborted run and an exception all end the same way — the staged dir goes in a
`finally` and `utils/<name>` is exactly what it was. That also disposes of the failure mode the
old shape could not cover: a rollback write that itself fails.

CONCURRENCY. The staged slug carries the pid AND a random token, so two authors — two runs, or
two web PUTs inside one web process — can never read each other's candidate, and neither can
mistake it for its own. A process killed between the write and the remove leaves a staging dir
behind; the next transaction sweeps the ones older than `STALE_AFTER_S`, a cutoff far longer
than a live transaction can hold one.

TWO CONSEQUENCES OF THE CANDIDATE NAME, named because they are real and a reader will meet them:

* a selftest that calls ITSELF (`gu <its own name>`) reaches the INSTALLED version, not the
  candidate — the staged script answers to the staged slug. All 57 utils of the live library
  were checked when this shipped and none declares itself on its `calls:` line (a self-call is
  a strange shape anyway: `--selftest` is the entry point under test). If one ever does, it is
  testing the old code and the header check is the place to catch it.
* the catalog hiding here covers `utils_lib.list_utils` (the engine's catalog, the library
  impact report, `sandbox.private_store_paths`) and the `gu` dispatcher THIS module installs.
  A library root carrying its OWN richer `gu` keeps it (`utils_lib._install_dispatcher` never
  overwrites one), so on such an instance a `gu list` made from inside another util can still
  see a `staging--…` entry for the seconds its selftest runs. Nothing calls that name, and
  `utils/<name>` — the thing F584 is about — is untouched throughout.

THE COST, named because it is real: the candidate's script path is new on every attempt, so uv
builds the PEP 723 environment instead of reusing the installed script's. That install happens
in the PREWARM phase, outside the selftest's timed window (R20/R40), so a heavy dependency tree
still cannot spend the selftest timeout on the toolchain — the price is one extra dependency
install per authoring attempt, paid by the author and by nobody calling the util.
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from collections.abc import Callable
from pathlib import Path

from . import sandbox, utils_lib, utils_run

log = logging.getLogger("rsched.utils_stage")

#: How long a staging dir may live before a later transaction reads it as crash residue and
#: removes it. A dependency prewarm (180 s) plus a selftest (120 s) plus the commit bounds how
#: long a LIVE transaction holds one, so an hour cannot reach another author's candidate.
STALE_AFTER_S = 3600


def staging_name(name: str) -> str:
    """The throwaway slug this attempt stages its candidate for `name` under.

    A slug (`utils_lib.write_util_file` and `utils_run.run_util` both refuse anything else),
    carrying the util's own name so crash residue says what it was, plus the pid and a random
    token so no two attempts can collide — including two in one process, which is what the web
    editor's PUT is.
    """
    return f"{utils_lib.STAGING_PREFIX}{name}-{os.getpid()}-{uuid.uuid4().hex[:8]}"


def sweep_stale(home: Path, *, now: float | None = None) -> list[str]:
    """Remove staging dirs older than `STALE_AFTER_S` — residue from a process killed between
    a candidate's write and its `finally`. Age-bounded, so a transaction running right now is
    never swept out from under itself.
    """
    cutoff = (now if now is not None else time.time()) - STALE_AFTER_S
    try:
        stale = [d.name for d in (home / "utils").iterdir()
                 if d.is_dir() and d.name.startswith(utils_lib.STAGING_PREFIX)
                 and d.stat().st_mtime <= cutoff]
    except OSError:
        return []
    for staged in stale:
        utils_lib.remove_util_file(home, staged)
    if stale:
        log.warning("swept %d stale util staging dir(s): %s", len(stale), ", ".join(stale))
    return stale


def write_selftested(home: Path, name: str, content: str, *, policy: sandbox.SandboxPolicy,
                     message: str, routines_home: Path | None,
                     aborted: Callable[[], bool] | None = None) -> tuple[bool, str]:
    """Selftest a util candidate out of the live path and INSTALL IT ONLY ON GREEN — the one
    transaction every util author goes through (the engine's `write_util`, the web Library
    editor's PUT). Returns the selftest's `(ok, output)`.

    The selftest gates the LIBRARY, not just the reply, and it does it BEFORE the library ever
    holds the text: on failure `utils/<name>` is untouched (a revision keeps serving its
    working script, a creation never appears), so there is no window in which a `gu <name>`
    from any routine can reach an untested candidate. See the module docstring for why the
    candidate is staged under a throwaway slug in the live library rather than in a temporary
    library home of its own.
    """
    sweep_stale(home)
    staged = staging_name(name)
    try:
        utils_lib.write_util_file(home, staged, content)
        ok, output = utils_run.selftest(home, staged, policy=policy, aborted=aborted)
    finally:
        # The one exit every failure path shares — a failing assertion, the 120 s deadline, an
        # aborted run, an exception inside the runner. The library needs no repair because it
        # was never changed.
        utils_lib.remove_util_file(home, staged)
    # The author must not be told to fix a path that does not exist: a traceback and a log line
    # name the staged dir, so they are read back as the util the author wrote.
    output = output.replace(staged, name)
    if not ok:
        return False, output
    utils_lib.write_util_file(home, name, content)
    utils_lib.git_commit(home, message, routines_home=routines_home, paths=[f"utils/{name}"])
    return True, output
