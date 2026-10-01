"""The deploy STATE inventory, read against the compose file and run through its consumers.

`deploy/state-paths.sh` is the one list of what lives outside the engine image. `bundle.sh`
(the one-shot migration tarball) and `backup.sh` (the recurring mirror) both source it so the
two cannot drift, and the compose file's binds are what it has to cover. None of it is Python,
which is why it is tested here: each of these drifted once in a way only a migration, a disk
failure or a fresh host would have shown — a data home mounted but never carried, a mirror
that ignored the excludes the tarball honoured, an optional credential store required.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
DEPLOY = REPO / "deploy"
COMPOSE = REPO / "docker-compose.yml"
#: How the compose file spells the host's data root; every data bind is relative to it.
HOME_VAR = "${RSCHED_HOME:-/home/mark}"
LISTS = ("STATE_PATHS_REQUIRED", "STATE_PATHS_OPTIONAL", "STATE_PATHS_NOT_CARRIED",
         "STATE_EXCLUDES")


def _lists() -> dict[str, list[str]]:
    """The inventory's arrays, as bash itself reads them (comments and quoting included)."""
    script = ('source "$1"; for name in ' + " ".join(LISTS) + '; do declare -n list="$name"; '
              'for item in "${list[@]}"; do printf "%s\\t%s\\n" "$name" "$item"; done; '
              "unset -n list; done")
    out = subprocess.run(["bash", "-c", script, "bash", str(DEPLOY / "state-paths.sh")],
                         capture_output=True, text=True, check=True).stdout
    found: dict[str, list[str]] = {name: [] for name in LISTS}
    for line in out.splitlines():
        name, _, item = line.partition("\t")
        found[name].append(item)
    return found


def _binds() -> list[dict]:
    """Every bind mount of every compose service: its HOME-relative source (None when the
    source is not under RSCHED_HOME), target, whether it is read-only, and whether Docker
    creates a missing source (the short syntax always does)."""
    binds = []
    for service, spec in yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"].items():
        for vol in spec.get("volumes", []):
            if isinstance(vol, str):
                # the variable carries a ':' of its own, so it goes before the split
                src, dst, *mode = vol.replace(HOME_VAR, "@HOME@").split(":")
                src, read_only, creates = src.replace("@HOME@", HOME_VAR), "ro" in mode, True
            else:
                src, dst, read_only = vol["source"], vol["target"], vol.get("read_only", False)
                creates = vol.get("bind", {}).get("create_host_path", True)
            if not src.startswith(("/", HOME_VAR)):
                continue                                     # a named volume
            rel = src.removeprefix(HOME_VAR + "/") if src.startswith(HOME_VAR) else None
            binds.append({"service": service, "home_rel": rel, "target": dst,
                          "read_only": read_only, "creates": creates})
    return binds


def _covered(rel: str, roots: list[str]) -> bool:
    return any(rel == root or rel.startswith(root + "/") for root in roots)


def _entrypoint_dirs() -> list[str]:
    """The directories docker-entrypoint.sh hands to the runtime user before dropping root."""
    text = (DEPLOY / "docker-entrypoint.sh").read_text(encoding="utf-8")
    loop = re.search(r"^for d in (.*?); do$", text, re.MULTILINE | re.DOTALL)
    assert loop, "docker-entrypoint.sh no longer has its `for d in …; do` ownership loop"
    return re.findall(r'"([^"]+)"', loop.group(1))


def test_the_entrypoint_hands_every_writable_bind_to_the_runtime_user():
    """Docker creates a missing bind source root-owned, so a home the engine writes is unusable
    to uid 1000 until the entrypoint chowns it. Its list was a hand copy of the compose binds
    and missed every home added after it — conversations, background tasks, the messenger
    session stores, the `claude /login` store — each dead on arrival on a fresh host."""
    owned = set(_entrypoint_dirs())
    missing = [b["target"] for b in _binds()
               if b["service"] == "rsched" and b["target"].startswith("/home/mark/")
               and not b["read_only"] and b["creates"] and b["target"] not in owned]
    assert not missing, f"bind targets the entrypoint leaves root-owned when Docker creates them: {missing}"


def test_every_data_bind_is_in_the_inventory():
    """THE INVARIANT state-paths.sh states: every bind under RSCHED_HOME is carried, or
    declared not carried. A bind nobody listed dies on the next migration."""
    lists = _lists()
    listed = [*lists["STATE_PATHS_REQUIRED"], *lists["STATE_PATHS_OPTIONAL"],
              *lists["STATE_PATHS_NOT_CARRIED"]]
    unlisted = [f"{b['service']}: {b['home_rel']}" for b in _binds()
                if b["home_rel"] is not None and not _covered(b["home_rel"], listed)]
    assert not unlisted, f"mounted but neither carried nor declared not carried: {unlisted}"


def test_every_inventory_entry_is_a_mounted_home():
    """The reverse: an entry whose bind was dropped from the compose file is carried for
    nothing — and reads as a home the instance still has."""
    mounted = {b["home_rel"] for b in _binds() if b["home_rel"] is not None}
    lists = _lists()
    stale = [p for name in LISTS[:3] for p in lists[name] if p not in mounted]
    assert not stale, f"listed but no compose service mounts it: {stale}"


def _home(root: Path) -> Path:
    """A minimal instance: every required home, one optional one, and one file of each kind
    STATE_EXCLUDES drops — at the depth and in the home it is meant for."""
    home = root / "home"
    for rel in _lists()["STATE_PATHS_REQUIRED"]:
        (home / rel).mkdir(parents=True)
        (home / rel / "data.txt").write_text(rel, encoding="utf-8")
    files = {
        ".credentials/openrouter.env": "KEY=x",
        "git-repos/LLMSecTest_agentic/src/keep.py": "",
        "git-repos/LLMSecTest_agentic/secrets/token": "",
        "git-repos/LLMSecTest_agentic/apps/member/compose.yml": "",   # anchored exclude
        "git-repos/LLMSecTest_agentic/venv/lib/site.py": "",          # anchored exclude
        "routines/r1/.venv/bin/python": "",                           # bare-name excludes
        "routines/r1/__pycache__/m.cpython-312.pyc": "",
        "routines/r1/scripts/tool.pyc": "",
        "routines/r1/apps/kept.txt": "",       # `apps` is excluded in the workspace only
    }
    for rel, text in files.items():
        (home / rel).parent.mkdir(parents=True, exist_ok=True)
        (home / rel).write_text(text, encoding="utf-8")
    # Chrome's lock is a DANGLING symlink naming the host and pid that hold the profile
    (home / "chrome-profile/Default").mkdir(parents=True)
    (home / "chrome-profile/Default/Cookies").write_text("", encoding="utf-8")
    (home / "chrome-profile/SingletonLock").symlink_to("buildhost-4242")
    return home


EXCLUDED = {"git-repos/LLMSecTest_agentic/apps/member/compose.yml",
            "git-repos/LLMSecTest_agentic/venv/lib/site.py",
            "routines/r1/.venv/bin/python",
            "routines/r1/__pycache__/m.cpython-312.pyc",
            "routines/r1/scripts/tool.pyc",
            "chrome-profile/SingletonLock"}


def _env(home: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("RSCHED_")}
    return {**env, "HOME": str(home)}


def _bundle(home: Path, out: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(DEPLOY / "bundle.sh"), str(out)], env=_env(home),
                          capture_output=True, text=True, timeout=120, check=False)


def _tar_files(archive: Path) -> set[str]:
    """Every file and symlink the archive holds (directories are implied by their contents)."""
    with tarfile.open(archive) as tar:
        return {m.name for m in tar.getmembers() if m.isfile() or m.issym()}


def test_an_install_without_key_files_bundles(tmp_path):
    """`~/.credentials` is the optional last rung of the credential ladder: install.sh never
    creates it and the Secrets store replaced it, so a host that never used it is complete —
    the bundle carries what it has and says what it skipped, instead of refusing."""
    home = _home(tmp_path)
    shutil.rmtree(home / ".credentials")
    out = tmp_path / "bundle.tgz"
    proc = _bundle(home, out)
    assert proc.returncode == 0, proc.stderr
    assert "skipping .credentials" in proc.stderr
    assert "git-repos/routine-scheduler/data.txt" in _tar_files(out)


@pytest.mark.skipif(shutil.which("rsync") is None, reason="backup.sh mirrors with rsync")
def test_the_tarball_and_the_mirror_carry_the_same_files(tmp_path):
    """bundle.sh and backup.sh read ONE exclude list, so they must carry exactly the same
    files. The mirror used to sync each home from inside it, where rsync never sees the
    `git-repos/LLMSecTest_agentic/` prefix an anchored exclude names — so it copied the
    workspace bulk the tarball leaves out. backup.sh refuses a mirror on its home's device
    (the unmounted-share guard), so the mirror lives on a different filesystem here."""
    shm = Path("/dev/shm")  # noqa: S108 — a second FILESYSTEM is the point; mkdtemp makes the dir
    if not shm.is_dir() or not os.access(shm, os.W_OK) \
            or shm.stat().st_dev == tmp_path.stat().st_dev:
        pytest.skip("needs a writable filesystem apart from tmp_path for the mirror")
    home = _home(tmp_path)
    out = tmp_path / "bundle.tgz"
    proc = _bundle(home, out)
    assert proc.returncode == 0, proc.stderr
    tarred = _tar_files(out)

    share = Path(tempfile.mkdtemp(prefix="rsched-backup-test-", dir=shm))
    try:
        mirror = share / "mirror"
        stale = mirror / "git-repos/LLMSecTest_agentic/apps/stale.txt"
        stale.parent.mkdir(parents=True)
        stale.write_text("copied before the exclude existed", encoding="utf-8")
        proc = subprocess.run(["bash", str(DEPLOY / "backup.sh"), str(mirror)], env=_env(home),
                              capture_output=True, text=True, timeout=120, check=False)
        if "another backup is already running" in proc.stdout:
            pytest.skip("a real backup holds the mirror lock")
        assert proc.returncode == 0, proc.stdout + proc.stderr
        mirrored = {str(p.relative_to(mirror)) for p in mirror.rglob("*")
                    if (p.is_symlink() or p.is_file()) and p.name != ".rsched-backup-completed"}
    finally:
        shutil.rmtree(share, ignore_errors=True)

    assert not EXCLUDED & tarred, f"the tarball carried excluded files: {EXCLUDED & tarred}"
    assert not EXCLUDED & mirrored, f"the mirror carried excluded files: {EXCLUDED & mirrored}"
    assert "routines/r1/apps/kept.txt" in tarred   # a workspace's exclude stays in its home
    assert mirrored == tarred, (f"only mirrored: {mirrored - tarred}; "
                                f"only tarred: {tarred - mirrored}")
