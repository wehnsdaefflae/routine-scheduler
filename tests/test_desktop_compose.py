"""The agent-desktop sidecar as compose declares it, read as files (no Docker here).

Three promises an edit can break with nothing failing until a host notices:

1. The sidecar sees the shareable homes EXACTLY as the engine does — same source, same target,
   same mode — and never wider. A run names a folder as the engine sees it and proves its
   access there; the broker then serves the same path. A bind wider than the engine's, or a
   read-only bind beneath it left out (the LLMSecTest grant folder under /srv), would let a
   desktop write what no run may.
2. Its doors: opt-in by profile, the broker and the screens behind two different tokens that
   compose does not hard-require (a `:?` would stop every compose command on a host that never
   enabled it), and the screen published on the host's loopback only.
3. Every file the image COPYs is re-included by .dockerignore, which is an allowlist: a source
   it misses fails the build with "not found" on a file that is plainly present.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
HOME_VAR = "${RSCHED_HOME:-/home/mark}"
UTIL = REPO / "util-seed" / "utils" / "desktop" / "main.py"


def _services() -> dict:
    return yaml.safe_load((REPO / "docker-compose.yml").read_text(encoding="utf-8"))["services"]


def _binds(spec: dict) -> list[tuple[str, str, bool]]:
    out = []
    for vol in spec.get("volumes", []):
        if isinstance(vol, str):
            src, dst, *mode = vol.replace(HOME_VAR, "@").split(":")
            out.append((src.replace("@", HOME_VAR), dst, "ro" in mode))
        else:
            out.append((vol["source"], vol["target"], bool(vol.get("read_only"))))
    return out


#: The sidecar's own binds — not shares: its disks, and the host's zone.
OWN = {"/home/mark/desktop-vm", "/etc/localtime", "/etc/timezone"}


def test_every_shareable_bind_is_an_engine_bind_with_the_same_mode():
    services = _services()
    engine = set(_binds(services["rsched"]))
    shared = [b for b in _binds(services["desktop"]) if b[1] not in OWN]
    assert shared, "the desktop mirrors no engine bind at all"
    wider = [b for b in shared if b not in engine]
    assert not wider, f"desktop binds the engine does not have (or with another mode): {wider}"


def test_a_read_only_engine_bind_beneath_a_shared_one_is_mirrored_too():
    services = _services()
    shared = [b for b in _binds(services["desktop"]) if b[1] not in OWN]
    targets = [t for _, t, _ in shared]
    for bind in _binds(services["rsched"]):
        _, target, read_only = bind
        beneath = any(target != t and target.startswith(t.rstrip("/") + "/") for t in targets)
        if read_only and beneath:
            assert bind in shared, f"{target} is read-only for the engine but writable here"


def test_the_share_roots_the_broker_checks_are_the_mirrored_targets():
    spec = _services()["desktop"]
    roots = spec["environment"]["DESKTOP_SHARE_ROOTS"].split(":")
    top = {t for _, t, _ in _binds(spec) if t not in OWN}
    # the read-only grant folder is beneath /srv: a deeper mode, not another root
    top = {t for t in top if not any(t != o and t.startswith(o + "/") for o in top)}
    assert set(roots) == top


def test_the_sidecar_is_opt_in_and_its_doors_are_guarded():
    spec = _services()["desktop"]
    assert spec["profiles"] == ["desktop"]
    env = spec["environment"]
    for token in ("DESKTOP_VM_TOKEN", "DESKTOP_OPERATOR_TOKEN"):
        assert env[token] == "${" + token + ":-}", f"{token} must not be `:?` (see docstring)"
    assert all(p.startswith("127.0.0.1:") for p in spec["ports"]), spec["ports"]
    assert set(spec["cap_add"]) == {"NET_ADMIN"}
    assert set(spec["devices"]) == {"/dev/kvm", "/dev/net/tun"}


def test_the_util_dials_the_address_compose_pins():
    spec = _services()["desktop"]
    address = spec["networks"]["browser"]["ipv4_address"]
    default = re.search(r'^DEFAULT_URL = "([^"]+)"', UTIL.read_text(encoding="utf-8"), re.MULTILINE)
    assert default and default.group(1) == f"http://{address}:8790"


def test_the_entrypoint_keeps_the_two_tokens_apart():
    script = (REPO / "deploy" / "desktop-entrypoint.sh").read_text(encoding="utf-8")
    assert '"$DESKTOP_OPERATOR_TOKEN" = "$DESKTOP_VM_TOKEN"' in script
    assert re.search(r"--token-env DESKTOP_VM_TOKEN \\\n\s+--map \"0\.0\.0\.0:\$\{AGENT_PORT\}",
                     script)
    assert re.search(r"--token-env DESKTOP_OPERATOR_TOKEN \\\n\s+--map \"0\.0\.0\.0:\$\{VNC_PORT",
                     script)


def _dockerignore_admits(path: str) -> bool:
    admitted = False
    for line in (REPO / ".dockerignore").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        negate, pattern = line.startswith("!"), line.lstrip("!").rstrip("/")
        if pattern in ("*", path) or path.startswith(pattern + "/"):
            admitted = negate
    return admitted


def test_every_file_the_dockerfiles_copy_is_admitted_to_the_build_context():
    missing = []
    for dockerfile in [REPO / "Dockerfile", *sorted((REPO / "deploy").glob("Dockerfile*"))]:
        for line in dockerfile.read_text(encoding="utf-8").splitlines():
            if not line.startswith("COPY ") or "--from" in line:
                continue
            words = [w for w in line.split()[1:] if not w.startswith("--")]
            missing += [f"{dockerfile.name}: {source}" for source in words[:-1]
                        if not _dockerignore_admits(source.rstrip("/"))]
    assert not missing, f".dockerignore keeps these COPY sources out: {missing}"
