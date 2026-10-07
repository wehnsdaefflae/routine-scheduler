"""The agent desktop's in-guest control agent (deploy/desktop-guest/agent), its pure half.

The agent runs inside the VM against X11 and AT-SPI, neither of which exists here, so what is
held here is everything that decides WHAT it does before a process starts: the xdotool / wmctrl
argv each request becomes, the window-geometry parsing (wmctrl and xdotool report a window a
title bar too low under a reparenting manager — the reason it reads xwininfo), the overlays'
geometry, which accessibility nodes become numbered elements, and the two guards on the door.
The live half was exercised against a booted guest; docs/desktop-sessions.md says how.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

AGENT_DIR = Path(__file__).resolve().parents[1] / "deploy" / "desktop-guest" / "agent"
sys.path.insert(0, str(AGENT_DIR))

import a11y  # noqa: E402
import agent  # noqa: E402
import screen  # noqa: E402
import xctl  # noqa: E402

# ---- input -----------------------------------------------------------------------------------


def test_a_modified_double_click_holds_the_modifier_around_the_clicks():
    argv = xctl.click_argv(10, 20, count=2, mods=["Ctrl", "shift"])
    assert argv[:5] == ["xdotool", "mousemove", "--sync", "10", "20"]
    assert argv[5:9] == ["keydown", "ctrl", "keydown", "shift"]
    assert argv[9:15] == ["click", "--repeat", "2", "--delay", "80", "1"]
    assert argv[15:] == ["keyup", "shift", "keyup", "ctrl"]


@pytest.mark.parametrize("kwargs", [{"button": "fourth"}, {"count": 4}, {"mods": ["hyper"]}])
def test_an_input_that_names_nothing_real_is_refused_before_anything_runs(kwargs):
    with pytest.raises(xctl.InputError):
        xctl.click_argv(1, 1, **kwargs)


@pytest.mark.parametrize("point", [(-1, 5), ("a", 5), (None, 5)])
def test_a_point_off_screen_or_not_a_number_is_refused(point):
    with pytest.raises(xctl.InputError):
        xctl.move_argv(*point)


def test_a_drag_presses_travels_in_steps_and_releases_where_it_was_aimed():
    steps = xctl.drag_argvs(0, 0, 120, 60, mods=["alt"])
    assert steps[0] == ["xdotool", "mousemove", "--sync", "0", "0"]
    assert steps[1] == ["xdotool", "keydown", "alt", "mousedown", "1"]
    moves = steps[2:-1]
    assert len(moves) == xctl.DRAG_STEPS
    assert moves[-1] == ["xdotool", "mousemove", "120", "60"]
    assert steps[-1] == ["xdotool", "mouseup", "1", "keyup", "alt"]


def test_scroll_direction_follows_the_sign():
    argv = xctl.scroll_argv(5, 5, dy=-3, dx=2)
    assert argv[argv.index("click") + 5] == xctl.WHEEL["up"]
    assert argv[-1] == xctl.WHEEL["right"]
    with pytest.raises(xctl.InputError):
        xctl.scroll_argv(5, 5)


def test_a_key_chord_is_a_keysym_list_never_a_command_line():
    assert xctl.key_argv(["ctrl+shift+t", "Return"])[-2:] == ["ctrl+shift+t", "Return"]
    for bad in (["rm -rf /"], ["ctrl+"], [], ["a;b"]):
        with pytest.raises(xctl.InputError):
            xctl.key_argv(bad)


# ---- windows ---------------------------------------------------------------------------------

XWININFO = """
xwininfo: Window id: 0x1a00003 "Untitled 1 - Mousepad"

  Absolute upper-left X:  5
  Absolute upper-left Y:  56
  Relative upper-left X:  5
  Relative upper-left Y:  29
  Width: 640
  Height: 480
"""


def test_window_geometry_is_read_from_xwininfo_and_widened_to_the_outer_frame():
    """wmctrl -G and xdotool both said 10,85 for this window; it is drawn at 5,56."""
    client = xctl.parse_xwininfo(XWININFO)
    extents = xctl.parse_extents("_NET_FRAME_EXTENTS(CARDINAL) = 5, 5, 29, 5")
    assert client == (5, 56, 640, 480)
    assert xctl.outer_box(client, extents) == (0, 27, 650, 514)
    assert xctl.parse_extents("_NET_FRAME_EXTENTS:  not found.") == (0, 0, 0, 0)


def test_wmctrl_rows_keep_titles_with_spaces():
    rows = xctl.parse_wmctrl("0x01e00003  0 567    desktop Window manager - Wikipedia\n"
                             "0x00c00003 -1 0      desktop xfce4-panel\n")
    assert rows[0] == {"id": "0x01e00003", "desktop": 0, "pid": 567,
                       "title": "Window manager - Wikipedia"}
    assert rows[1]["desktop"] == -1


def test_resize_converts_the_outer_size_wmctrl_would_apply_to_the_client():
    """wmctrl places the outer frame but sizes the client, so a resize to the size `windows`
    printed would grow the window by its decorations if passed through."""
    argv = xctl.window_argv("resize", "0x1", w=710, h=534, extents=(5, 5, 29, 5))
    assert argv[-1] == "0,-1,-1,700,500"
    assert xctl.window_argv("move", "0x1", x=200, y=100)[-1] == "0,200,100,-1,-1"
    with pytest.raises(xctl.InputError):
        xctl.window_argv("move", "0x1")
    with pytest.raises(xctl.InputError):
        xctl.window_argv("focus", "Mousepad")


# ---- overlays --------------------------------------------------------------------------------


def test_a_zoom_at_the_edge_keeps_its_size_and_stays_on_screen():
    assert screen.zoom_box(5, 5, 100, (1280, 800)) == (0, 0, 200, 200)
    assert screen.zoom_box(1279, 799, 100, (1280, 800)) == (1080, 600, 1280, 800)
    assert screen.zoom_box(640, 400, 1, (1280, 800))[2] - screen.zoom_box(
        640, 400, 1, (1280, 800))[0] == 2 * screen.ZOOM_MIN_RADIUS


def test_zoom_grid_lines_are_round_screen_coordinates():
    step = screen.zoom_step(60)
    assert step == 20
    assert screen.grid_lines(240, 360, step) == [240, 260, 280, 300, 320, 340]
    assert screen.to_screen(180, 240, 3) == 300


def test_mark_tags_never_cover_one_another():
    taken: list[tuple[int, int, int, int]] = []
    for rect in ((0, 0, 102, 26), (0, 0, 1280, 800), (2, 2, 50, 20)):
        tx, ty = screen.label_box(rect, 20, 16, (1280, 800), taken)
        box = (tx, ty, tx + 20, ty + 16)
        assert not any(screen._overlaps(box, t) for t in taken), (rect, box, taken)
        assert tx >= 0 and ty >= 0
        taken.append(box)


# ---- the accessibility filter ----------------------------------------------------------------

SCREEN = (1280, 800)


def node(role, name="", *, x=10, y=10, w=40, h=20, actions=(), focusable=False):
    return {"role": role, "name": name, "x": x, "y": y, "w": w, "h": h, "showing": True,
            "actions": list(actions), "focusable": focusable}


@pytest.mark.parametrize(("info", "kept"), [
    (node("push button", "Save", actions=["press"]), True),
    (node("push button", "", actions=["press"]), True),          # an icon a shot can show
    (node("link", "Donate", actions=["jump"]), True),
    (node("section", "", actions=["clickAncestor"]), False),      # Firefox's noise
    (node("landmark", "Site", actions=["clickAncestor"]), False),
    (node("section", "Menu", actions=["click"]), True),           # a named, clickable container
    (node("frame", "Firefox", w=1280, h=749, focusable=True), False),
    (node("frame", "Firefox", w=1280, h=749, actions=["click"]), False),
    (node("document web", "Window manager", actions=["click"]), False),
    (node("table cell", ""), False),
    (node("table cell", "Desktop"), True),
    (node("heading", "Window manager"), False),                   # words only with `all`
])
def test_only_things_a_person_operates_become_elements(info, kept):
    assert a11y.keep(info, everything=False, screen=SCREEN, clip=None) is kept


def test_words_are_listed_only_when_asked_for():
    heading = node("heading", "Window manager")
    assert a11y.keep(heading, everything=True, screen=SCREEN, clip=None)
    assert not a11y.keep(node("label", ""), everything=True, screen=SCREEN, clip=None)


def test_a_scoped_walk_keeps_only_what_lies_inside_the_window():
    clip = (0, 51, 1280, 800)
    assert a11y.keep(node("push button", "Back", y=96), everything=False, screen=SCREEN,
                     clip=clip)
    assert not a11y.keep(node("push button", "Mousepad", y=0), everything=False,
                         screen=SCREEN, clip=clip)
    off = node("push button", "x", x=-200, y=900)
    assert not a11y.keep(off, everything=False, screen=SCREEN, clip=None)


def test_a_wrapper_with_its_targets_name_is_listed_once_as_the_target():
    item = node("list item", "Donate", x=1016, y=162, w=51, h=17)
    link = node("link", "Donate", x=1016, y=162, w=51, h=17) | {"depth": 9}
    item["depth"] = 8
    other = node("push button", "Search", x=662, y=154, w=78, h=32)
    assert a11y.dedupe([item, link, other]) == [link, other]


def test_elements_are_numbered_in_reading_order():
    found = a11y.number([node("b", "", x=300, y=14), node("a", "", x=10, y=200),
                         node("c", "", x=20, y=10)])
    assert [(e["n"], e["role"]) for e in found] == [(1, "c"), (2, "b"), (3, "a")]


def test_find_matches_name_or_description_and_an_exact_role():
    el = node("link", "View history") | {"description": "past revisions"}
    assert a11y.matches(el, "history", None)
    assert a11y.matches(el, "REVISIONS", "link")
    assert not a11y.matches(el, "history", "push button")


# ---- the door --------------------------------------------------------------------------------


class Headers(dict):
    def get(self, key, default=None):
        return super().get(key, default)


def test_the_agent_refuses_what_a_web_page_inside_the_desktop_could_send():
    """A page in the desktop's own Firefox can reach the agent's address; a 'simple' POST
    needs no preflight. JSON content type plus no Origin is something only the broker sends."""
    agent.gate(Headers({"Content-Type": "application/json"}))
    with pytest.raises(agent.RefusedError):
        agent.gate(Headers({"Content-Type": "application/json", "Origin": "https://evil"}))
    with pytest.raises(agent.RefusedError):
        agent.gate(Headers({"Content-Type": "text/plain"}))


def test_files_move_through_the_desktop_home_only(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "HOME", tmp_path)
    (tmp_path / "docs").mkdir()
    assert agent.confine("docs/a.txt") == tmp_path / "docs" / "a.txt"
    assert agent.confine(str(tmp_path / "x")) == tmp_path / "x"
    (tmp_path / "out").symlink_to("/etc")
    for escape in ("../etc/passwd", "/etc/passwd", "out/passwd", ""):
        with pytest.raises(agent.RefusedError):
            agent.confine(escape)


def test_the_broker_alone_reaches_the_mount_operations():
    """`mount_share` mounts whatever device tag it is told; only the broker, after a folder's
    proof held, may ask for it — so the broker never forwards it from a caller."""
    sys.path.insert(0, str(AGENT_DIR.parents[1] / "desktop-broker"))
    import broker
    assert hasattr(agent.Desktop, "op_mount_share")
    assert "mount_share" not in broker.FORWARD
    assert "unmount_share" not in broker.FORWARD
    acting = {name[3:] for name in dir(agent.Desktop) if name.startswith("op_")}
    assert acting >= broker.FORWARD
