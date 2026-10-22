"""F649: a desktop nobody is watching needs no VNC server.

The operator's order, 2026-10-16: "give the desktop a headless mode a routine can select, so a
desktop-driving run needs no VNC-backed display when nobody is watching." The guest session
already ran `Xvnc` — an X server that IS a VNC server — so "headless" here means *no RFB port
at all*: `Xvfb` gives the same display, the same XFCE and the same accessibility bus, and the
agent (which grabs from $DISPLAY, not from the VNC stream) is untouched.

What is pinned here is the SELECTION PATH, end to end, in the places a test can reach it:
the header a routine sets, the kernel command line the broker writes, the marker the guest's
boot script leaves (and REMOVES), the display server the session script then chooses, and the
two places that must not advertise a screen that cannot connect.

What no test here can prove: that a real Cloud Hypervisor guest comes up on Xvfb. That needs a
booted VM, which the gate has no way to run — it is named in the release notes as the one part
verified by reading rather than by execution.
"""

from __future__ import annotations

import re
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "deploy" / "desktop-broker"))

import broker  # noqa: E402
import vms  # noqa: E402

GUEST = REPO / "deploy" / "desktop-guest"


def _vm(name: str, slot: int, *, headless: bool) -> SimpleNamespace:
    return SimpleNamespace(name=name, slot=slot, headless=headless, vnc=f"tok-{name}")


def test_the_kernel_command_line_carries_the_flag_only_when_asked():
    watched = vms.cmdline("routines--foo", 1, "Europe/Berlin")
    headless = vms.cmdline("routines--foo", 1, "Europe/Berlin", headless=True)
    assert "rsched.headless" not in watched, "a watched desktop must not carry the flag"
    assert "rsched.headless=1" in headless
    # everything else is unchanged: the flag is additive, not a different boot
    for part in ("rsched.ip=192.168.249.6/30", "rsched.gw=192.168.249.5",
                 "rsched.tz=Europe/Berlin", "rsched.name=routines--foo"):
        assert part in headless


def test_a_headless_desktop_is_absent_from_the_vnc_token_file():
    """A token pointing at a closed port makes noVNC hang on a blank canvas instead of saying
    there is no screen, so the headless VM must not be in the file at all."""
    pool = {"a": _vm("a", 0, headless=False), "b": _vm("b", 1, headless=True)}
    tokens = vms.vnc_tokens(pool)
    assert "tok-a: 192.168.249.2:5900" in tokens
    assert "tok-b" not in tokens


def test_the_status_row_says_which_mode_and_withholds_the_token(tmp_path):
    """"No screen" must be distinguishable from "the screen is broken", and the operator's view
    must not offer a screen that cannot connect."""
    fleet = vms.Fleet.__new__(vms.Fleet)
    fleet.s = SimpleNamespace(max_vms=2, idle_s=600)
    fleet.lock = threading.Lock()
    ready = threading.Event()
    ready.set()
    made = {}
    for nm, hl in (("watched", False), ("blind", True)):
        vm = vms.VM(name=nm, slot=0 if nm == "watched" else 1, dir=tmp_path / nm,
                    headless=hl)
        vm.ready = ready
        made[nm] = vm
    fleet.vms = made
    rows = {r["name"]: r for r in fleet.status(operator=True)}
    assert rows["watched"]["headless"] is False
    assert rows["blind"]["headless"] is True
    assert "vnc" in rows["watched"], "a watched desktop keeps its operator token"
    assert "vnc" not in rows["blind"], "a headless desktop has no screen to hand out"
    # the routine's own view never carries a token either way, and still says the mode
    plain = {r["name"]: r for r in fleet.status()}
    assert "vnc" not in plain["watched"] and plain["blind"]["headless"] is True


def test_the_boot_script_removes_a_stale_marker():
    """One root image backs every VM, so a marker left by an earlier boot would silently take
    the screen away from a watched desktop. The script must clear it, not only write it."""
    text = (GUEST / "desktop-boot.sh").read_text(encoding="utf-8")
    assert "rm -f /etc/desktop-headless" in text
    assert re.search(r'if \[ "\$headless" = "1" \]', text)
    assert "> /etc/desktop-headless" in text


def test_the_session_runs_xvfb_only_for_a_headless_boot():
    """The branch is on the MARKER, so a VM's mode is fixed by its own boot — and the watched
    path keeps every Xvnc flag it had, since those are what the token proxy depends on."""
    text = (GUEST / "desktop-session.sh").read_text(encoding="utf-8")
    assert "if [ -e /etc/desktop-headless ]; then" in text
    assert "Xvfb :1 -screen 0" in text
    for flag in ("-rfbport 5900", "-SecurityTypes None", "-AlwaysShared"):
        assert flag in text, f"the watched path lost {flag!r}"
    # both servers must land on the same display, or the agent and XFCE find nothing
    assert text.count(":1") >= 2 and "export DISPLAY=:1" in text


@pytest.mark.parametrize(("value", "wanted"),
                         [("1", True), ("true", True), ("yes", True), (" 1 ", True),
                          ("", False), ("0", False), ("no", False), ("maybe", False)])
def test_the_header_is_read_strictly(value, wanted):
    """A routine selects the mode with X-Desktop-Headless on any forwarded call. Anything that
    is not an affirmative leaves the desktop watched — the safe default, because a desktop whose
    screen the operator cannot open is one he cannot supervise."""
    handler = broker.Handler.__new__(broker.Handler)
    handler.headers = {"X-Desktop-Headless": value}
    assert handler.wants_headless() is wanted


def test_an_absent_header_is_a_watched_desktop():
    handler = broker.Handler.__new__(broker.Handler)
    handler.headers = {}
    assert handler.wants_headless() is False


def test_the_headers_choice_reaches_the_boot_and_not_only_the_reader():
    """The seam that would otherwise rot silently: `route` must PASS what `wants_headless`
    read into `ensure`, since the display server is chosen at boot and nowhere else. The
    through-the-door version of this lives in tests/test_desktop_broker.py's `door` fixture,
    whose `ensure` stub records the mode it is handed.
    """
    seen: list = []

    class _Fleet:
        s = SimpleNamespace(max_vms=2, idle_s=600)

        def ensure(self, name, *, headless=False):
            seen.append((name, headless))
            return SimpleNamespace(name=name, lock=threading.Lock(), last_used=0.0)

    handler = broker.Handler.__new__(broker.Handler)
    handler.fleet = _Fleet()
    handler.headers = {"X-Desktop-Headless": "1"}
    original = vms.agent_call
    vms.agent_call = lambda vm, op, raw, timeout: {"ok": True}
    try:
        handler.route("windows", "routines--foo", b"{}")
        handler.headers = {}
        handler.route("windows", "routines--bar", b"{}")
    finally:
        vms.agent_call = original
    assert seen == [("routines--foo", True), ("routines--bar", False)]
