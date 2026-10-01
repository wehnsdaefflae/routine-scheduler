"""ONE compose selection per host, in `.env` — and `deploy/nat64.sh`, which writes its file half.

Which files make up the project and which profiles are on belong to the HOST, so they live where
Compose looks on every command run from the checkout's root: COMPOSE_FILE and COMPOSE_PROFILES in
`.env`. A selection handed to one command reached that command and no other. It failed in both
directions:

- `nat64.sh on` gave its own `up` a file list naming docker-compose.yml and compose.nat64.yml. A
  list replaces Compose's default — and with it the auto-loading of the gitignored
  docker-compose.override.yml — so from 2026-09-27 until the 0.372.1 deploy every container ran
  with no memory limit — the condition that override exists to prevent.
- DOCKER.md's plain rebuild named no list at all, so on a host with NAT64 on it would have
  recreated rsched and chrome without the DNS64 resolvers; 0.372.1 had to be deployed with all
  three files and the profile typed out by hand.

The script is driven for real, against a copy of the checkout's compose files, through a `docker`
stub that resolves the selection the way Compose does: a file flag replaces COMPOSE_FILE, which
replaces the default discovery (and the override's auto-load with it); a profile flag replaces
COMPOSE_PROFILES; the shell's environment beats `.env`. That is what Compose v5.5.1 did on the
server on 2026-10-01 — a Compose that resolves differently moves this stub with it.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEPLOY = REPO / "deploy"
DEFAULT = ["docker-compose.yml", "docker-compose.override.yml"]
NAT64 = [*DEFAULT, "compose.nat64.yml"]
#: The host's memory ceilings, in the shape of the real (untracked) override.
OVERRIDE = """services:
  rsched: {mem_limit: 5g, memswap_limit: 6g}
  chrome: {mem_limit: 2g, memswap_limit: 2g}
  cliproxy: {mem_limit: 384m, memswap_limit: 384m}
  tor: {mem_limit: 256m, memswap_limit: 256m}
"""
ENV = "# Compose environment. Not tracked — see .gitignore.\nBROWSER_CDP_TOKEN=t0k3n\n"
#: The containers this host runs, as `docker compose ps` and `docker inspect` report them.
FLEET = {"rsched": {"service": "rsched", "dns": "[2a00:1098:2b::1]", "mem": 5368709120},
         "rsched-chrome": {"service": "chrome", "dns": "[2a00:1098:2b::1]", "mem": 2147483648},
         "rsched-tor": {"service": "tor", "dns": "(default)", "mem": 268435456},
         "routine-scheduler-cliproxy-1": {"service": "cliproxy", "dns": "(default)",
                                          "mem": 402653184}}

#: `docker`, resolving the compose selection the way Compose does and logging it per command.
STUB = """#!@PYTHON@
import hashlib, json, os, socket, sys
from pathlib import Path

import yaml

args = sys.argv[1:]
fleet = json.loads(os.environ["STUB_FLEET"])
if args[0] == "inspect":
    c = fleet[args[1]]
    print("|".join([c["service"], c["dns"], str(c["mem"]), c.get("hash", "")]))
    sys.exit(0)
if args[0] == "info":
    # What deploy/docker-host-guard.sh compares against `hostname`: the daemon's own host name.
    # The stub daemon IS this host's, so the guard lets the script through; STUB_DAEMON lets a
    # test pose as a daemon somewhere else.
    print(os.environ.get("STUB_DAEMON") or socket.gethostname())
    sys.exit(0)
assert args.pop(0) == "compose", sys.argv
files, profiles = [], []
while args and args[0].startswith("-"):
    flag, _, value = args.pop(0).partition("=")
    if flag in ("-f", "--file", "--profile"):
        (profiles if flag == "--profile" else files).append(value or args.pop(0))
dotenv = {}
if Path(".env").is_file():
    for line in Path(".env").read_text(encoding="utf-8").splitlines():
        key, eq, value = line.partition("=")
        if eq and not key.startswith("#"):
            dotenv[key.strip()] = value.strip()
env = {**dotenv, **os.environ}
if not files:
    files = (env["COMPOSE_FILE"].split(":") if "COMPOSE_FILE" in env else
             ["docker-compose.yml"] + (["docker-compose.override.yml"]
                                       if Path("docker-compose.override.yml").is_file() else []))
if not profiles:
    profiles = [p.strip() for p in env.get("COMPOSE_PROFILES", "").split(",") if p.strip()]
services = {}
for name in files:
    if not Path(name).is_file():
        sys.exit(f"open {name}: no such file or directory")
    spec = yaml.safe_load(Path(name).read_text(encoding="utf-8")) or {}
    for svc, conf in (spec.get("services") or {}).items():
        services.setdefault(svc, {}).update(conf or {})
active = {s: c for s, c in services.items()
          if not c.get("profiles") or set(c["profiles"]) & set(profiles)}
with Path(os.environ["STUB_LOG"]).open("a", encoding="utf-8") as log:
    log.write(json.dumps({"argv": sys.argv[1:], "files": files, "profiles": profiles}) + "\\n")
if args[:1] == ["config"] and "--services" in args:
    print("\\n".join(active))
elif args[:1] == ["config"] and "--hash" in args:
    for svc, conf in active.items():
        print(svc, hashlib.sha256(json.dumps(conf, sort_keys=True).encode()).hexdigest())
elif args[:1] == ["ps"]:
    fmt = args[args.index("--format") + 1]
    for name, c in fleet.items():
        print(fmt.replace("{{.Name}}", name).replace("{{.Service}}", c["service"]))
"""


def _checkout(tmp_path: Path, *, override: bool = True, profiles: str | None = "claude-proxy",
              ) -> Path:
    """The checkout's root as nat64.sh sees it: the real script and compose files, a host
    override when asked for, and a 0600 `.env` carrying the token (and the profile line)."""
    root = tmp_path / "checkout"
    (root / "deploy").mkdir(parents=True)
    shutil.copy2(DEPLOY / "nat64.sh", root / "deploy" / "nat64.sh")
    # nat64.sh refuses before it writes `.env` or reports a fleet when the Docker daemon is not
    # on this host (deploy/docker-host-guard.sh). The guard is part of the script, so a checkout
    # without it is not one the script can run in; the stub `docker` below answers its
    # `info --format {{.Name}}` with this host's own name, which is what the guard compares.
    shutil.copy2(DEPLOY / "docker-host-guard.sh", root / "deploy" / "docker-host-guard.sh")
    for name in ("docker-compose.yml", "compose.nat64.yml"):
        shutil.copy2(REPO / name, root / name)
    if override:
        (root / "docker-compose.override.yml").write_text(OVERRIDE, encoding="utf-8")
    dotenv = root / ".env"
    dotenv.write_text(ENV + (f"COMPOSE_PROFILES={profiles}\n" if profiles else ""),
                      encoding="utf-8")
    dotenv.chmod(0o600)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "docker").write_text(STUB.replace("@PYTHON@", sys.executable), encoding="utf-8")
    (bin_dir / "docker").chmod(0o755)
    return root


def _env(root: Path, fleet: dict, extra: dict | None = None) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("COMPOSE_")}
    return {**env, "PATH": f"{root.parent / 'bin'}{os.pathsep}{env['PATH']}",
            "STUB_LOG": str(root.parent / "docker.log"), "STUB_FLEET": json.dumps(fleet),
            **(extra or {})}


def _calls(root: Path) -> list[dict]:
    """Every docker compose command run since the last look, with the selection it resolved."""
    log = root.parent / "docker.log"
    if not log.exists():
        return []
    calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    log.unlink()
    return calls


def _nat64(root: Path, *args: str, fleet: dict = FLEET, extra: dict | None = None,
           ) -> tuple[subprocess.CompletedProcess, list[dict]]:
    proc = subprocess.run(["bash", str(root / "deploy" / "nat64.sh"), *args],
                          env=_env(root, fleet, extra), capture_output=True, text=True,
                          timeout=60, check=False)
    return proc, _calls(root)


def _compose(root: Path, *args: str, extra: dict | None = None) -> subprocess.CompletedProcess:
    """A compose command typed bare in the checkout's root, as DOCKER.md gives them."""
    return subprocess.run(["docker", "compose", *args], cwd=root, env=_env(root, FLEET, extra),
                          capture_output=True, text=True, timeout=60, check=True)


def _selection(calls: list[dict]) -> set[tuple[tuple[str, ...], tuple[str, ...]]]:
    return {(tuple(c["files"]), tuple(c["profiles"])) for c in calls}


def test_on_gives_every_command_the_override_the_nat64_file_and_the_profile(tmp_path):
    """THE REGRESSION: every compose command nat64.sh runs — and every one typed bare after it,
    a rebuild included — reads the host override, the NAT64 file and the profile."""
    root = _checkout(tmp_path)
    proc, calls = _nat64(root, "on")
    assert proc.returncode == 0, proc.stderr
    assert _selection(calls) == {(tuple(NAT64), ("claude-proxy",))}, calls
    assert calls[-1]["argv"] == ["compose", "up", "-d"], calls
    for later in (["build"], ["up", "-d"], ["exec", "-u", "mark", "rsched", "node", "--version"]):
        _compose(root, *later)
        assert _selection(_calls(root)) == {(tuple(NAT64), ("claude-proxy",))}, later


def test_on_adds_its_line_and_leaves_the_rest_of_env_as_it_was(tmp_path):
    """`.env` is a credential file: the token line, the comments and the mode survive, no copy
    is left beside it, and a second `on` changes nothing."""
    root = _checkout(tmp_path)
    before = (root / ".env").read_text(encoding="utf-8")
    assert _nat64(root, "on")[0].returncode == 0
    after = (root / ".env").read_text(encoding="utf-8")
    assert after.startswith(before), after
    assert f"COMPOSE_FILE={':'.join(NAT64)}\n" in after, after
    assert stat.S_IMODE((root / ".env").stat().st_mode) == 0o600
    assert not list(root.glob(".env.*")), "the temporary copy carries the token too"
    assert _nat64(root, "on")[0].returncode == 0
    assert (root / ".env").read_text(encoding="utf-8") == after


def test_off_hands_the_file_list_back_to_composes_default(tmp_path):
    """`off` restores `.env` byte for byte, so Compose discovers its files itself again — and the
    override with them, whether or not it existed when NAT64 went on."""
    root = _checkout(tmp_path)
    before = (root / ".env").read_bytes()
    assert _nat64(root, "on")[0].returncode == 0
    proc, calls = _nat64(root, "off")
    assert proc.returncode == 0, proc.stderr
    assert (root / ".env").read_bytes() == before
    assert _selection(calls) == {(tuple(DEFAULT), ("claude-proxy",))}, calls
    assert calls[-1]["argv"] == ["compose", "up", "-d"], calls


def test_a_host_without_an_override_gets_a_list_without_one(tmp_path):
    """Compose fails on a listed file that does not exist, so the list names only what is there."""
    root = _checkout(tmp_path, override=False)
    proc, calls = _nat64(root, "on")
    assert proc.returncode == 0, proc.stderr
    assert _selection(calls) == {(("docker-compose.yml", "compose.nat64.yml"), ("claude-proxy",))}


def test_a_selection_that_leaves_a_running_service_out_changes_nothing(tmp_path):
    """An `up` that leaves out a service with a container here cannot settle the project's
    networks and strands it — cliproxy, 2026-09-27. The script used to hard-code that one profile;
    it now refuses, leaving `.env` untouched, until `.env` names whatever profile a running service
    needs. A host that runs no proxy needs no profile at all."""
    root = _checkout(tmp_path, profiles=None)
    before = (root / ".env").read_bytes()
    proc, calls = _nat64(root, "on")
    assert proc.returncode == 3, proc.stdout + proc.stderr
    assert "cliproxy" in proc.stderr and "COMPOSE_PROFILES" in proc.stderr, proc.stderr
    assert "NAT64 on" not in proc.stdout, "announced a switch it then refused"
    assert not [c for c in calls if c["argv"][1:2] == ["up"]], calls
    assert (root / ".env").read_bytes() == before
    no_proxy = {name: c for name, c in FLEET.items() if c["service"] != "cliproxy"}
    proc, calls = _nat64(root, "on", fleet=no_proxy)
    assert proc.returncode == 0, proc.stderr
    assert _selection(calls) == {(tuple(NAT64), ())}, calls


def test_a_selection_exported_in_the_shell_is_refused(tmp_path):
    """The shell beats `.env`, so with either variable exported the line nat64.sh writes would
    govern nothing — not even its own `up`."""
    root = _checkout(tmp_path)
    for var in ("COMPOSE_FILE", "COMPOSE_PROFILES"):
        proc, calls = _nat64(root, "on", extra={var: "docker-compose.yml"})
        assert proc.returncode == 2 and var in proc.stderr, proc.stderr
        assert not calls


def test_status_shows_the_selection_and_which_containers_it_would_recreate(tmp_path):
    """What 2026-09-27 left behind, as status reads it: containers created from the NAT64 file
    without the override run with the resolvers and without a memory ceiling. Their config is not
    the one the selection gives them."""
    root = _checkout(tmp_path)
    assert _nat64(root, "on")[0].returncode == 0
    hashes = {}
    for files, names in ((["docker-compose.yml", "compose.nat64.yml"], ("rsched",)),
                         (NAT64, ("rsched-chrome", "rsched-tor"))):
        out = _compose(root, "config", "--hash", "*",
                       extra={"COMPOSE_FILE": ":".join(files)}).stdout
        by_service = {}
        for line in out.splitlines():
            service, digest = line.split()
            by_service[service] = digest
        hashes.update({name: by_service[FLEET[name]["service"]] for name in names})
    _calls(root)
    fleet = {name: {**c, "hash": hashes.get(name, "")} for name, c in FLEET.items()}
    fleet["rsched"]["mem"] = 0
    proc, _ = _nat64(root, "status", fleet=fleet)
    assert proc.returncode == 0, proc.stderr
    lines = {line.split()[0]: line for line in proc.stdout.splitlines()}
    assert ":".join(NAT64) in lines["COMPOSE_FILE"] and "NAT64 on" in lines["COMPOSE_FILE"]
    assert "claude-proxy" in lines["COMPOSE_PROFILES"]
    assert "mem=none" in lines["rsched"] and "config differs" in lines["rsched"], lines
    assert "mem=2.0G" in lines["rsched-chrome"] and "matches" in lines["rsched-chrome"], lines
    assert "dns=[2a00:1098:2b::1]" in lines["rsched-chrome"], lines
    assert "matches" in lines["rsched-tor"], lines
    assert "config differs" in lines["routine-scheduler-cliproxy-1"], lines


#: Global flags that select files or profiles for ONE command — what `.env` holds for all of them.
SELECTING = ("-f", "--file", "--profile")
#: Global flags that take a value, so the walk to the subcommand steps over it.
VALUED = (*SELECTING, "-p", "--project-name", "--project-directory", "--env-file", "--ansi",
          "--progress", "--parallel")
COMPOSE_CMD = re.compile(r"\bdocker[ -]compose(?=\s)")


def _bypasses(line: str) -> list[str]:
    """The ways a line hands one compose command a selection of its own."""
    found = []
    for match in COMPOSE_CMD.finditer(line):
        if re.search(r"\bCOMPOSE_(?:FILE|PROFILES)=\S*\s+(?:\w+=\S*\s+)*$", line[:match.start()]):
            found.append("COMPOSE_FILE/COMPOSE_PROFILES set for one command")
        words = [w.strip("`'\"()") for w in line[match.end():].split()]
        while words and words[0].startswith("-"):
            flag, eq, _ = words.pop(0).partition("=")
            if flag in SELECTING:
                found.append(flag)
            if flag in VALUED and not eq and words:
                words.pop(0)
    return found


def test_no_tracked_file_hands_compose_a_selection_of_its_own():
    """Docs, comments, scripts and test hints give compose commands bare, so each one a person or
    a session copies reads the host's `.env`. CHANGELOG.md is history: it quotes what it retired."""
    cmd = "docker compose"   # a quote follows it here, so this line is no command to the scan
    assert _bypasses(f"{cmd} -f a.yml -f b.yml up -d") == ["-f", "-f"]
    assert _bypasses(f"`{cmd} --profile=claude-proxy exec cliproxy`") == ["--profile"]
    assert _bypasses(f"COMPOSE_FILE=a.yml {cmd} up -d") == [
        "COMPOSE_FILE/COMPOSE_PROFILES set for one command"]
    assert not _bypasses(f"RSCHED_PORT=8322 {cmd} up -d; {cmd} logs -f tor; {cmd} exec -u mark rsched")
    assert not _bypasses(f"`.env` holds COMPOSE_FILE=a.yml; `{cmd} up -d` reads it")
    tracked = subprocess.run(["git", "ls-files", "-z"], cwd=REPO, capture_output=True,
                             check=True).stdout.decode().split("\0")
    found = []
    for rel in filter(None, tracked):
        if rel == "CHANGELOG.md":
            continue
        try:
            text = (REPO / rel).read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
            continue
        found += [f"{rel}:{n}: {why}" for n, line in enumerate(text.splitlines(), 1)
                  for why in _bypasses(line)]
    assert not found, ("a compose command with a selection of its own — run it bare from the "
                       "checkout's root, where .env names the files and profiles "
                       "(deploy/DOCKER.md, 'One compose selection per host'):\n"
                       + "\n".join(found))
