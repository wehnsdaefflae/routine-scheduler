# /// script
# dependencies = ["paramiko>=3.4"]
# ///
"""remote — act on a bound remote machine over SSH (reserved: needs the remote-machines permission).

usage: gu remote <command> [args] [--json]
calls: (none)
tags: ssh, remote, machines, gpu, execute
secrets: RSCHED_MACHINES, RSCHED_MACHINE_KEYS
net: outbound

Runs commands and moves files on the SSH hosts the routine is BOUND to (Settings → Machines,
then bind on the routine page). The engine injects the bound machines' connection details
(RSCHED_MACHINES) and private keys (RSCHED_MACHINE_KEYS) — you never handle credentials; a
machine you are not bound to is invisible. Host keys are PINNED: a mismatch (or an unscanned
machine) refuses to connect. Commands:

  list                              the machines this routine can reach
  exec MACHINE --command CMD        run CMD, wait, return stdout/stderr/exit (short jobs)
  submit MACHINE --command CMD      start a DETACHED job (survives this call) → a job id
  status MACHINE --job ID           running | exit=<code> | nojob
  logs MACHINE --job ID [--tail N]  the job's stdout + stderr so far
  cancel MACHINE --job ID           terminate the job's process group
  push MACHINE --src L --dest R      upload a local file over SFTP
  pull MACHINE --src R --dest L      download a remote file over SFTP
  scan-host HOST [--port N]         read a host's public key line (for pinning in Settings)
  test MACHINE                      connect + run `true`, report reachability

Long GPU jobs: `submit` then poll `status`, or pass `--notify-webhook <url>` and let the job
POST the routine's own trigger URL on completion (no polling). --selftest runs offline."""

import argparse
import base64
import io
import json
import os
import re
import shlex
import sys

_JOBID_RE = re.compile(r"[A-Za-z0-9_.-]+")

CAP = 64_000               # per-stream output cap (head+tail), matching the shell util
JOBS_DIR = ".rsched-jobs"  # per-machine job root, under the machine's workdir (else $HOME)


class RemoteError(Exception):
    """A clean, user-facing failure (bad binding, unreachable host, key mismatch)."""


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
    lines = ["(", inner, ") > stdout 2> stderr < /dev/null", "code=$?", "echo $code > exit"]
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


def _run(client, command: str, timeout: int, cwd: str = "") -> tuple[int, str, str]:
    if cwd:
        command = f"cd {shlex.quote(cwd)} && {command}"
    _stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
    out = stdout.read().decode("utf-8", "replace")
    err = stderr.read().decode("utf-8", "replace")
    return stdout.channel.recv_exit_status(), out, err


# ------------------------------------------------------------------------------ commands -----
def cmd_list(machines: dict[str, dict]) -> dict:
    return {"command": "list", "machines": [
        {"name": n, "host": m.get("host"), "user": m.get("user"), "port": m.get("port"),
         "description": m.get("description", ""), "tags": m.get("tags", []),
         "ready": bool(m.get("has_key") and m.get("has_host_key"))}
        for n, m in sorted(machines.items())]}


def cmd_exec(m: dict, keys: dict, command: str, timeout: int, cwd: str) -> tuple[dict, int]:
    client = connect(m, keys)
    try:
        code, out, err = _run(client, command, timeout, cwd)
    finally:
        client.close()
    out_c, t1 = _capped(out)
    err_c, t2 = _capped(err)
    return ({"command": "exec", "machine": m["name"], "exit": code, "stdout": out_c,
             "stderr": err_c, "truncated": t1 or t2}, code)


def cmd_submit(m: dict, keys: dict, command: str, cwd: str, webhook: str) -> dict:
    import uuid

    jobid = uuid.uuid4().hex[:16]
    script_b64 = base64.b64encode(build_job_script(command, jobid, cwd, webhook).encode()).decode()
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
    snippet = _job_cmd(m, jobid,
                       'if [ -f "$JOBDIR/exit" ]; then echo "exit=$(cat "$JOBDIR/exit")"; '
                       'elif [ -f "$JOBDIR/pgid" ] && kill -0 -"$(cat "$JOBDIR/pgid")" '
                       '2>/dev/null; then echo running; '
                       'elif [ -d "$JOBDIR" ]; then echo started; else echo nojob; fi')
    client = connect(m, keys)
    try:
        _code, out, _err = _run(client, snippet, timeout=30)
    finally:
        client.close()
    state = out.strip()
    exit_code = int(state.split("=", 1)[1]) if state.startswith("exit=") else None
    return {"command": "status", "machine": m["name"], "job": jobid,
            "state": "done" if exit_code is not None else state, "exit": exit_code}


def cmd_logs(m: dict, keys: dict, jobid: str, tail: int) -> dict:
    snippet = _job_cmd(m, jobid,
                       f'echo "===STDOUT==="; tail -c {tail} "$JOBDIR/stdout" 2>/dev/null; '
                       f'echo; echo "===STDERR==="; tail -c {tail} "$JOBDIR/stderr" 2>/dev/null')
    client = connect(m, keys)
    try:
        _code, out, _err = _run(client, snippet, timeout=30)
    finally:
        client.close()
    stdout, _, rest = out.partition("===STDERR===")
    return {"command": "logs", "machine": m["name"], "job": jobid,
            "stdout": stdout.replace("===STDOUT===", "", 1).strip(), "stderr": rest.strip()}


def cmd_cancel(m: dict, keys: dict, jobid: str) -> dict:
    snippet = _job_cmd(m, jobid,
                       'PG="$(cat "$JOBDIR/pgid" 2>/dev/null)"; '
                       '{ [ -n "$PG" ] && kill -TERM -"$PG" 2>/dev/null && echo cancelled; } '
                       '|| echo "not running"')
    client = connect(m, keys)
    try:
        _code, out, _err = _run(client, snippet, timeout=30)
    finally:
        client.close()
    return {"command": "cancel", "machine": m["name"], "job": jobid, "result": out.strip()}


def _strip_home(path: str) -> str:
    """SFTP has no shell ~ expansion; a leading ~/ maps to the SFTP default dir (the home)."""
    return path[2:] if path.startswith("~/") else path


def cmd_push(m: dict, keys: dict, src: str, dest: str) -> dict:
    if not os.path.isfile(src):
        raise RemoteError(f"local file not found: {src}")
    client = connect(m, keys)
    try:
        sftp = client.open_sftp()
        sftp.put(src, _strip_home(dest))
        size = os.path.getsize(src)
    finally:
        client.close()
    return {"command": "push", "machine": m["name"], "src": src, "dest": dest, "bytes": size}


def cmd_pull(m: dict, keys: dict, src: str, dest: str) -> dict:
    client = connect(m, keys)
    try:
        sftp = client.open_sftp()
        if os.path.isdir(dest):
            dest = os.path.join(dest, os.path.basename(src))
        sftp.get(_strip_home(src), dest)
        size = os.path.getsize(dest)
    finally:
        client.close()
    return {"command": "pull", "machine": m["name"], "src": src, "dest": dest, "bytes": size}


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
    assert s.lstrip().startswith("("), "job body is a subshell so a user `exit N` is captured"
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
    print("selftest: ok", file=sys.stderr)
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
    sp.add_argument("--command", required=True); sp.add_argument("--timeout", type=int, default=120)
    sp.add_argument("--cwd", default="")
    sp = leaf("submit", help="start a detached job")
    sp.add_argument("--command", required=True); sp.add_argument("--cwd", default="")
    sp.add_argument("--notify-webhook", default="", dest="webhook")
    for name in ("status", "logs", "cancel"):
        sp = leaf(name); sp.add_argument("--job", required=True)
        if name == "logs":
            sp.add_argument("--tail", type=int, default=8000, help="max bytes per stream")
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
                "push | pull | scan-host | test)")

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
            elif args.op == "submit":
                payload = cmd_submit(m, keys, args.command, args.cwd, args.webhook)
            elif args.op == "status":
                payload = cmd_status(m, keys, args.job)
            elif args.op == "logs":
                payload = cmd_logs(m, keys, args.job, args.tail)
            elif args.op == "cancel":
                payload = cmd_cancel(m, keys, args.job)
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
