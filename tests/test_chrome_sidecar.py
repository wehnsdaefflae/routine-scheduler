"""The browser sidecar's entrypoint, RUN — not read."""
from __future__ import annotations

import contextlib
import os
import re
import signal
import subprocess
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
ENTRYPOINT = REPO / "deploy" / "chrome-entrypoint.sh"

#: the stub that stands in for a process that stays up — it must be killable and cheap
STAYS_UP = "#!/bin/bash\nexec sleep 300\n"
#: Xvfb's readiness probe; the real one queries the display, which no stub serves
PROBE_OK = "#!/bin/bash\nexit 0\n"
#: `dbus-launch --exit-with-session CMD …` — drop its own options, run what follows
DBUS = '#!/bin/bash\nwhile [ "${1#--}" != "$1" ]; do shift; done\nexec "$@"\n'


def _sidecar(tmp_path: Path, dies: str | None = None, status: int = 7) -> Path:
    """Lay out a stub PATH for the entrypoint. `dies` names the one process that exits early."""
    binaries = tmp_path / "bin"
    binaries.mkdir()
    for name in ("Xvfb", "x11vnc", "websockify", "google-chrome-stable"):
        body = STAYS_UP
        if name == dies:
            body = f"#!/bin/bash\nsleep 2\nexit {status}\n"
        (binaries / name).write_text(body, encoding="utf-8")
    (binaries / "xdpyinfo").write_text(PROBE_OK, encoding="utf-8")
    (binaries / "dbus-launch").write_text(DBUS, encoding="utf-8")
    for stub in binaries.iterdir():
        stub.chmod(0o755)
    # the auth proxy is invoked by absolute path, so the script is copied with that path rewritten
    proxy = tmp_path / "proxy.py"
    proxy.write_text("import time\ntime.sleep(300)\n", encoding="utf-8")
    script = tmp_path / "chrome-entrypoint.sh"
    script.write_text(
        ENTRYPOINT.read_text(encoding="utf-8")
        .replace("/usr/local/bin/browser-auth-proxy.py", str(proxy)),
        encoding="utf-8")
    return script


def _run(script: Path, tmp_path: Path, timeout: float) -> subprocess.CompletedProcess:
    """Run the entrypoint and return its status and log."""
    environment = dict(os.environ,
                       PATH=f"{script.parent / 'bin'}{os.pathsep}{os.environ['PATH']}",
                       BROWSER_CDP_TOKEN="test-token",
                       CHROME_PROFILE=str(tmp_path / "profile"))
    out = tmp_path / "out.log"
    err = tmp_path / "err.log"
    with out.open("w") as stdout, err.open("w") as stderr:
        child = subprocess.Popen(["bash", str(script)], env=environment,
                                 stdout=stdout, stderr=stderr, start_new_session=True)
        try:
            child.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            _end_the_group(child)
            raise
        finally:
            _end_the_group(child)
    return subprocess.CompletedProcess(child.args, child.returncode,
                                       out.read_text(encoding="utf-8"),
                                       err.read_text(encoding="utf-8"))


def _end_the_group(child: subprocess.Popen) -> None:
    """Kill the whole session AND reap it, so no stub outlives the test that started it."""
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(os.getpgid(child.pid), signal.SIGKILL)
    with contextlib.suppress(subprocess.TimeoutExpired):
        child.wait(timeout=5)


@pytest.mark.parametrize(("dies", "named"), [
    ("Xvfb", "Xvfb (the virtual display)"),
    ("websockify", "websockify (the noVNC bridge)"),
    ("google-chrome-stable", "Chrome"),
])
def test_one_process_dying_ends_the_whole_sidecar(tmp_path, dies, named):
    """Whichever of them dies, the script leaves — and SAYS which, because the log line is the
    only thing a person has when a container restarts by itself."""
    script = _sidecar(tmp_path, dies=dies, status=7)
    started = time.monotonic()
    done = _run(script, tmp_path, timeout=25)
    assert done.returncode == 7, (
        f"{dies} exited 7 and the script answered {done.returncode}; a status the container does "
        f"not carry is a restart nobody can diagnose\n{done.stderr}")
    assert time.monotonic() - started < 20, "the script waited for something other than the first exit"
    assert named in done.stderr, f"the log does not say {named} died:\n{done.stderr}"


def test_it_stays_up_while_every_process_does(tmp_path):
    """The other half of the promise: supervision must not end the container on its own. Nothing
    dies here, so the script must still be running when the timeout takes it."""
    script = _sidecar(tmp_path, dies=None)
    with pytest.raises(subprocess.TimeoutExpired):
        _run(script, tmp_path, timeout=8)


def test_it_refuses_to_start_without_the_cdp_token(tmp_path):
    """Pre-existing promise, kept here because this module can actually run it: the ports have no
    authentication of their own, so no token means no browser (EX_CONFIG)."""
    script = _sidecar(tmp_path, dies=None)
    environment = dict(os.environ,
                       PATH=f"{script.parent / 'bin'}{os.pathsep}{os.environ['PATH']}",
                       CHROME_PROFILE=str(tmp_path / "profile"))
    environment.pop("BROWSER_CDP_TOKEN", None)
    done = subprocess.run(["bash", str(script)], env=environment, timeout=20,
                          capture_output=True, text=True, check=False)
    assert done.returncode == 78, done.stderr
    assert "BROWSER_CDP_TOKEN" in done.stderr




def test_the_chrome_sidecar_dies_whole_or_not_at_all():
    """The browser container runs FOUR processes — Xvfb, x11vnc, websockify and Chrome — and
    only Chrome was supervised: it ran as PID 1's child under `exec`, so compose's `restart:`
    saw the container exit when the BROWSER died. Any of the other three dying left a container
    that is up and broken instead: Xvfb gone means Chrome paints into nothing, websockify gone
    means the noVNC page never connects, and nothing exits, so nothing restarts. That is a
    sidecar in half, and the symptom reaching a person is "the browser view is black" with a
    healthy-looking container behind it (SD2.5, docs/designs.md).
    """
    script = ENTRYPOINT.read_text(encoding="utf-8")
    body = [ln for ln in script.splitlines()
            if ln.strip() and not ln.lstrip().startswith("#")]

    # `exec <the browser>` is the single-child shape this replaces: it makes Chrome PID 1 and
    # leaves the other three unwatched for the whole life of the container.
    execed = [ln for ln in body if re.match(r"^\s*exec\b", ln)
              and "gosu" not in ln]          # the root->mark re-exec of this same script stays
    assert not execed, (
        "the entrypoint still execs a single process, so the other three are unsupervised: "
        f"{execed}")

    assert re.search(r"^\s*wait -n\b", script, re.MULTILINE), (
        "nothing waits for the first process to exit, so one dying leaves the container up "
        "and broken")

    # Each of the four must be BACKGROUNDED for `wait -n` to see it at all: a process left in
    # the foreground blocks the script before it reaches the wait, which is the same bug wearing
    # a different hat. A launch may span several lines (Chrome's does), so the test asks whether
    # the command STARTING at the process's line ends with `&`.
    for process in ("Xvfb", "x11vnc", "websockify", "google-chrome-stable"):
        lines = script.splitlines()
        starts = [i for i, ln in enumerate(lines)
                  if re.search(rf"^\s*(dbus-launch[^\n]*)?{re.escape(process)}\b", ln)]
        assert starts, f"{process} is not started by this entrypoint at all"
        backgrounded = False
        for i in starts:
            command = []
            for ln in lines[i:]:
                command.append(ln)
                if not ln.rstrip().endswith("\\"):
                    break
            backgrounded = backgrounded or command[-1].rstrip().endswith("&")
        assert backgrounded, f"{process} is not backgrounded, so `wait -n` never sees it"
