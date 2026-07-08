"""Allowlist vetting + execution for `shell` actions.

Threat model: accidents and drift, NOT a malicious model — this is the user's own agent on
the user's own machine. We split the command line on shell separators, check every
segment's leading words against the allowlist (fnmatch patterns like "gu *"), reject
command/process substitution outright, and run with cwd = routine dir and a scrubbed env.
"""

from __future__ import annotations

import fnmatch
import os
import shlex
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from ..endpoints.claude_cli import STRIP_VARS

DEFAULT_TIMEOUT_S = 120
SEPARATORS = {";", "&&", "||", "|", "&", "\n"}
FORBIDDEN_SUBSTRINGS = ("$(", "`", "<(", ">(")


def split_segments(command: str) -> list[str]:
    """Split a command line into pipeline/sequence segments using shlex with
    punctuation_chars so operators come out as their own tokens."""
    lex = shlex.shlex(command, posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    segments: list[list[str]] = [[]]
    for tok in lex:
        if tok in SEPARATORS or all(c in ";&|" for c in tok):
            if segments[-1]:
                segments.append([])
        else:
            segments[-1].append(tok)
    return [" ".join(seg) for seg in segments if seg]


def vet(command: str, allowlist: list[str]) -> list[str]:
    """Return problems (empty = allowed)."""
    problems: list[str] = []
    for bad in FORBIDDEN_SUBSTRINGS:
        if bad in command:
            problems.append(f"command contains {bad!r} (substitution is not allowed)")
    if problems:
        return problems
    try:
        segments = split_segments(command)
    except ValueError as exc:
        return [f"unparseable command line: {exc}"]
    if not segments:
        return ["empty command"]
    for seg in segments:
        if not any(fnmatch.fnmatch(seg, pat) or seg == pat.rstrip(" *")
                   for pat in allowlist):
            head = seg.split()[0] if seg.split() else seg
            problems.append(
                f"segment {seg[:80]!r} (program {head!r}) does not match the allowlist {allowlist}"
            )
    return problems


@dataclass
class ShellResult:
    exit: int
    stdout: str
    stderr: str
    duration_s: float
    timed_out: bool = False


def scrubbed_env() -> dict:
    """Child env for shell actions: metered-LLM auth vars stripped (child tools like
    `gu claude` resolve their own subscription token)."""
    env = dict(os.environ)
    for k in STRIP_VARS:
        env.pop(k, None)
    return env


def run_shell(command: str, *, cwd: Path, timeout_s: int = DEFAULT_TIMEOUT_S) -> ShellResult:
    start = time.monotonic()
    try:
        r = subprocess.run(
            ["bash", "-c", command],
            cwd=cwd,
            env=scrubbed_env(),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            start_new_session=True,  # its own process group: engine SIGTERM doesn't orphan children
        )
        return ShellResult(exit=r.returncode, stdout=r.stdout, stderr=r.stderr,
                           duration_s=time.monotonic() - start)
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        err = exc.stderr.decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        return ShellResult(exit=-1, stdout=out, stderr=err,
                           duration_s=time.monotonic() - start, timed_out=True)
