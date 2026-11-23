"""Does a catalog machine's login act with ROOT rights?

A machine whose login is root, or can become root (sudo, admin, wheel) or command a root daemon
(docker, lxd), turns every command a run sends into a command run as root. A run holding one is
under its routine's ROOT COMMAND CHECK (`root_check`, rsched/rootmodes.py, engine/rootcheck.py).
Privilege is DISCOVERED on the box, never guessed from the user name: `probe` runs
`id -u; id -Gn` through the `remote` util, the very path a run connects by, and records what
it found under `<routines_home>/.control/machine-privilege/<name>.json`, keyed to the host,
user and port it was probed as — a record for another login reads as never probed.

A machine never probed is UNKNOWN, and unknown counts as ROOT: a machine leaves the check only
once the box itself said its login is unprivileged. Derived state, never config — deleting a
record costs one probe.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from .paths import atomic_write_json, read_json

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .config import MachineConfig

log = logging.getLogger("rsched.machine_privilege")

PRIVILEGE_DIR = Path(".control") / "machine-privilege"
#: Groups whose members are root or one step from it: sudo/admin/wheel grant sudo on the common
#: distributions; docker and lxd run a root daemon a member can command.
ROOT_GROUPS = frozenset({"root", "sudo", "admin", "wheel", "docker", "lxd"})
#: Two lines the parser reads back, nothing else. `id` is POSIX, so every box answers it.
PROBE_COMMAND = 'printf "uid=%s\\ngroups=%s\\n" "$(id -u)" "$(id -Gn)"'
#: An engine probe waits this long: it runs before a run's first command, and a box that does
#: not answer by then stays unknown — counted as root — anyway.
ENGINE_PROBE_TIMEOUT_S = 20
#: The machines this process already asked, so an unreachable box costs one attempt per run.
_asked: set[str] = set()


def record_path(routines_home: Path, machine: str) -> Path:
    return routines_home / PRIVILEGE_DIR / f"{machine}.json"


def _login(mac: MachineConfig) -> dict:
    return {"host": mac.host, "user": mac.user, "port": mac.port}


def parse(stdout: str) -> tuple[bool, str] | None:
    """`(privileged, why)` from PROBE_COMMAND's output, or None when it did not answer."""
    fields = dict(line.partition("=")[::2] for line in stdout.splitlines() if "=" in line)
    uid = fields.get("uid", "").strip()
    if not uid.isdigit():
        return None
    if uid == "0":
        return True, "logs in as uid 0 (root)"
    held = sorted(ROOT_GROUPS & set(fields.get("groups", "").split()))
    if held:
        return True, f"member of {', '.join(held)}"
    return False, f"uid {uid}, in none of {', '.join(sorted(ROOT_GROUPS))}"


def record(routines_home: Path, machine: str, mac: MachineConfig, *, privileged: bool,
           why: str) -> dict:
    doc = {"machine": machine, **_login(mac), "privileged": privileged, "why": why,
           "probed": datetime.now(UTC).isoformat(timespec="seconds")}
    path = record_path(routines_home, machine)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, doc)
    return doc


def load(routines_home: Path, machine: str, mac: MachineConfig) -> dict | None:
    """The record for THIS login, or None when the box was never probed as it."""
    doc = read_json(record_path(routines_home, machine))
    if not isinstance(doc, dict) or not isinstance(doc.get("privileged"), bool):
        return None
    if any(doc.get(k) != v for k, v in _login(mac).items()):
        return None
    return doc


def privileged(routines_home: Path, machine: str, mac: MachineConfig) -> bool | None:
    doc = load(routines_home, machine, mac)
    return None if doc is None else bool(doc["privileged"])


def is_root(server, machine: str) -> bool:
    """Does `machine`'s login count as root for the check? Yes unless the box said otherwise.
    A machine outside the catalog is not: the `remote` util cannot reach it either.
    """
    mac = (server.machines or {}).get(machine)
    return mac is not None and privileged(server.routines_home, machine, mac) is not False


def probe(server, machine: str, *, timeout: int = 60) -> dict:
    """Ask the box who its login is, record the answer, and return it as
    `{privileged: bool | None, why}`. Never raises: an unreachable box stays UNKNOWN (None),
    its last record untouched, so it still counts as root.
    """
    from . import sandbox, utils_run
    from .machine_queue import REMOTE_UTIL
    from .machines import machines_for_routine

    mac = (server.machines or {}).get(machine)
    if mac is None:
        return {"privileged": None, "why": f"machine {machine!r} is not in the catalog"}
    env, _warnings = machines_for_routine([machine], server.machines)
    try:
        code, out, err = utils_run.run_util(
            server.libraries_home, REMOTE_UTIL,
            ["exec", machine, "--command", PROBE_COMMAND, "--json"],
            timeout=timeout, policy=sandbox.base_policy(server), extra_secrets=env)
    except OSError as exc:
        return {"privileged": None, "why": f"probe failed: {exc}"}
    try:
        stdout = str(json.loads(out).get("stdout") or "") if code == 0 else ""
    except (ValueError, AttributeError):
        stdout = ""
    found = parse(stdout)
    if found is None:
        why = (err.strip() or out.strip() or f"remote util exited {code}")[:300]
        log.warning("machine %s privilege probe got no answer: %s", machine, why)
        return {"privileged": None, "why": f"probe got no answer: {why}"}
    return record(server.routines_home, machine, mac, privileged=found[0], why=found[1])


def ensure_probed(server, names: list[str]) -> None:
    """Probe each named machine whose login was never probed — at most once per process,
    and one engine process is one run. Run at run start and by the root command check, so an
    unprivileged box (whose key a script may need, exec_env._machine_env) is learned unasked.
    """
    from . import utils_lib
    from .machine_queue import REMOTE_UTIL

    if not utils_lib.exists(server.libraries_home, REMOTE_UTIL):
        return                               # nothing could connect to it anyway
    for name in names:
        mac = (server.machines or {}).get(name)
        if (mac is None or name in _asked
                or privileged(server.routines_home, name, mac) is not None):
            continue
        _asked.add(name)
        probe(server, name, timeout=ENGINE_PROBE_TIMEOUT_S)


def catalog_rows(server) -> list[dict]:
    """The machine picker's rows: who each is, and whether its login counts as ROOT — which
    puts a run holding it under the routine's root command check.
    """
    return [{"name": m.name, "description": m.description, "host": m.host, "user": m.user,
             "tags": list(m.tags), "root": is_root(server, m.name)}
            for m in (server.machines or {}).values()]
