# /// script
# dependencies = ["paramiko>=3.4"]
# ///
"""remote — act on a bound remote machine over SSH (reserved: needs the remote-machines permission).

usage:
  gu remote list [--json]                                    # the machines this routine can reach
  gu remote exec MACHINE --command CMD [--cwd DIR] [--timeout S] [--json]
                                                             # run CMD, WAIT, return stdout/stderr/exit
  gu remote submit MACHINE --command CMD [--cwd DIR] [--notify-webhook URL]
                   [--deadline-hours H] [--est-minutes N] [--json]
                                                             # start a DETACHED job -> a job id
  gu remote status MACHINE --job ID [--json]                 # running | exit=<code> | nojob
  gu remote logs MACHINE --job ID [--tail BYTES] [--tail-lines N] [--json]
                                                             # the job's stdout + stderr so far
  gu remote cancel MACHINE --job ID [--json]                 # terminate the job's process group
  gu remote queue MACHINE [--cancel JOBID] [--json]          # an exclusive machine's job queue
  gu remote push MACHINE --src LOCAL --dest REMOTE [--json]  # upload a file or a tree (SFTP)
  gu remote pull MACHINE --src REMOTE --dest LOCAL [--json]  # download a file (SFTP)
  gu remote scan-host HOST [--port N] [--json]               # read a host key line, for pinning
  gu remote test MACHINE [--json]                            # connect + run `true`
  gu remote --selftest
calls: (none)
tags: ssh, remote, machines, gpu, execute
secrets: RSCHED_MACHINES, RSCHED_MACHINE_KEYS, RSCHED_ROUTINE?
net: outbound
fs: roots

Runs commands and moves files on the SSH hosts the routine is BOUND to (Settings → Machines,
then bind on the routine page). The engine injects the bound machines' connection details
(RSCHED_MACHINES) and private keys (RSCHED_MACHINE_KEYS) — you never handle credentials; a
machine you are not bound to is invisible. Host keys are PINNED: a mismatch (or an unscanned
machine) refuses to connect.

Every command this util ships starts with the remote user's own `~/.local/bin` on PATH, where
astral's installer puts `uv` (and with it `gu`) — a non-interactive SSH shell reads no profile,
so without it a tool present in the operator's login shell is simply not found. `--tail` on
`logs` counts BYTES (default 8000); `--tail-lines` counts LINES instead.

BACKGROUNDING A COMMAND IS NOT DETACHING IT. `cd DIR && CMD &` backgrounds the WHOLE and-list:
the subshell that survives still holds this SSH session's stdout and stderr, so the session
cannot finish even though CMD itself is gone. Redirect INSIDE the command and detach from the
session — `nohup CMD > log 2>&1 < /dev/null &` — or, for anything longer than a few seconds,
use `submit`, which is detached by construction and keeps a durable log. When a process is
still holding the pipes as the shell exits, the result says so on stderr instead of hanging.

Long jobs (CPU or GPU): `submit` then poll `status`, or pass `--notify-webhook <url>` and let
the job POST the routine's own trigger URL on completion (no polling).

`push --src DIR` copies the tree recursively (parents created before their children). Files go
one at a time in a deterministic order and the FIRST failure STOPS the transfer: the result is
`ok: false` with `uploaded` (what landed), `failed` (the file and its error) and `remaining`
(never attempted). Nothing on the remote is ever deleted, so re-running the same push is safe
and simply re-sends — resume by repeating the command.

`exec --timeout` is an SSH I/O deadline. It DEFAULTS from the action timeout_s this call was
given (less a small margin), so the util reports the timeout — with the output captured up to
that point, `timed_out: true` and exit -1 — instead of being killed by the engine with nothing
to show. A timeout does not guarantee the remote command stopped, and exec has no durable job
log. Inspect existing work before retrying; use submit for long jobs.

NO SECRET FORWARDING (R1569). There is deliberately NO flag that carries an environment
variable or a Secrets-store value into a remote command: whatever `exec`/`submit` runs sees
only the environment the REMOTE box gives it. A routine holding, say, PANGRAM_API_KEY here
cannot hand it to a job on a bound machine. This is a known gap, not an oversight to work
around by echoing the value into the command string — that writes the secret into the remote
shell history, the job script on disk, and this instance's own transcript. Until it is
designed, put the credential on the box (its own credential convention, or
`gu remote-service-credentials MACHINE --service …`) and have the remote job read it there.
Closing the gap needs an operator decision, because forwarding means the secret leaves this
instance's sandbox: a generic `--env FOO=$FOO` would let any caller forward anything the
routine holds to any bound machine, which is wider than the `secrets:` header model intends;
a per-call declaration (the util names which vars it will forward, the caller chooses only
among those) keeps that property.

Exclusive is currently WHOLE-MACHINE serialization: CPU-only submitted jobs share the same
queue and lock as GPU jobs. Preserve the operator's exclusive setting; do not bypass it
with exec or detached shells. Resource-class scheduling requires a separately authorized
design, not an inference that idle CPU cores permit overlap.

ONE JOB AT A TIME — an EXCLUSIVE machine. A machine the operator marks `exclusive` is a single
resource: two training jobs on one card do not run half as fast, they run out of VRAM. There,
`submit` does NOT launch the payload — it takes a QUEUE TICKET and returns at once with
`queued: true`, your `position` and how many jobs are `ahead`. The box then starts the waiting
jobs in FAIR-SHARE order: round-robin across ROUTINES by each routine's oldest ticket, FIFO
within one routine, so a routine that submitted three jobs never starves one that submitted one.
Every ticket carries a mandatory deadline (`--deadline-hours`, default 6): past it the job is
killed and its ticket dropped, which is the only self-healing a detached job can have — it
leaves no live process to heartbeat. NOTHING BLOCKS: the submitting run gets its job id
immediately and should spend itself on work that does not need this machine. `queue MACHINE`
reads the box's live tickets, `queue MACHINE --cancel ID` drops one (terminating its job if it
had started). Everything else — `status`, `logs`, `cancel`, `push`, `pull` — is unchanged, and a
machine that is not exclusive still launches on submit exactly as it always did.

The queue lives ON THE BOX, under the machine's job root, so it survives a scheduler restart and
a human can read it: `.rsched-queue/tickets/` is what waits or runs, `.rsched-queue/round/` the
turns already spent in the round being served, `.rsched-queue/lock` the flock a running job
holds. Jobs themselves stay in `.rsched-jobs/<id>/` exactly as before; a queued one just adds a
`queue.log` there narrating its wait.

The queue is COOPERATIVE, never enforced, for the same reason the remote host itself is not
sandboxed. It coordinates only the jobs submitted through this util: a person logged into the
box, a `shell` action, or anything else starting work without taking a ticket walks straight
past it and will collide with whatever is running.

--selftest runs offline."""

import argparse
import base64
import io
import json
import os
import re
import shlex
import sys
import time

_JOBID_RE = re.compile(r"[A-Za-z0-9_.-]+")

CAP = 64_000               # per-stream output cap (head+tail), matching the shell action
JOBS_DIR = ".rsched-jobs"  # per-machine job root, under the machine's workdir (else $HOME)
QUEUE_DIR = ".rsched-queue"  # tickets + the flock file, beside JOBS_DIR (exclusive machines)
DEFAULT_DEADLINE_H = 6     # a detached job cannot heartbeat, so every ticket gets a wall clock
POLL_S = 5                 # how often a waiting job re-checks whether its turn has come
# The keys a ticket REPORTS. The scheduler's read model (rsched/machine_queue.py) mirrors
# exactly these, so the box's own bookkeeping (the waiting process group, the start stamp)
# stays on the box instead of leaking into a contract.
TICKET_KEYS = ("holder", "job", "submitted", "deadline_s", "est_min", "state")
# A holder reaches the ticket FILENAME, and a routine addressed by directory path is no slug.
_HOLDER_RE = re.compile(r"[^A-Za-z0-9_.-]")
# Every shipped command starts by putting the remote user's OWN ~/.local/bin on PATH: astral's
# installer puts `uv` (and with it `gu`) exactly there, and a non-interactive SSH session sources
# no profile — so a command that works in the operator's login shell failed here with
# "uv: command not found" (R1730). The line is a harmless no-op where the dir does not exist.
PATH_PREFIX = 'export PATH="$HOME/.local/bin:$PATH"; '
DEFAULT_EXEC_TIMEOUT_S = 120   # when nothing exported the caller's own deadline
EXEC_TIMEOUT_MARGIN_S = 15     # report a timeout before the engine kills this process group
_CHUNK = 65_536
_POLL_S = 0.1                  # channel poll interval while a command runs
_LINGER_S = 2.0                # grace for output already in flight once the shell has exited


class RemoteError(Exception):
    """A clean, user-facing failure (bad binding, unreachable host, key mismatch)."""


class ExecTimeout(RemoteError):
    """The SSH I/O deadline passed with the remote shell still running. Carries what the
    command had already written — the half a read-to-EOF threw away."""

    def __init__(self, message: str, stdout: str = "", stderr: str = ""):
        super().__init__(message)
        self.stdout, self.stderr = stdout, stderr


def default_exec_timeout() -> int:
    """The `exec --timeout` default: the ENGINE's own deadline for this util call
    (RSCHED_UTIL_TIMEOUT_S, exported by both util runners) minus a margin, so this util
    reports a timeout — with the output captured so far — instead of being killed mid-read
    with nothing to show. Two clocks that raced is exactly what R1813 cost: the action's
    timeout_s: 120 and this util's own 120 expired together and the engine won.
    """
    try:
        budget = int(os.environ.get("RSCHED_UTIL_TIMEOUT_S", ""))
    except ValueError:
        return DEFAULT_EXEC_TIMEOUT_S
    return max(budget - EXEC_TIMEOUT_MARGIN_S, 10) if budget > 0 else DEFAULT_EXEC_TIMEOUT_S


# ---------------------------------------------------------------------------- pure helpers ---
def load_machines() -> tuple[dict[str, dict], dict[str, str]]:
    """The engine-injected binding: RSCHED_MACHINES (metadata list) + RSCHED_MACHINE_KEYS
    ({name: PEM}) → ({name: meta}, {name: pem}). Missing/blank env = no bound machines.
    """
    raw = os.environ.get("RSCHED_MACHINES") or "[]"
    keys_raw = os.environ.get("RSCHED_MACHINE_KEYS") or "{}"
    try:
        meta = {m["name"]: m for m in json.loads(raw) if m.get("name")}
    except (ValueError, TypeError, KeyError):
        meta = {}
    try:
        keys = {k: v for k, v in json.loads(keys_raw).items() if isinstance(v, str)}
    except (ValueError, TypeError, AttributeError):
        keys = {}
    return meta, keys


def _pick(machines: dict[str, dict], name: str) -> dict:
    m = machines.get(name)
    if m is None:
        avail = ", ".join(sorted(machines)) or "(none — bind a machine on the routine page)"
        raise RemoteError(f"machine {name!r} is not bound to this routine (available: {avail})")
    return m


def _capped(text: str, cap: int = CAP) -> tuple[str, bool]:
    """Head 70% + tail 30% with an elision marker when over cap (a traceback's tail matters)."""
    if len(text) <= cap:
        return text, False
    head, tail = int(cap * 0.7), cap - int(cap * 0.7)
    return text[:head] + f"\n...[{len(text) - cap} chars omitted]...\n" + text[-tail:], True


def hostkey_lines(host: str, port: int, host_key_text: str) -> list[str]:
    """Normalize a catalog `host_key` (ssh-keyscan "host type base64", a .pub file's
    "type base64 comment", or a bare "type base64") into known_hosts lines for THIS
    host:port. The key TYPE token anchors the parse — taking the last two tokens would
    pin "base64 comment" for a .pub paste and every connection would refuse. Pure.
    """
    entry_host = host if int(port) == 22 else f"[{host}]:{port}"
    out = []
    for line in host_key_text.splitlines():
        parts = line.split()
        idx = next((i for i, tok in enumerate(parts)
                    if tok.startswith(("ssh-", "ecdsa-", "sk-"))), None)
        if idx is None or len(parts) <= idx + 1:
            continue                       # blank / malformed → skip
        out.append(f"{entry_host} {parts[idx]} {parts[idx + 1]}")
    return out


def _job_root(m: dict) -> str:
    """The remote dir jobs live under. The machine's workdir if set (a literal path), else the
    login shell's $HOME (expanded remotely). Returned for embedding inside a double-quoted
    shell string, so $HOME expands and a literal path is used verbatim.
    """
    wd = (m.get("workdir") or "").strip()
    return wd if wd else "$HOME"


def build_job_script(command: str, jobid: str, cwd: str, webhook: str) -> str:
    """The detached job body, run inside the job dir (setsid cd's there first). Redirections are
    opened in the job dir; an optional --cwd changes only the command's dir. base64-transported,
    so its content needs no outer quoting. Pure — the selftest asserts its shape.
    """
    # A SUBSHELL, not a { } group: a user command ending in `exit N` must terminate only the
    # job body, so `code=$?` and the exit-file write below still run (a group's exit would kill
    # job.sh outright, losing the exit code). Redirections are opened in the job dir.
    inner = f"cd {shlex.quote(cwd)} || exit 1\n{command}\n" if cwd else f"{command}\n"
    # Same PATH line as `exec` (R1730): a job.sh runs under the same profile-less shell.
    lines = [PATH_PREFIX.strip(), "(", inner, ") > stdout 2> stderr < /dev/null",
             "code=$?", "echo $code > exit"]
    if webhook:
        # The job POSTs the routine's trigger URL on completion (job id + exit code) so a
        # multi-hour run needs no polling. URL is base64-transported to avoid any quoting.
        b64url = base64.b64encode(webhook.encode()).decode()
        lines.append(
            f'url="$(echo {b64url} | base64 -d)"; '
            f'curl -fsS -m 20 -X POST "$url" -H "Content-Type: application/json" '
            f'-d "{{\\"job\\":\\"{jobid}\\",\\"exit\\":$code}}" >/dev/null 2>&1 || true')
    return "\n".join(lines) + "\n"


def build_launcher(root: str, jobid: str, job_script_b64: str) -> str:
    """The submit command: make the job dir, drop job.sh, launch it detached in a new session
    (setsid → its own process group, killable by `cancel`), print the job id. Pure.
    """
    jobdir = f'"{root}/{JOBS_DIR}/{jobid}"'
    return (
        f"set -e; JOBDIR={jobdir}; mkdir -p \"$JOBDIR\"; "
        f"printf %s '{job_script_b64}' | base64 -d > \"$JOBDIR/job.sh\"; "
        "setsid bash -c 'cd \"$1\" || exit 1; echo $$ > pgid; bash job.sh' _ \"$JOBDIR\" "
        ">/dev/null 2>&1 & "
        f"echo {jobid}")


def _job_cmd(m: dict, jobid: str, tail: str) -> str:
    """A small remote snippet operating on job <jobid>'s dir (status/logs/cancel share this).
    The job id is validated to a safe charset so it can be embedded in the shell string.
    """
    if not _JOBID_RE.fullmatch(jobid):
        raise RemoteError(f"invalid job id {jobid!r} (expected [A-Za-z0-9_.-])")
    return f'JOBDIR="{_job_root(m)}/{JOBS_DIR}/{jobid}"; {tail}'



# ------------------------------------------------------------------- the fair-share queue ----
def fair_share_order(tickets: list) -> list:
    """Round-robin across HOLDERS by each holder's oldest ticket, FIFO within one holder.

    THE definition of "everyone gets their turn", and the single copy of it: `build_queue_helper`
    ships this exact function to the box by source, so the position a run is told here and the
    order the machine actually starts jobs in cannot drift apart. It reproduces the scheduler's
    own `machine_queue.fair_share_order` — three tickets from one routine and one from another
    interleave A, B, A, A, so the routine that asked once does not wait behind the routine that
    asked three times.
    """
    from itertools import zip_longest

    by_holder = {}
    for t in sorted(tickets, key=lambda t: str(t.get("submitted") or "")):
        by_holder.setdefault(str(t.get("holder") or "?"), []).append(t)
    # holders enter the rotation in the order their oldest ticket arrived, so a newcomer joins
    # the end of it rather than jumping ahead of someone already waiting
    holders = sorted(by_holder, key=lambda h: str(by_holder[h][0].get("submitted") or ""))
    # interleaving each holder's FIFO queue IS the round-robin: one from every holder that still
    # has one, in holder order, until all are drained
    return [t for row in zip_longest(*(by_holder[h] for h in holders)) for t in row
            if t is not None]


def _ticket_view(ticket: dict) -> dict:
    """The six keys a ticket REPORTS (TICKET_KEYS) — never the box's private bookkeeping."""
    return {k: ticket.get(k) for k in TICKET_KEYS}


def _b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


def ticket_name(holder: str, jobid: str, submitted: str) -> str:
    """`<submitted-iso>-<holder>-<jobid>.json`. The NAME is for a human reading the directory;
    every reader parses the JSON inside, so sanitizing the holder here costs nothing. Pure.
    """
    return f"{submitted}-{_HOLDER_RE.sub('_', holder) or 'unknown'}-{jobid}.json"


# The on-box janitor + orderer, assembled around the LOCAL `fair_share_order` (shipped by
# SOURCE), so this util and the machine can never disagree about whose turn it is.
_QUEUE_HELPER_HEAD = '''#!/usr/bin/env python3
"""The on-box half of the routine scheduler's fair-share job queue, written here by the
scheduler's `remote` util. Safe to read, and to run by hand:

  python3 queue.py list           every live ticket, JSON, in the order the box will start them
  python3 queue.py head JOB PGID  record PGID on JOB's ticket; print yes | no | gone
  python3 queue.py claim JOB      mark JOB running (stamps when it started)
  python3 queue.py drop JOB       remove JOB's ticket
  python3 queue.py cancel JOB     kill JOB's process group and remove its ticket

Layout, all of it beside this file: `tickets/` is what is waiting or running, `round/` is the
turns already spent in the round being served (see ROUND below), `lock` is the flock file a
running job holds.

Every command PRUNES first: a ticket whose recorded process group has died is taken out, and one
past its deadline has whatever is left of its job killed before it goes. A detached job leaves no
live process to heartbeat, so that wall clock is the queue's only self-healing.

COOPERATIVE, never enforced. This coordinates the jobs submitted through the scheduler and
nothing else - a person working on this box collides with them as freely as ever.
"""
import json
import os
import signal
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
TICKETS = os.path.join(HERE, "tickets")
# The turns already SPENT in the round now being served. Without them, removing the ticket that
# just ran hands the next turn straight back to the same holder - the round-robin collapses into
# FIFO and the routine that submitted three jobs runs all three before the routine that submitted
# one, which is the exact failure this queue exists to end.
ROUND = os.path.join(HERE, "round")
MAX_ROUND = 200


def _now():
    return datetime.now(timezone.utc)


def _load(where):
    """[(path, ticket)] for every parseable ticket file in `where`, oldest first (the name
    starts with the submit stamp). A half-written one is skipped, never deleted - both writers
    rename into place, so an unreadable file is a passing glimpse."""
    out = []
    try:
        names = sorted(os.listdir(where))
    except OSError:
        return out
    for name in names:
        if not name.endswith(".json"):
            continue
        path = os.path.join(where, name)
        try:
            with open(path, encoding="utf-8") as fh:
                ticket = json.load(fh)
        except (OSError, ValueError):
            continue
        if isinstance(ticket, dict) and ticket.get("job"):
            out.append((path, ticket))
    return out


def _alive(pgid):
    """Does that process GROUP still exist? EPERM means it does and is not ours to signal."""
    try:
        os.killpg(int(pgid), 0)
    except PermissionError:
        return True
    except (OSError, TypeError, ValueError):
        return False
    return True


def _expired(ticket):
    """Has the ticket outlived its allowance, measured from wherever its CURRENT state began -
    the start for a running job (the same clock its own `timeout` enforces), the submit for one
    still waiting, so a job nobody ever started does not hold a place forever? A stamp we cannot
    read counts as unexpired: dropping a ticket we failed to parse would free the card under a
    job that is still on it.
    """
    try:
        began = datetime.fromisoformat(str(ticket.get("started") or ticket.get("submitted")))
        allowance = float(ticket.get("deadline_s") or 0)
    except (TypeError, ValueError):
        return False
    if began.tzinfo is None:
        began = began.replace(tzinfo=timezone.utc)
    return allowance > 0 and (_now() - began).total_seconds() > allowance


def _kill(ticket):
    pgid = ticket.get("pgid")
    if pgid is not None and _alive(pgid):
        try:
            os.killpg(int(pgid), signal.SIGTERM)
        except (OSError, TypeError, ValueError):
            pass


def _unlink(path):
    try:
        os.unlink(path)
    except OSError:
        pass


def _write(path, ticket):
    tmp = path + ".tmp"          # not a .json name, so a concurrent reader never sees it
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(ticket, fh)
    os.replace(tmp, path)


def _find(live, job):
    for path, ticket in live:
        if str(ticket.get("job")) == job:
            return path, ticket
    return None, None


def _retire(path, ticket):
    """Take a ticket out of the queue. One that actually RAN leaves a marker in `round/`, because
    its holder has now had the card and must not be handed it again while someone else is still
    waiting. One that never started is simply deleted: no turn was taken, so its holder keeps the
    place it was in.
    """
    if not (ticket.get("started") or ticket.get("state") == "running"):
        _unlink(path)
        return
    try:
        os.makedirs(ROUND, exist_ok=True)
        ticket["state"] = "done"
        _write(path, ticket)
        os.replace(path, os.path.join(ROUND, os.path.basename(path)))
    except OSError:
        _unlink(path)


def prune():
    """Drop every ticket that no longer stands for live, in-time work. Returns
    (still waiting or running, turns already spent in this round).
    """
    live = []
    for path, ticket in _load(TICKETS):
        gone = ticket.get("pgid") is not None and not _alive(ticket["pgid"])
        if _expired(ticket):
            _kill(ticket)     # normally a no-op: the job's own `timeout` got there first
            gone = True
        if gone:
            _retire(path, ticket)
            continue
        live.append((path, ticket))
    spent = _load(ROUND)
    if not live:
        for path, _ticket in spent:   # nobody is waiting: the round is over, the next starts even
            _unlink(path)
        spent = []
    elif len(spent) > MAX_ROUND:      # a round nobody ever finishes must not grow without bound
        for path, _ticket in spent[:len(spent) - MAX_ROUND]:
            _unlink(path)
        spent = spent[len(spent) - MAX_ROUND:]
    return live, spent


'''

_QUEUE_HELPER_TAIL = '''

def main(argv):
    op = argv[0] if argv else "list"
    live, spent = prune()
    # The rotation is derived over this round's SPENT turns AND what is still waiting, then
    # filtered down to what can actually be started. Deriving it over the waiting tickets alone
    # would re-run the same holder every time, because the ticket that just used its turn is the
    # very one that has been removed.
    waiting = {str(t.get("job")) for _path, t in live}
    order = [t for t in fair_share_order([t for _path, t in spent] + [t for _path, t in live])
             if str(t.get("job")) in waiting]
    if op == "list":
        print(json.dumps(order))
        return 0
    if len(argv) < 2:
        print("usage: queue.py {list | head JOB PGID | claim JOB | drop JOB | cancel JOB}",
              file=sys.stderr)
        return 2
    job = argv[1]
    path, ticket = _find(live, job)
    if op == "head":
        if ticket is None:
            print("gone")       # pruned while we waited - the job must not start
            return 0
        pgid = argv[2] if len(argv) > 2 else ""
        if pgid.isdigit() and str(ticket.get("pgid") or "") != pgid:
            ticket["pgid"] = int(pgid)   # from here a killed waiter's ticket cleans itself up
            _write(path, ticket)
        print("yes" if order and str(order[0].get("job")) == job else "no")
        return 0
    if ticket is None:
        print("no ticket for " + job, file=sys.stderr)
        return 1
    if op == "claim":
        ticket["state"] = "running"
        ticket["started"] = _now().isoformat()
        _write(path, ticket)
        return 0
    if op == "cancel":
        _kill(ticket)
    elif op != "drop":
        print("unknown command " + repr(op), file=sys.stderr)
        return 2
    _retire(path, ticket)
    print("cancelled" if op == "cancel" else "dropped")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
'''


def build_queue_helper() -> str:
    """The on-box helper script, with THIS module's `fair_share_order` spliced in verbatim."""
    import inspect

    return _QUEUE_HELPER_HEAD + inspect.getsource(fair_share_order) + _QUEUE_HELPER_TAIL


# The queued job's body. Two conditions gate its start and they do different jobs: being at the
# HEAD of the fair-share order is the FAIRNESS (whose turn it is), the non-blocking flock is the
# EXCLUSION (only one job on the card). Polling rather than blocking on the lock is what keeps
# the order the queue's and not the kernel's arrival order.
_WRAPPER = r'''exec >> queue.log 2>&1
JOB=@JOB@
PGID="$(cat pgid 2>/dev/null)"
cleanup() {
  trap - EXIT INT TERM
  python3 @Q@ drop "$JOB" || true
  # every descendant of the payload shares this session's process group, so one signal collects
  # the strays a killed `timeout` would otherwise orphan onto the card. It reaches this shell
  # too - by then the ticket is gone and the exit file written, so nothing is lost.
  [ -n "$PGID" ] && kill -TERM -"$PGID" 2>/dev/null
  true
}
trap cleanup EXIT INT TERM
exec 9> @LOCK@
while : ; do
  TURN="$(python3 @Q@ head "$JOB" "$PGID" 2>/dev/null)"
  if [ "$TURN" = gone ]; then
    echo "queue: this ticket was dropped before the job started (its deadline passed, or it was cancelled) - the payload never ran" >> stderr
    echo 75 > exit
    exit 75
  fi
  [ "$TURN" = yes ] && flock -n 9 && break
  sleep @POLL@
done
python3 @Q@ claim "$JOB" || true
echo "queue: started $(date -Is)"
timeout -k 30 @DEADLINE@ bash job.sh 9>&-
code=$?
[ -f exit ] || echo $code > exit
echo "queue: finished $(date -Is) code=$code"
'''


def build_wrapper(jobid: str, root: str, deadline_s: int, poll_s: int = POLL_S) -> str:
    """The wrapper that waits for the job's turn, holds the lock for exactly as long as the
    payload runs, and leaves nothing behind — including when it is killed, which is what the
    trap is for. It runs IN the job dir, so `job.sh` and the stdout/stderr/exit files every
    other verb reads are untouched; its own waiting is narrated to `queue.log` beside them.
    Pure — the selftest asserts its shape.
    """
    return (_WRAPPER.replace("@JOB@", jobid)
            .replace("@Q@", f'"{root}/{QUEUE_DIR}/queue.py"')
            .replace("@LOCK@", f'"{root}/{QUEUE_DIR}/lock"')
            .replace("@POLL@", str(int(poll_s)))
            .replace("@DEADLINE@", str(int(deadline_s))))


# Refused BEFORE anything is written: a box without these cannot run the protocol, and a job
# that silently never starts is exactly the failure the queue exists to end.
_NEEDS_PY = ('command -v python3 >/dev/null || { echo "this machine needs python3 for the job '
             'queue" >&2; exit 127; }; ')
_NEEDS_FLOCK = ('command -v flock >/dev/null || { echo "this machine needs flock (util-linux) '
                'for the job queue" >&2; exit 127; }; ')


def build_queue_command(root: str, helper_b64: str, tail: str) -> str:
    """Bootstrap the queue dir + helper, then run ONE helper command. The bootstrap rides every
    call on purpose: a machine never submitted to has neither, and reading its queue must answer
    "empty" rather than fail — and re-dropping the helper is how a box picks up a newer one. Pure.
    """
    return ("set -e; " + _NEEDS_PY
            + f'QDIR="{root}/{QUEUE_DIR}"; mkdir -p "$QDIR/tickets"; '
            + f"printf %s '{helper_b64}' | base64 -d > \"$QDIR/queue.py\"; "
            + f'python3 "$QDIR/queue.py" {tail}')


def build_queued_launcher(root: str, jobid: str, ticket_file: str, ticket_b64: str,
                          helper_b64: str, job_b64: str, wrapper_b64: str) -> str:
    """`submit` on an EXCLUSIVE machine: refuse early if the box lacks what the queue needs, drop
    the ticket + helper + both scripts, READ THE QUEUE BACK — before launching, so the position
    reported is this ticket's real one and no wrapper of ours can have moved it — then launch the
    waiting wrapper detached. stdout is the queue JSON and nothing else. Pure.
    """
    jobdir = f'"{root}/{JOBS_DIR}/{jobid}"'
    return (
        "set -e; " + _NEEDS_PY + _NEEDS_FLOCK
        + f'QDIR="{root}/{QUEUE_DIR}"; JOBDIR={jobdir}; mkdir -p "$QDIR/tickets" "$JOBDIR"; '
        + f"printf %s '{helper_b64}' | base64 -d > \"$QDIR/queue.py\"; "
        + f"printf %s '{job_b64}' | base64 -d > \"$JOBDIR/job.sh\"; "
        + f"printf %s '{wrapper_b64}' | base64 -d > \"$JOBDIR/wrapper.sh\"; "
        # the ticket lands by RENAME: a reader mid-prune must never see half of it
        + f'T="$QDIR/tickets/{ticket_file}"; '
        + f"printf %s '{ticket_b64}' | base64 -d > \"$T.tmp\"; mv \"$T.tmp\" \"$T\"; "
        + 'Q="$(python3 "$QDIR/queue.py" list)"; '
        + "setsid bash -c 'cd \"$1\" || exit 1; echo $$ > pgid; bash wrapper.sh' _ \"$JOBDIR\" "
        ">/dev/null 2>&1 & "
        + 'printf %s "$Q"')


# --------------------------------------------------------------------------- ssh (network) ---
def _load_key(pem: str):
    import paramiko

    buf = pem if pem.endswith("\n") else pem + "\n"
    last: Exception | None = None
    # getattr, not attribute access: paramiko dropped DSSKey in 3.x — skip whatever is absent.
    classes = [getattr(paramiko, n) for n in ("Ed25519Key", "ECDSAKey", "RSAKey", "DSSKey")
               if hasattr(paramiko, n)]
    for cls in classes:
        try:
            return cls.from_private_key(io.StringIO(buf))
        except paramiko.SSHException as exc:      # wrong type / encrypted → try the next
            last = exc
    raise RemoteError(f"could not load the machine's private key ({last}); is it "
                      "unencrypted and a supported type (ed25519/ecdsa/rsa)?")


def connect(m: dict, keys: dict[str, str], *, timeout: int = 20):
    """An authenticated, host-key-PINNED SSHClient for machine `m`. Only the catalog's pinned
    host key is trusted (RejectPolicy for anything else — no TOFU in a headless run); the agent
    and on-disk keys are disabled so ONLY the injected key authenticates.
    """
    import paramiko
    from paramiko.hostkeys import HostKeyEntry

    name, host = m["name"], m["host"]
    port = int(m.get("port") or 22)
    if not (m.get("host_key") or "").strip():
        raise RemoteError(f"machine {name!r} has no pinned host key — scan it in "
                          "Settings → Machines before a run can connect")
    pem = keys.get(name)
    if not pem:
        raise RemoteError(f"machine {name!r} has no private key available — set its key_var "
                          "secret in Settings → Secrets")
    client = paramiko.SSHClient()
    store = client.get_host_keys()
    for line in hostkey_lines(host, port, m["host_key"]):
        entry = HostKeyEntry.from_line(line)
        if entry:
            for hn in entry.hostnames:
                store.add(hn, entry.key.get_name(), entry.key)
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    try:
        client.connect(hostname=host, port=port, username=m["user"], pkey=_load_key(pem),
                       timeout=timeout, banner_timeout=timeout, auth_timeout=timeout,
                       look_for_keys=False, allow_agent=False)
    except paramiko.BadHostKeyException as exc:
        raise RemoteError(f"host key MISMATCH for {name!r} — the server's key differs from the "
                          f"pinned one; if the host legitimately changed, re-scan it in "
                          f"Settings → Machines ({exc})") from exc
    except paramiko.AuthenticationException as exc:
        raise RemoteError(f"authentication failed for {m['user']}@{host} — is the machine's "
                          f"public key in its authorized_keys? ({exc})") from exc
    except (OSError, paramiko.SSHException) as exc:
        raise RemoteError(f"could not connect to {name!r} ({host}:{port}): {exc}") from exc
    return client


HELD_CHANNEL_NOTE = (
    "[remote] the command exited but left a process holding this SSH session's stdout/stderr, "
    "so anything written after this point is lost. Background work must redirect INSIDE the "
    "command and detach from the session — `nohup CMD > log 2>&1 < /dev/null &` — and note that "
    "`cd DIR && CMD &` backgrounds the WHOLE and-list, i.e. a subshell that keeps these pipes "
    "open. For anything longer than a few seconds use `remote submit`, which is detached by "
    "construction and keeps a durable log.")


def _drain(chan, out: bytearray, err: bytearray) -> None:
    """Move whatever has arrived off the channel into the buffers. Never blocks."""
    while chan.recv_ready():
        chunk = chan.recv(_CHUNK)
        if not chunk:
            break
        out += chunk
    while chan.recv_stderr_ready():
        chunk = chan.recv_stderr(_CHUNK)
        if not chunk:
            break
        err += chunk


def _run(client, command: str, timeout: int, cwd: str = "") -> tuple[int, str, str]:
    """Run ONE command on an open connection → (exit, stdout, stderr).

    Two live failures shape this (R1813/R1734, funscript-trainer 2026-09-21/22):

    * `--cwd` ships `cd DIR || exit 1;`, never `cd DIR && CMD`. With `&&` the shipped line is
      one AND-LIST, so a caller ending it in `&` backgrounds the WHOLE list — a subshell that
      inherits this session's stdout/stderr and holds them open long after the job it launched
      has detached.
    * the read ends when the remote SHELL exits, not when the pipes reach EOF. Reading to EOF
      waits for the last holder of those fds, which a backgrounded subshell never releases: the
      engine then killed this util at its own deadline and the caller got NOTHING, though the
      shell had exited 0 and printed the job's PID. Polling the exit status returns that output,
      with a note naming the holder so the caller can fix its launch form.
    """
    if cwd:
        command = f"cd {shlex.quote(cwd)} || exit 1; {command}"
    _stdin, stdout, _stderr = client.exec_command(PATH_PREFIX + command, timeout=timeout)
    chan = stdout.channel
    out, err = bytearray(), bytearray()
    deadline = time.monotonic() + timeout
    while not chan.exit_status_ready():
        _drain(chan, out, err)
        if time.monotonic() >= deadline:
            raise ExecTimeout(f"SSH I/O timeout after {timeout}s",
                              out.decode("utf-8", "replace"), err.decode("utf-8", "replace"))
        time.sleep(_POLL_S)
    # The shell has exited. Collect what is still in flight, then STOP — waiting for EOF is
    # the hang itself, not a guarantee of completeness.
    linger = time.monotonic() + _LINGER_S
    while not chan.eof_received and time.monotonic() < linger:
        _drain(chan, out, err)
        time.sleep(_POLL_S)
    _drain(chan, out, err)
    code = chan.recv_exit_status()
    held = not chan.eof_received
    chan.close()
    text_err = err.decode("utf-8", "replace")
    if held:
        text_err = (text_err + "\n" + HELD_CHANNEL_NOTE) if text_err else HELD_CHANNEL_NOTE
    return code, out.decode("utf-8", "replace"), text_err


# ------------------------------------------------------------------------------ commands -----
def cmd_list(machines: dict[str, dict]) -> dict:
    return {"command": "list", "machines": [
        {"name": n, "host": m.get("host"), "user": m.get("user"), "port": m.get("port"),
         "description": m.get("description", ""), "tags": m.get("tags", []),
         "ready": bool(m.get("has_key") and m.get("has_host_key"))}
        for n, m in sorted(machines.items())]}


def cmd_exec(m: dict, keys: dict, command: str, timeout: int, cwd: str) -> tuple[dict, int]:
    """Run and wait. A timeout is REPORTED, not raised: the payload carries the exit -1, the
    output captured up to the deadline and the recovery route, because a timed-out exec whose
    diagnostics were discarded is the one failure that teaches the caller nothing (R1813)."""
    client = connect(m, keys)
    timed_out = None
    try:
        code, out, err = _run(client, command, timeout, cwd)
    except ExecTimeout as exc:
        timed_out, code, out, err = exc, -1, exc.stdout, exc.stderr
    finally:
        client.close()
    out_c, t1 = _capped(out)
    err_c, t2 = _capped(err)
    payload = {"command": "exec", "machine": m["name"], "exit": code, "stdout": out_c,
               "stderr": err_c, "truncated": t1 or t2}
    if timed_out is None:
        return payload, code
    payload["timed_out"] = True
    payload["error"] = (
        f"remote exec on {m['name']!r} hit its SSH I/O timeout (--timeout {timeout}s), which "
        "defaults from the action timeout_s this util was given. The remote command may still "
        "be running; anything it printed after the deadline is unavailable. Check its state "
        "before retrying to avoid duplicate work. For long jobs use `remote submit` then "
        "`status`/`logs`; submit respects the exclusive machine queue.")
    return payload, 1


def cmd_submit(m: dict, keys: dict, command: str, cwd: str, webhook: str, *,
               deadline_h: float = DEFAULT_DEADLINE_H, est_min: int = 0) -> dict:
    """Start a DETACHED job. On an EXCLUSIVE machine the payload is not launched: the job takes a
    queue ticket and the box starts it when its turn comes (`_submit_queued`). Everywhere else
    this is unchanged — launch on submit.
    """
    import uuid

    jobid = uuid.uuid4().hex[:16]
    script_b64 = _b64(build_job_script(command, jobid, cwd, webhook))
    if m.get("exclusive"):
        return _submit_queued(m, keys, jobid, script_b64, webhook, deadline_h, est_min)
    launcher = build_launcher(_job_root(m), jobid, script_b64)
    client = connect(m, keys)
    try:
        code, out, err = _run(client, launcher, timeout=30)
    finally:
        client.close()
    if code != 0:
        raise RemoteError(f"submit failed (exit {code}): {err.strip() or out.strip()}")
    return {"command": "submit", "machine": m["name"], "job": jobid,
            "job_dir": f"{_job_root(m)}/{JOBS_DIR}/{jobid}",
            "notify_webhook": webhook or None}


def cmd_status(m: dict, keys: dict, jobid: str) -> dict:
    # R1629: a QUEUED job has a job dir but no pgid, so the old chain fell through to
    # `started` — a job whose payload has never executed was indistinguishable from one
    # that is running. The distinction is load-bearing: a caller waiting on "running"
    # cannot tell "waiting my turn in the queue" from "my code is executing", and (R1698)
    # a payload that died instantly also reads as `started` because the dir exists.
    # So: report the queue position when a ticket says waiting, and separate a dir with
    # no pgid ever written (`pending`) from one whose process is gone (`stopped`).
    snippet = _job_cmd(m, jobid,
                       'if [ -f "$JOBDIR/exit" ]; then echo "exit=$(cat "$JOBDIR/exit")"; '
                       'elif [ -f "$JOBDIR/pgid" ] && kill -0 -"$(cat "$JOBDIR/pgid")" '
                       '2>/dev/null; then echo running; '
                       'elif [ -f "$JOBDIR/queued" ]; then '
                       'echo "queued=$(cat "$JOBDIR/queued" 2>/dev/null || echo 1)"; '
                       'elif [ -f "$JOBDIR/pgid" ]; then echo stopped; '
                       'elif [ -d "$JOBDIR" ]; then echo pending; else echo nojob; fi')
    client = connect(m, keys)
    try:
        _code, out, _err = _run(client, snippet, timeout=30)
    finally:
        client.close()
    res = {"command": "status", "machine": m["name"], "job": jobid}
    res.update(_decode_status(out.strip()))
    return res


def _decode_status(state: str) -> dict:
    """Map the status snippet's token to the caller-facing state. Pure, so it is testable
    without a bound machine — the mapping is the part callers branch on (R1629/R1698)."""
    exit_code = int(state.split("=", 1)[1]) if state.startswith("exit=") else None
    res = {"state": "done" if exit_code is not None else state, "exit": exit_code}
    if state.startswith("queued="):
        res["state"] = "queued"
        res["started"] = False
        pos = state.split("=", 1)[1].strip()
        if pos and pos != "1":
            res["queue_note"] = pos
    elif res["state"] == "running":
        res["started"] = True
    elif res["state"] in ("pending", "stopped"):
        res["started"] = False
        if res["state"] == "stopped":
            res["ran"] = True
    return res


def cmd_logs(m: dict, keys: dict, jobid: str, tail: int, tail_lines: int = 0) -> dict:
    # R1496: `--tail` counts BYTES (`tail -c`). A caller who reads the flag as "last N
    # lines" and passes `--tail 20` gets a 20-BYTE fragment, which is indistinguishable
    # from the flag being ignored — which is exactly how it was reported. The unit is now
    # stated in the usage line, and a caller who means lines can say so.
    sel = f'-n {int(tail_lines)}' if tail_lines else f'-c {int(tail)}'
    snippet = _job_cmd(m, jobid,
                       f'echo "===STDOUT==="; tail {sel} "$JOBDIR/stdout" 2>/dev/null; '
                       f'echo; echo "===STDERR==="; tail {sel} "$JOBDIR/stderr" 2>/dev/null')
    client = connect(m, keys)
    try:
        _code, out, _err = _run(client, snippet, timeout=30)
    finally:
        client.close()
    stdout, _, rest = out.partition("===STDERR===")
    return {"command": "logs", "machine": m["name"], "job": jobid,
            "stdout": stdout.replace("===STDOUT===", "", 1).strip(), "stderr": rest.strip()}


def cmd_cancel(m: dict, keys: dict, jobid: str) -> dict:
    """Cancel a job and report what actually happened to it.

    R1618: this used to send ONE `kill -TERM` and echo `cancelled` on the strength of
    kill's own exit code. kill succeeding means the signal was DELIVERED, not that the
    process died — a job that traps TERM, or that is mid-syscall, or whose children
    outlive the group leader, kept running while the caller was told it was cancelled.
    The observed cost: a metered external API billed on after "cancellation", and a
    queue ticket left `running` so the next queued job never started.

    So: TERM, wait, re-check the group; escalate to KILL, wait, re-check again; and
    report the OUTCOME. `result` is `cancelled` only when the group is provably gone.
    A survivor yields `still_running` with the pgid, so the caller learns the truth.
    """
    snippet = _job_cmd(m, jobid, r'''
PG="$(cat "$JOBDIR/pgid" 2>/dev/null)"
if [ -z "$PG" ]; then echo "not running"; exit 0; fi
if ! kill -0 -"$PG" 2>/dev/null; then echo "not running"; exit 0; fi
kill -TERM -"$PG" 2>/dev/null
for _ in 1 2 3 4 5 6 7 8 9 10; do
  kill -0 -"$PG" 2>/dev/null || { echo cancelled; exit 0; }
  sleep 0.5
done
kill -KILL -"$PG" 2>/dev/null
for _ in 1 2 3 4 5 6; do
  kill -0 -"$PG" 2>/dev/null || { echo "cancelled (SIGKILL)"; exit 0; }
  sleep 0.5
done
echo "still_running pgid=$PG"
''')
    client = connect(m, keys)
    try:
        _code, out, _err = _run(client, snippet, timeout=30)
    finally:
        client.close()
    result = out.strip()
    payload = {"command": "cancel", "machine": m["name"], "job": jobid, "result": result}
    if result.startswith("still_running"):
        # Loudly, and non-zero at the CLI: a control verb must never report the outcome
        # it intended in place of the one it achieved.
        payload["ok"] = False
        payload["error"] = (
            f"cancel FAILED: job {jobid} survived SIGTERM and SIGKILL on {m['name']!r} "
            f"({result}). It is STILL RUNNING and its queue ticket is still held — "
            "check it with `gu remote status` / `gu remote queue` before retrying.")
    else:
        payload["ok"] = True
    return payload


def _submit_queued(m: dict, keys: dict, jobid: str, script_b64: str, webhook: str,
                   deadline_h: float, est_min: int) -> dict:
    """`submit` on an exclusive machine: take a TICKET instead of the card, and return as fast as
    the plain path does. The run gets a job id and a POSITION, never a wait — that is the whole
    point of a queue over a lock, so the run can spend itself on work this machine is not needed
    for.
    """
    from datetime import datetime, timedelta, timezone

    root = _job_root(m)
    deadline_s = max(60, int(float(deadline_h) * 3600))
    submitted = datetime.now(timezone.utc)
    # WHO is asking comes from the environment, never from an argument — a routine cannot forge
    # it, so the rotation is over real holders.
    holder = (os.environ.get("RSCHED_ROUTINE") or "").strip() or "unknown"
    ticket = {"holder": holder, "job": jobid, "submitted": submitted.isoformat(),
              "deadline_s": deadline_s, "est_min": int(est_min or 0), "state": "waiting"}
    launcher = build_queued_launcher(
        root, jobid, ticket_name(holder, jobid, ticket["submitted"]), _b64(json.dumps(ticket)),
        _b64(build_queue_helper()), script_b64, _b64(build_wrapper(jobid, root, deadline_s)))
    client = connect(m, keys)
    try:
        code, out, err = _run(client, launcher, timeout=60)
    finally:
        client.close()
    if code != 0:
        raise RemoteError(f"submit failed (exit {code}): {err.strip() or out.strip()}")
    try:
        tickets = [t for t in json.loads(out or "[]") if isinstance(t, dict)]
    except ValueError:
        tickets = []
    order = fair_share_order(tickets)
    position = next((i for i, t in enumerate(order, 1) if str(t.get("job")) == jobid), None)
    return {"command": "submit", "machine": m["name"], "job": jobid,
            "job_dir": f"{root}/{JOBS_DIR}/{jobid}", "notify_webhook": webhook or None,
            "queued": True, "holder": holder, "position": position,
            "ahead": (position - 1) if position else 0, "est_min": int(est_min or 0),
            "deadline": (submitted + timedelta(seconds=deadline_s)).isoformat(),
            "queue": [_ticket_view(t) for t in order]}


def cmd_queue(m: dict, keys: dict, cancel_job: str) -> dict:
    """The machine's live tickets in fair-share order — what the scheduler mirrors every tick
    (rsched/machine_queue.refresh) and what an operator reads to see whose turn it is. Reading
    also PRUNES on the box, so a dead holder's ticket clears even when nobody is waiting behind
    it. `--cancel` drops one ticket, terminating its job if it had already started.
    """
    if cancel_job and not _JOBID_RE.fullmatch(cancel_job):
        raise RemoteError(f"invalid job id {cancel_job!r} (expected [A-Za-z0-9_.-])")
    if not m.get("exclusive"):
        # Not an empty queue — no queue at all. Saying so beats bootstrapping a queue dir onto a
        # box that will never use one, and beats reporting "free" for a machine nobody schedules.
        if cancel_job:
            raise RemoteError(f"machine {m['name']!r} is not exclusive, so its jobs are not "
                              "queued and there is no ticket to cancel (use `cancel --job`)")
        return {"command": "queue", "machine": m["name"], "exclusive": False, "tickets": []}
    helper_b64 = _b64(build_queue_helper())
    root, result = _job_root(m), ""
    client = connect(m, keys)
    try:
        if cancel_job:
            _c, out, err = _run(client, build_queue_command(root, helper_b64,
                                                            f"cancel {cancel_job}"), timeout=30)
            result = out.strip() or err.strip()
        code, out, err = _run(client, build_queue_command(root, helper_b64, "list"), timeout=30)
    finally:
        client.close()
    if code != 0:
        raise RemoteError(f"could not read {m['name']}'s job queue (exit {code}): "
                          f"{err.strip() or out.strip()}")
    try:
        tickets = [t for t in json.loads(out or "[]") if isinstance(t, dict)]
    except ValueError as exc:
        raise RemoteError(f"{m['name']} did not return a readable job queue: "
                          f"{out[:200]!r}") from exc
    payload = {"command": "queue", "machine": m["name"], "exclusive": True,
               "tickets": [_ticket_view(t) for t in fair_share_order(tickets)]}
    if cancel_job:
        payload["cancelled"], payload["result"] = cancel_job, result
    return payload


def _strip_home(path: str) -> str:
    """SFTP has no shell ~ expansion; a leading ~/ maps to the SFTP default dir (the home)."""
    return path[2:] if path.startswith("~/") else path


def plan_push_tree(src: str, dest: str) -> tuple[list[str], list[tuple[str, str]]]:
    """Walk a local directory and return (remote dirs to create, [(local file, remote path)]),
    both in parent-before-child order so a directory always exists before anything lands in it.

    Paths are joined with "/" REGARDLESS of the local separator: the destination is a remote
    POSIX path, and os.path.join would emit a backslash on a non-POSIX client. Pure — the
    selftest asserts the shape without touching the network.
    """
    src_root = src.rstrip(os.sep) or src
    dest_root = _strip_home(dest).rstrip("/") or _strip_home(dest)
    dirs: list[str] = [dest_root]
    files: list[tuple[str, str]] = []
    for cur, subdirs, filenames in os.walk(src_root):
        subdirs.sort()
        rel = os.path.relpath(cur, src_root)
        remote_dir = dest_root if rel == "." else f"{dest_root}/" + rel.replace(os.sep, "/")
        if remote_dir not in dirs:
            dirs.append(remote_dir)
        for fn in sorted(filenames):
            local = os.path.join(cur, fn)
            if os.path.isfile(local):          # skip sockets/fifos/broken symlinks
                files.append((local, f"{remote_dir}/{fn}"))
    return dirs, files


def _sftp_mkdir_p(sftp, path: str) -> None:
    """Create a remote directory, tolerating one that already exists (SFTP has no mkdir -p)."""
    try:
        sftp.stat(path)
        return
    except OSError:
        pass
    try:
        sftp.mkdir(path)
    except OSError as exc:                     # a racing creator, or a file in the way
        try:
            sftp.stat(path)
        except OSError:
            raise RemoteError(f"could not create remote directory {path}: {exc}") from exc


def cmd_push(m: dict, keys: dict, src: str, dest: str) -> dict:
    """Upload a local FILE, or a whole DIRECTORY TREE recursively, over SFTP.

    PARTIAL FAILURE (the caller's next decision depends on this): files are sent one at a time
    in a deterministic order, and the FIRST failure stops the transfer. The result then reports
    ok=False with `uploaded` (what definitely landed), `failed` (the one that did not, with its
    error) and `remaining` (never attempted) — so a retry of the same command is safe and simply
    re-sends what is already there. Nothing is deleted on the remote, ever.
    """
    if not os.path.exists(src):
        raise RemoteError(f"local path not found: {src}")
    is_dir = os.path.isdir(src)
    if not is_dir and not os.path.isfile(src):
        raise RemoteError(f"local path is neither a file nor a directory: {src}")

    client = connect(m, keys)
    try:
        sftp = client.open_sftp()
        if not is_dir:
            sftp.put(src, _strip_home(dest))
            return {"command": "push", "machine": m["name"], "src": src, "dest": dest,
                    "bytes": os.path.getsize(src), "ok": True}
        dirs, files = plan_push_tree(src, dest)
        for d in dirs:
            _sftp_mkdir_p(sftp, d)
        uploaded, total = [], 0
        for i, (local, remote) in enumerate(files):
            try:
                sftp.put(local, remote)
            except OSError as exc:
                # Stop here and SAY what landed — a half-synced folder the caller cannot see
                # into is worse than a failure it can read.
                return {"command": "push", "machine": m["name"], "src": src, "dest": dest,
                        "directory": True, "ok": False, "files": len(uploaded),
                        "bytes": total, "dirs": len(dirs), "uploaded": uploaded,
                        "failed": {"src": local, "dest": remote, "error": str(exc)},
                        "remaining": [lp for lp, _rp in files[i + 1:]]}
            total += os.path.getsize(local)
            uploaded.append(remote)
        return {"command": "push", "machine": m["name"], "src": src, "dest": dest,
                "directory": True, "ok": True, "files": len(uploaded), "bytes": total,
                "dirs": len(dirs), "uploaded": uploaded}
    finally:
        client.close()


def _resolve_pull_dest(src: str, dest: str) -> str:
    """Resolve the final LOCAL path for `pull` and ensure its parent dir exists. If dest is an
    existing directory the file lands inside it under src's basename; then the parent of the
    final path is created (mkdir -p) so SFTP get()'s local open never fails with a bare
    FileNotFoundError on a missing --dest parent (R1140 / R1176). Pure except for the mkdir."""
    if os.path.isdir(dest):
        dest = os.path.join(dest, os.path.basename(src))
    parent = os.path.dirname(os.path.abspath(dest))
    if parent:
        os.makedirs(parent, exist_ok=True)
    return dest


def cmd_pull(m: dict, keys: dict, src: str, dest: str) -> dict:
    """Download ONE remote file.

    R1491: pointing this at a remote DIRECTORY used to raise a bare `OSError` out of
    paramiko — an error naming neither the path nor the reason — AFTER the local file
    had already been created, leaving a 0-byte artefact the caller then mistook for a
    real (empty) download. So: stat the source first and refuse a directory with a
    message that teaches the working call, and never leave a partial file behind.
    """
    import stat as _stat

    client = connect(m, keys)
    try:
        sftp = client.open_sftp()
        remote = _strip_home(src)
        try:
            st = sftp.stat(remote)
        except OSError as exc:
            raise RemoteError(
                f"remote pull: cannot stat {src!r} on {m['name']!r} ({exc}). "
                "Check the path exists and is readable by this routine's SSH user.") from exc
        if _stat.S_ISDIR(st.st_mode):
            raise RemoteError(
                f"remote pull: {src!r} on {m['name']!r} is a DIRECTORY, and pull fetches one "
                "FILE. Name a file inside it, or tar it on the box first — e.g. "
                f"`gu remote exec {m['name']} --command \"tar czf /tmp/x.tgz -C {src} .\"` "
                f"then `gu remote pull {m['name']} --src /tmp/x.tgz --dest ./x.tgz`.")
        dest = _resolve_pull_dest(src, dest)  # mkdir -p the local parent before SFTP get()
        # Download to a temp SIBLING and rename on success. Writing straight to `dest`
        # meant a failed transfer left a 0-byte file — and, worse, TRUNCATED an existing
        # good file at that path before failing, destroying it. The destination is now
        # never touched until the bytes are actually here.
        tmp = f"{dest}.part-{os.getpid()}"
        try:
            sftp.get(remote, tmp)
            size = os.path.getsize(tmp)
            os.replace(tmp, dest)
        except BaseException:
            if os.path.exists(tmp):
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
            raise
    finally:
        client.close()
    return {"command": "pull", "machine": m["name"], "src": src, "dest": dest,
            "bytes": size, "ok": True}


def cmd_scan_host(host: str, port: int) -> dict:
    """Read a host's public host key(s) — for PINNING in the Settings card (this is the one
    command that does NOT verify a pinned key, since pinning is exactly what it bootstraps).
    """
    import socket

    import paramiko

    lines = []
    for keytype in ("ssh-ed25519", "ecdsa-sha2-nistp256", "ssh-rsa"):
        transport = None
        try:
            sock = socket.create_connection((host, int(port)), timeout=15)
            transport = paramiko.Transport(sock)
            transport.get_security_options().key_types = (keytype,)
            transport.start_client(timeout=15)
            key = transport.get_remote_server_key()
            lines.append(f"{key.get_name()} {key.get_base64()}")
            break                                  # first algo the server offers is enough
        except (OSError, EOFError, paramiko.SSHException):
            continue
        finally:
            if transport is not None:
                transport.close()
    if not lines:
        raise RemoteError(f"could not read a host key from {host}:{port} (unreachable?)")
    return {"command": "scan-host", "host": host, "port": int(port), "host_key": "\n".join(lines)}


def cmd_test(m: dict, keys: dict) -> dict:
    client = connect(m, keys)
    try:
        code, _out, _err = _run(client, "true", timeout=15)
    finally:
        client.close()
    return {"command": "test", "machine": m["name"], "ok": code == 0,
            "host": m["host"], "user": m["user"], "port": m.get("port", 22)}


# ----------------------------------------------------------------------------- selftest ------
def selftest() -> int:
    # host-key line normalization (port 22 → bare host; other → [host]:port; ssh-keyscan lines)
    assert hostkey_lines("h", 22, "ssh-ed25519 AAAA") == ["h ssh-ed25519 AAAA"]
    assert hostkey_lines("h", 2222, "ssh-ed25519 AAAA") == ["[h]:2222 ssh-ed25519 AAAA"]
    assert hostkey_lines("h", 22, "h ssh-rsa BBBB\n\nbad") == ["h ssh-rsa BBBB"], "keyscan+skip"
    # job script: redirections + exit capture; --cwd changes only the command dir; webhook opt
    s = build_job_script("nvidia-smi", "job1", "", "")
    assert ") > stdout 2> stderr" in s and "echo $code > exit" in s, s
    assert s.lstrip().startswith(PATH_PREFIX.strip()), "a job.sh gets ~/.local/bin too (R1730)"
    assert s.splitlines()[1] == "(", "job body is a subshell so a user `exit N` is captured"
    assert "curl" not in s, "no webhook → no curl"
    assert "cd /data" in build_job_script("run", "j", "/data", ""), "cwd cd'd"
    assert "cd '/a b'" in build_job_script("run", "j", "/a b", ""), "cwd is shell-quoted"
    assert "curl" in build_job_script("run", "j", "", "https://x/hook"), "webhook → curl"
    # launcher: detached setsid, job dir under the given root, prints the id
    lz = build_launcher("$HOME", "abcd", "QUk=")
    assert "setsid" in lz and ".rsched-jobs/abcd" in lz and "echo abcd" in lz, lz
    # env loader tolerates junk and drops nameless/typeless entries
    os.environ["RSCHED_MACHINES"] = '[{"name":"g","host":"h"},{"host":"noname"}]'
    os.environ["RSCHED_MACHINE_KEYS"] = '{"g":"PEM"}'
    mach, keys = load_machines()
    assert list(mach) == ["g"] and keys == {"g": "PEM"}, (mach, keys)
    os.environ["RSCHED_MACHINES"] = "not json"
    assert load_machines()[0] == {}, "bad json → no machines"
    out, trunc = _capped("x" * (CAP + 50))
    assert trunc and len(out) < CAP + 60, "over-cap output is elided"
    # pull dest resolution creates the local parent dir (R1140/R1176: no bare FileNotFoundError)
    import tempfile
    td = tempfile.mkdtemp()
    nested = os.path.join(td, "a", "b", "out.bin")
    assert _resolve_pull_dest("/remote/x.bin", nested) == nested
    assert os.path.isdir(os.path.dirname(nested)), "pull must mkdir -p the dest parent"
    got = _resolve_pull_dest("/remote/name.txt", td)
    assert got == os.path.join(td, "name.txt") and os.path.isdir(td), "existing-dir dest lands inside it"
    # push of a DIRECTORY (R1413/R1420: one turn per file was the whole cost of syncing a folder)
    tree = os.path.join(td, "personas")
    os.makedirs(os.path.join(tree, "nested", "deep"))
    for rel in ("a.txt", "b.txt", os.path.join("nested", "c.txt"),
                os.path.join("nested", "deep", "d.txt")):
        with open(os.path.join(tree, rel), "w", encoding="utf-8") as fh:
            fh.write("x")
    dirs, files = plan_push_tree(tree, "~/qa/personas")
    assert dirs[0] == "qa/personas", dirs           # ~/ maps to the SFTP home, like push/pull
    assert dirs == ["qa/personas", "qa/personas/nested", "qa/personas/nested/deep"], dirs
    assert [r for _l, r in files] == ["qa/personas/a.txt", "qa/personas/b.txt",
                                      "qa/personas/nested/c.txt",
                                      "qa/personas/nested/deep/d.txt"], files
    assert all("\\" not in r for _l, r in files), "remote paths are POSIX regardless of client"
    # every dir BELOW the root is preceded by its parent, so nothing lands in a missing dir
    # (the root's own parent is the pre-existing destination parent and is not ours to create)
    for i, d in enumerate(dirs[1:], start=1):
        assert d.rsplit("/", 1)[0] in dirs[:i], (d, dirs)
    # a trailing slash on either side must not double up or lose the root
    d2, f2 = plan_push_tree(tree + os.sep, "qa/personas/")
    assert (d2, f2) == (dirs, files), (d2, f2)
    # an empty directory still gets created remotely (its files are simply none)
    empty = os.path.join(td, "empty")
    os.makedirs(os.path.join(empty, "sub"))
    de, fe = plan_push_tree(empty, "dst")
    assert de == ["dst", "dst/sub"] and fe == [], (de, fe)
    # THE message that misdescribed the cause: a missing path says "path", not "file"
    try:
        cmd_push({"name": "m"}, {}, os.path.join(td, "no-such-thing"), "d")
    except RemoteError as exc:
        assert "local path not found" in str(exc), exc
    else:
        raise AssertionError("a missing local path must be refused")
    from unittest.mock import Mock, patch

    # --- R1813: a timed-out exec REPORTS, carrying what the command already printed ---
    fake = Mock()
    held = ExecTimeout("SSH I/O timeout after 155s", "PID=4072019\n", "warming up\n")
    with patch(__name__ + '.connect', return_value=fake), \
            patch(__name__ + '._run', side_effect=held):
        payload, code = cmd_exec({'name': 'fixture'}, {}, 'private command', 155, '')
    assert code == 1 and payload['timed_out'] is True and payload['exit'] == -1, payload
    assert payload['stdout'] == "PID=4072019\n", "partial output must survive the deadline"
    assert payload['stderr'] == "warming up\n", payload
    message = payload['error']
    assert '--timeout 155s' in message and 'action timeout_s' in message, message
    assert 'still be running' in message and 'remote submit' in message, message
    assert 'private command' not in message, message
    fake.close.assert_called_once()

    # --- R1813/R1730: what `_run` actually ships, and when it stops reading ---
    class _Chan:
        """A channel whose command has exited while something still holds the pipes."""

        def __init__(self, out=b"", err=b"", eof=False, code=0):
            self._out, self._err, self.eof_received, self._code = out, err, eof, code
            self.closed = False

        def recv_ready(self):
            return bool(self._out)

        def recv(self, n):
            chunk, self._out = self._out[:n], self._out[n:]
            return chunk

        def recv_stderr_ready(self):
            return bool(self._err)

        def recv_stderr(self, n):
            chunk, self._err = self._err[:n], self._err[n:]
            return chunk

        def exit_status_ready(self):
            return True

        def recv_exit_status(self):
            return self._code

        def close(self):
            self.closed = True

    class _Client:
        def __init__(self, chan):
            self.chan, self.sent = chan, ""

        def exec_command(self, command, timeout=None):
            self.sent = command
            return None, Mock(channel=self.chan), Mock(channel=self.chan)

    chan = _Chan(out=b"PID=4072019\n", eof=False)      # a launcher subshell still holds the fds
    client = _Client(chan)
    code, out, err = _run(client, "nohup ./train.sh &", 30, cwd="~/video2funscript")
    assert client.sent.startswith(PATH_PREFIX), client.sent
    assert "|| exit 1;" in client.sent and "&&" not in client.sent, client.sent
    assert code == 0 and out == "PID=4072019\n", (code, out)
    assert "left a process holding" in err and "nohup" in err, err   # names the trap, no hang
    assert chan.closed, "the channel must be released"
    quiet = _Client(_Chan(out=b"done\n", eof=True))
    code, out, err = _run(quiet, "echo done", 30)
    assert (code, out, err) == (0, "done\n", ""), (code, out, err)   # EOF: no note, no noise

    # --- R1813: ONE clock. The SSH deadline follows the engine's own budget for this call ---
    os.environ["RSCHED_UTIL_TIMEOUT_S"] = "120"
    assert default_exec_timeout() == 120 - EXEC_TIMEOUT_MARGIN_S, default_exec_timeout()
    os.environ["RSCHED_UTIL_TIMEOUT_S"] = "not-a-number"
    assert default_exec_timeout() == DEFAULT_EXEC_TIMEOUT_S
    del os.environ["RSCHED_UTIL_TIMEOUT_S"]
    assert default_exec_timeout() == DEFAULT_EXEC_TIMEOUT_S

    # --- R1618: `cancel` must report what it ACHIEVED, never what it intended ---
    # It used to send one SIGTERM and echo "cancelled" on kill's exit code alone, so a
    # job that traps TERM (or outlives its group leader) kept running — and kept billing
    # a metered API — while the caller was told it had stopped, with its queue ticket
    # still held so nothing else could start.
    _saved_connect, _saved_run = connect, _run
    try:
        class _C:
            def close(self): pass
        globals()["connect"] = lambda *a, **k: _C()
        _seen = {}

        def _mk(out):
            def _fake(client, command, timeout, cwd=""):
                _seen["cmd"] = command
                return 0, out, ""
            return _fake

        _mach = {"name": "box", "root": "/srv/x"}
        for _out, _ok in (("cancelled", True),
                          ("cancelled (SIGKILL)", True),
                          ("not running", True),
                          ("still_running pgid=4072019", False)):
            globals()["_run"] = _mk(_out)
            _p = cmd_cancel(_mach, {}, "j1")
            assert _p["ok"] is _ok, ("cancel reported the wrong outcome", _out, _p)
            if not _ok:
                assert "STILL RUNNING" in _p["error"] and "j1" in _p["error"], _p
                assert "ticket" in _p["error"], "a survivor still holds its queue ticket"
        _snip = _seen["cmd"]
        assert "kill -TERM" in _snip and "kill -KILL" in _snip, "must escalate TERM → KILL"
        assert _snip.count("kill -0") >= 2, "must RE-CHECK the group, not trust the signal"

        # --- R1496: --tail is BYTES; --tail-lines must really switch to lines ---
        globals()["_run"] = _mk("===STDOUT===\nx\n===STDERR===\ny")
        cmd_logs(_mach, {}, "j1", 8000, 0)
        assert "tail -c 8000" in _seen["cmd"], ("--tail must stay BYTES", _seen["cmd"])
        cmd_logs(_mach, {}, "j1", 8000, 25)
        assert "tail -n 25" in _seen["cmd"], ("--tail-lines must select LINES", _seen["cmd"])
        assert "tail -c" not in _seen["cmd"], "line mode must not also pass a byte cap"
    finally:
        globals()["connect"], globals()["_run"] = _saved_connect, _saved_run

    # --- R1491: `pull` on a directory, and never destroying the destination ---
    # It used to call sftp.get() straight on the source: a remote DIRECTORY raised a bare
    # OSError naming neither path nor reason, AFTER the local file had been created —
    # a 0-byte artefact that reads like a real empty download. Worse, writing straight to
    # `dest` TRUNCATED an existing good file at that path before failing.
    import stat as _st
    import tempfile as _tf

    class _Stat:
        def __init__(self, mode): self.st_mode = mode

    class _SFTP:
        def __init__(self, mode, fail=False): self.mode, self.fail = mode, fail
        def stat(self, _p): return _Stat(self.mode)
        def get(self, _r, local):
            open(local, "w").close()              # paramiko creates the local file first
            if self.fail:
                raise OSError("Failure")
            with open(local, "w") as fh:
                fh.write("REALDATA")

    class _Client:
        def __init__(self, s): self._s = s
        def open_sftp(self): return self._s
        def close(self): pass

    _saved_connect2 = connect
    try:
        _d = _tf.mkdtemp()
        _mach2 = {"name": "box", "root": "/srv/x"}

        globals()["connect"] = lambda *a, **k: _Client(_SFTP(_st.S_IFDIR | 0o755))
        _p = os.path.join(_d, "out.bin")
        try:
            cmd_pull(_mach2, {}, "/data/ckpt", _p)
            raise AssertionError("pull on a DIRECTORY must fail, not return")
        except RemoteError as exc:
            assert "DIRECTORY" in str(exc) and "/data/ckpt" in str(exc), exc
            assert "tar" in str(exc), "the error must teach the working call"
        assert not os.path.exists(_p), "a refused pull must leave NO local file"

        globals()["connect"] = lambda *a, **k: _Client(_SFTP(_st.S_IFREG | 0o644, fail=True))
        _keep = os.path.join(_d, "keep.bin")
        with open(_keep, "w") as fh:
            fh.write("precious")
        try:
            cmd_pull(_mach2, {}, "/data/f.bin", _keep)
            raise AssertionError("a failing transfer must raise")
        except OSError:
            pass
        with open(_keep) as fh:
            assert fh.read() == "precious", "a FAILED pull destroyed the existing file"
        assert not [f for f in os.listdir(_d) if ".part-" in f], "temp file left behind"

        globals()["connect"] = lambda *a, **k: _Client(_SFTP(_st.S_IFREG | 0o644))
        _ok = cmd_pull(_mach2, {}, "/data/f.bin", os.path.join(_d, "ok.bin"))
        assert _ok["bytes"] == 8 and _ok["ok"] is True, _ok
        assert not [f for f in os.listdir(_d) if ".part-" in f], "temp file left on success"
    finally:
        globals()["connect"] = _saved_connect2

    # --- status mapping (R1629, R1698). The whole value of `status` is that a caller can
    # tell these apart; when everything without an exit file read as `running`/`started`,
    # a queued job and a payload that died instantly were both indistinguishable from
    # healthy execution. Each token the on-box snippet can emit is pinned here.
    assert _decode_status("exit=0") == {"state": "done", "exit": 0}
    assert _decode_status("exit=137")["exit"] == 137
    _run_st = _decode_status("running")
    assert _run_st["state"] == "running" and _run_st["started"] is True, _run_st
    _q = _decode_status("queued=1")
    assert _q["state"] == "queued", "a queued job must NOT read as running/started"
    assert _q["started"] is False, _q
    _q3 = _decode_status("queued=3 ahead")
    assert _q3["state"] == "queued" and _q3["queue_note"] == "3 ahead", _q3
    _p = _decode_status("pending")
    assert _p["state"] == "pending" and _p["started"] is False, _p
    _s = _decode_status("stopped")
    assert _s["state"] == "stopped" and _s["started"] is False and _s["ran"] is True, _s
    assert _decode_status("nojob")["state"] == "nojob"
    # the on-box snippet must actually be able to emit every token the decoder handles,
    # or the mapping is dead code that tests itself
    import inspect as _inspect
    _snip = _inspect.getsource(cmd_status)
    for _tok in ("queued=", "echo stopped", "echo pending", "echo running", "echo nojob"):
        assert _tok in _snip, f"status snippet can never emit {_tok!r}"

    selftest_queue()
    print("selftest: ok", file=sys.stderr)
    return 0


def selftest_queue() -> int:
    """The fair-share queue, offline: the ORDER, the shipped on-box half actually running it,
    and the shapes of the two scripts an exclusive machine is handed.
    """
    import datetime as dt
    import subprocess
    import tempfile

    def tk(holder, job, submitted):
        return {"holder": holder, "job": job, "submitted": submitted}

    # THE property, pinned against the scheduler's own test (tests/test_machine_queue.py): three
    # jobs from one routine must not starve one job from another. FIFO would answer f1 f2 f3 v1.
    order = fair_share_order([tk("funscript", "f1", "1"), tk("funscript", "f2", "2"),
                              tk("funscript", "f3", "3"), tk("voice", "v1", "4")])
    assert [t["job"] for t in order] == ["f1", "v1", "f2", "f3"], order
    # a newcomer joins the END of the rotation; one holder alone is plain FIFO
    assert [t["job"] for t in fair_share_order(
        [tk("a", "a1", "1"), tk("b", "b1", "2"), tk("c", "c1", "3"), tk("a", "a2", "4")])] \
        == ["a1", "b1", "c1", "a2"]
    assert [t["job"] for t in fair_share_order([tk("a", "a2", "2"), tk("a", "a1", "1")])] \
        == ["a1", "a2"]
    assert fair_share_order([]) == []
    # the mirror carries the six contract keys and none of the box's own bookkeeping
    assert set(_ticket_view({**tk("h", "j", "s"), "deadline_s": 1, "est_min": 2, "pgid": 999,
                             "state": "waiting", "started": "x"})) == set(TICKET_KEYS)
    assert ticket_name("voice-model-trainer", "abc", "2026-09-05T10:00:00+00:00") == \
        "2026-09-05T10:00:00+00:00-voice-model-trainer-abc.json"
    assert "/" not in ticket_name("../evil", "j", "S"), "a holder never escapes the ticket dir"
    assert ticket_name("", "j", "S").endswith("-unknown-j.json")

    # the SAME function, run by the on-box helper this util ships: prove the shipped copy
    # executes and answers identically, prunes, and re-orders as tickets come and go
    box = os.path.join(tempfile.mkdtemp(), QUEUE_DIR)
    os.makedirs(os.path.join(box, "tickets"))
    helper = os.path.join(box, "queue.py")
    with open(helper, "w", encoding="utf-8") as fh:
        fh.write(build_queue_helper())
    now = dt.datetime.now(dt.timezone.utc)
    # NEVER give a selftest ticket a live process group: prune TERMs an expired job's group.
    for holder, job, ago, life in (("funscript", "f1", 40, 3600), ("funscript", "f2", 30, 3600),
                                   ("funscript", "f3", 20, 3600), ("voice", "v1", 10, 3600),
                                   ("stale", "z9", 7200, 60)):
        stamp = (now - dt.timedelta(seconds=ago)).isoformat()
        with open(os.path.join(box, "tickets", ticket_name(holder, job, stamp)), "w",
                  encoding="utf-8") as fh:
            json.dump({"holder": holder, "job": job, "submitted": stamp, "deadline_s": life,
                       "est_min": 0, "state": "waiting"}, fh)

    def q(*args):
        done = subprocess.run([sys.executable, helper, *args], capture_output=True, text=True,
                              check=True)
        return done.stdout.strip()

    assert [t["job"] for t in json.loads(q("list"))] == ["f1", "v1", "f2", "f3"], \
        "the on-box order IS the order this util reports (and z9, past its deadline, is pruned)"
    assert q("head", "f1", str(os.getpgrp())) == "yes", "the head of the order may start"
    assert q("head", "v1", str(os.getpgrp())) == "no", "nobody else may"
    assert q("head", "ghost", "1") == "gone", "a pruned ticket tells its wrapper not to start"
    assert q("claim", "f1") == ""
    running = [t for t in json.loads(q("list")) if t["state"] == "running"]
    assert [t["job"] for t in running] == ["f1"] and running[0].get("started"), running
    assert q("drop", "f1") == "dropped"
    # THE regression the `round/` markers exist for: a SPENT turn is remembered, so the card goes
    # to the other routine next. Re-deriving the rotation from what is merely left would answer
    # f2 here, and funscript's three jobs would all run before voice's one — plain FIFO.
    assert [t["job"] for t in json.loads(q("list"))] == ["v1", "f2", "f3"], "the turn was spent"
    assert q("head", "v1", str(os.getpgrp())) == "yes", "the other routine goes next"
    # a ticket that never started spends no turn, so dropping it leaves the rotation where it was
    assert q("drop", "f2") == "dropped"
    assert [t["job"] for t in json.loads(q("list"))] == ["v1", "f3"]

    # the two scripts an exclusive machine is handed
    w = build_wrapper("abcd", "$HOME", 21600)
    assert "flock -n 9" in w and "sleep 5" in w, "waits for its turn without blocking the lock"
    assert "timeout -k 30 21600 bash job.sh 9>&-" in w, "deadline enforced, lock fd not inherited"
    assert "trap cleanup EXIT INT TERM" in w and 'drop "$JOB"' in w, "a killed job frees the queue"
    assert "bash job.sh" in w and "[ -f exit ]" in w, "status/logs/cancel keep working unchanged"
    lz = build_queued_launcher("$HOME", "abcd", "t.json", "QUk=", "QUk=", "QUk=", "QUk=")
    assert '$QDIR/tickets/t.json' in lz and ".rsched-queue" in lz and ".rsched-jobs/abcd" in lz, lz
    assert lz.index('queue.py" list') < lz.index("setsid"), \
        "the queue is read BEFORE the wrapper launches, so the reported position is the real one"
    assert 'python3 "$QDIR/queue.py" list' in build_queue_command("$HOME", "QUk=", "list")
    return 0


def _emit(payload: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    cmd = payload.get("command")
    if cmd == "list":
        for m in payload["machines"]:
            flag = "" if m["ready"] else "  [not ready — scan host key / set key]"
            desc = f" — {m['description']}" if m["description"] else ""
            print(f"- {m['name']} ({m['user']}@{m['host']}:{m['port']}){desc}{flag}")
        if not payload["machines"]:
            print("(no machines bound to this routine)")
    elif cmd == "exec":
        if payload["stdout"]:
            print(payload["stdout"])
        if payload["stderr"]:
            print(payload["stderr"], file=sys.stderr)
        print(f"[exit {payload['exit']}]", file=sys.stderr)
    elif cmd == "queue":
        for i, t in enumerate(payload["tickets"], 1):
            est = f", ~{t['est_min']}min" if t.get("est_min") else ""
            print(f"{i}. {t['holder']} — {t['job']} [{t['state']}] "
                  f"submitted {t['submitted']}{est}")
        if not payload["tickets"]:
            print("(no jobs queued)" if payload.get("exclusive")
                  else "(this machine is not exclusive — its jobs are not queued)")
    elif cmd == "logs":
        print(f"--- stdout ---\n{payload['stdout']}\n--- stderr ---\n{payload['stderr']}")
    else:
        print(json.dumps(payload, ensure_ascii=False))


def main() -> int:
    # --json lives on a shared PARENT so it is accepted AFTER the subcommand (the natural util
    # call: `gu remote exec loc --command … --json`). The subparser dest is `op`, NOT `command`
    # — `exec`/`submit` own `--command`, and a `command` dest would collide, silently clobbering
    # the chosen subcommand with the --command value.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="structured JSON on stdout")
    p = argparse.ArgumentParser(prog="gu remote", description="Act on a bound remote machine (SSH).")
    p.add_argument("--selftest", action="store_true")
    sub = p.add_subparsers(dest="op")

    def leaf(name: str, **kw):
        sp = sub.add_parser(name, parents=[common], **kw)
        if name not in ("list", "scan-host"):
            sp.add_argument("machine", help="a machine name (see `gu remote list`)")
        return sp

    leaf("list", help="machines this routine can reach")
    sp = leaf("exec", help="run a command and wait")
    sp.add_argument("--command", required=True)
    sp.add_argument("--timeout", type=int, default=default_exec_timeout(),
                    help="SSH I/O deadline in seconds (defaults to this call's own action "
                         "timeout_s, less a margin, so the timeout is REPORTED with its "
                         "partial output instead of the util being killed)")
    sp.add_argument("--cwd", default="")
    sp = leaf("submit", help="start a detached job")
    sp.add_argument("--command", required=True); sp.add_argument("--cwd", default="")
    sp.add_argument("--notify-webhook", default="", dest="webhook")
    sp.add_argument("--deadline-hours", type=float, default=DEFAULT_DEADLINE_H, dest="deadline_h",
                    help="wall-clock allowance on an exclusive machine (default 6): past it the "
                         "job is killed and its queue ticket dropped")
    sp.add_argument("--est-minutes", type=int, default=0, dest="est_min",
                    help="how long you expect the job to take - shown to whoever is waiting")
    sp = leaf("queue", help="an exclusive machine's job queue")
    sp.add_argument("--cancel", default="", dest="cancel_job", metavar="JOBID",
                    help="drop a ticket (terminating its job if it is already running)")
    for name in ("status", "logs", "cancel"):
        sp = leaf(name); sp.add_argument("--job", required=True)
        if name == "logs":
            sp.add_argument("--tail", type=int, default=8000,
                            help="max BYTES per stream (default 8000)")
            sp.add_argument("--tail-lines", type=int, default=0, dest="tail_lines",
                            help="last N LINES per stream instead of bytes")
    for name in ("push", "pull"):
        sp = leaf(name, help="upload/download a file (SFTP)")
        sp.add_argument("--src", required=True); sp.add_argument("--dest", required=True)
    sp = leaf("scan-host", help="read a host's public key (for pinning)")
    sp.add_argument("host"); sp.add_argument("--port", type=int, default=22)
    leaf("test", help="connect + run true")

    args = p.parse_args()
    if args.selftest:
        return selftest()
    if not args.op:
        p.error("a command is required (list | exec | submit | status | logs | cancel | "
                "queue | push | pull | scan-host | test)")

    machines, keys = load_machines()
    exit_code = 0
    try:
        if args.op == "scan-host":
            payload = cmd_scan_host(args.host, args.port)
        elif args.op == "list":
            payload = cmd_list(machines)
        else:
            m = _pick(machines, args.machine)
            if args.op == "exec":
                payload, exit_code = cmd_exec(m, keys, args.command, args.timeout, args.cwd)
                if payload.get("timed_out"):
                    print(payload["error"], file=sys.stderr)
            elif args.op == "submit":
                payload = cmd_submit(m, keys, args.command, args.cwd, args.webhook,
                                     deadline_h=args.deadline_h, est_min=args.est_min)
            elif args.op == "queue":
                payload = cmd_queue(m, keys, args.cancel_job)
            elif args.op == "status":
                payload = cmd_status(m, keys, args.job)
            elif args.op == "logs":
                payload = cmd_logs(m, keys, args.job, args.tail,
                                   getattr(args, "tail_lines", 0))
            elif args.op == "cancel":
                payload = cmd_cancel(m, keys, args.job)
                if payload.get("ok") is False:
                    # R1618: a control verb that could not achieve what it was asked to
                    # do must FAIL, not return 0 with a quiet field nobody reads.
                    print(payload["error"], file=sys.stderr)
                    exit_code = 1
            elif args.op == "push":
                payload = cmd_push(m, keys, args.src, args.dest)
            elif args.op == "pull":
                payload = cmd_pull(m, keys, args.src, args.dest)
            else:                                    # test (argparse-gated to the leaves above)
                payload = cmd_test(m, keys)
    except RemoteError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:                          # paramiko/socket surprises → clean exit 1
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    _emit(payload, args.json)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
