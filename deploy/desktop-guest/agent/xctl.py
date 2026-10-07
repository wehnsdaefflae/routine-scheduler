"""X11 input, windows and clipboard — the hands of the desktop agent.

Everything a person does with a mouse and a keyboard reaches an X server as XTEST events, and
`xdotool` is the long-standing way to send them: a click is a click to every application, a drag
between two windows is a real drag-and-drop, a key chord is the chord. That is the reason the
guest runs X11 (Xvnc) and not Wayland, where synthesized input needs a portal and a consent
dialog. Window management speaks EWMH through `wmctrl`; the clipboard is `xclip`.

The argv BUILDERS are pure (and tested on the host); `run` is the one place a process starts.
"""
from __future__ import annotations

import re
import subprocess
import time

#: X button numbers. 4-7 are the wheel: up, down, left, right.
BUTTONS = {"left": "1", "middle": "2", "right": "3"}
WHEEL = {"up": "4", "down": "5", "left": "6", "right": "7"}
#: Modifier names a caller may hold during a click or a drag (xdotool keysym names).
MODIFIERS = {"shift": "shift", "ctrl": "ctrl", "control": "ctrl", "alt": "alt",
             "super": "super", "meta": "super"}
#: A key chord as xdotool reads it: keysyms joined by '+', e.g. ctrl+shift+t, Return, F5.
CHORD = re.compile(r"^[A-Za-z0-9_]+(\+[A-Za-z0-9_]+)*$")
#: How many intermediate pointer moves a drag makes. One jump from A to B is not a drag to most
#: toolkits: they start a drag-and-drop only after the pointer has MOVED with the button held.
DRAG_STEPS = 12
RUN_TIMEOUT_S = 20


class InputError(ValueError):
    """A request that names no valid input — reported to the caller, never executed."""


def run(argv: list[str], *, stdin: str | None = None, timeout: float = RUN_TIMEOUT_S) -> str:
    """Run one X tool and return its stdout; a failure raises with its stderr."""
    done = subprocess.run(argv, input=stdin, capture_output=True, text=True, timeout=timeout,
                          check=False)
    if done.returncode != 0:
        raise RuntimeError(f"{argv[0]} failed ({done.returncode}): {done.stderr.strip()[:400]}")
    return done.stdout


def _point(x: object, y: object) -> tuple[str, str]:
    try:
        xi, yi = int(x), int(y)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        raise InputError(f"x and y must be integers, got {x!r}, {y!r}") from None
    if xi < 0 or yi < 0:
        raise InputError(f"({xi}, {yi}) is off screen")
    return str(xi), str(yi)


def _mods(mods: list[str] | None) -> list[str]:
    out = []
    for m in mods or []:
        key = MODIFIERS.get(str(m).lower())
        if key is None:
            raise InputError(f"unknown modifier {m!r} (one of {', '.join(sorted(MODIFIERS))})")
        out.append(key)
    return out


def click_argv(x: object, y: object, *, button: str = "left", count: int = 1,
               mods: list[str] | None = None) -> list[str]:
    """Move to (x, y) and click — `count` 2 is a double click — with modifiers held."""
    if button not in BUTTONS:
        raise InputError(f"button must be one of {', '.join(BUTTONS)}")
    if not 1 <= int(count) <= 3:
        raise InputError("count is 1 (click), 2 (double) or 3 (triple)")
    held = _mods(mods)
    argv = ["xdotool", "mousemove", "--sync", *_point(x, y)]
    for key in held:
        argv += ["keydown", key]
    argv += ["click", "--repeat", str(int(count)), "--delay", "80", BUTTONS[button]]
    for key in reversed(held):
        argv += ["keyup", key]
    return argv


def move_argv(x: object, y: object) -> list[str]:
    return ["xdotool", "mousemove", "--sync", *_point(x, y)]


def drag_argvs(x1: object, y1: object, x2: object, y2: object, *, button: str = "left",
               mods: list[str] | None = None) -> list[list[str]]:
    """A drag as separate steps (the caller sleeps between them): press, travel, release."""
    if button not in BUTTONS:
        raise InputError(f"button must be one of {', '.join(BUTTONS)}")
    (ax, ay), (bx, by) = _point(x1, y1), _point(x2, y2)
    held = _mods(mods)
    steps = [["xdotool", "mousemove", "--sync", ax, ay],
             ["xdotool", *[w for k in held for w in ("keydown", k)], "mousedown", BUTTONS[button]]]
    for i in range(1, DRAG_STEPS + 1):
        px = int(ax) + (int(bx) - int(ax)) * i // DRAG_STEPS
        py = int(ay) + (int(by) - int(ay)) * i // DRAG_STEPS
        steps.append(["xdotool", "mousemove", str(px), str(py)])
    steps.append(["xdotool", "mouseup", BUTTONS[button],
                  *[w for k in reversed(held) for w in ("keyup", k)]])
    return steps


def scroll_argv(x: object, y: object, *, dy: int = 0, dx: int = 0) -> list[str]:
    """Wheel clicks at (x, y): positive dy scrolls DOWN, positive dx scrolls RIGHT."""
    if not dy and not dx:
        raise InputError("scroll needs a non-zero dy or dx")
    argv = ["xdotool", "mousemove", "--sync", *_point(x, y)]
    if dy:
        argv += ["click", "--repeat", str(abs(int(dy))), "--delay", "40",
                 WHEEL["down" if dy > 0 else "up"]]
    if dx:
        argv += ["click", "--repeat", str(abs(int(dx))), "--delay", "40",
                 WHEEL["right" if dx > 0 else "left"]]
    return argv


def type_argv(text: str) -> list[str]:
    if not isinstance(text, str) or not text:
        raise InputError("type needs non-empty text")
    return ["xdotool", "type", "--clearmodifiers", "--delay", "12", "--", text]


def key_argv(chords: list[str], *, op: str = "key") -> list[str]:
    """Press chords in order (`key`), or hold / release them (`keydown` / `keyup`)."""
    if op not in ("key", "keydown", "keyup"):
        raise InputError("op is key, keydown or keyup")
    if not chords:
        raise InputError("name at least one key, e.g. ctrl+s or Return")
    bad = [c for c in chords if not CHORD.match(str(c))]
    if bad:
        raise InputError(f"not a key chord: {bad} — keysyms joined by '+', e.g. ctrl+shift+t")
    return ["xdotool", op, "--clearmodifiers", "--", *map(str, chords)]


def button_argv(op: str, *, button: str = "left") -> list[str]:
    if op not in ("mousedown", "mouseup") or button not in BUTTONS:
        raise InputError("mousedown/mouseup with a left/middle/right button")
    return ["xdotool", op, BUTTONS[button]]


def run_steps(steps: list[list[str]], *, pause: float = 0.02) -> None:
    for argv in steps:
        run(argv)
        time.sleep(pause)


# ---- windows -------------------------------------------------------------------------------

def parse_wmctrl(listing: str) -> list[dict]:
    """`wmctrl -lp` rows: id, desktop, pid, host, title (the title keeps its spaces)."""
    rows = []
    for line in listing.splitlines():
        parts = line.split(None, 4)
        if len(parts) < 4:
            continue
        rows.append({"id": parts[0], "desktop": int(parts[1]), "pid": int(parts[2]),
                     "title": parts[4] if len(parts) > 4 else ""})
    return rows


def parse_xwininfo(text: str) -> tuple[int, int, int, int]:
    """The CLIENT area's absolute (x, y, w, h) from `xwininfo -id`.

    Not `wmctrl -G` or `xdotool getwindowgeometry`: under a reparenting window manager both add
    the decoration offset twice (a window drawn at 5,56 reports 10,85), and every coordinate the
    agent derived from them would miss by the height of a title bar.
    """
    found = {}
    for line in text.splitlines():
        key, _, value = line.strip().partition(":")
        found[key.strip()] = value.strip()
    return (int(found["Absolute upper-left X"]), int(found["Absolute upper-left Y"]),
            int(found["Width"]), int(found["Height"]))


def parse_extents(text: str) -> tuple[int, int, int, int]:
    """(left, right, top, bottom) decoration widths from `xprop _NET_FRAME_EXTENTS`; zeros for a
    window the manager does not decorate.
    """
    _, _, values = text.partition("=")
    nums = [int(v) for v in values.replace(",", " ").split() if v.strip().isdigit()]
    return (nums[0], nums[1], nums[2], nums[3]) if len(nums) == 4 else (0, 0, 0, 0)


def outer_box(client: tuple[int, int, int, int],
              extents: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    """The window as a person sees it — client area plus title bar and borders."""
    (x, y, w, h), (left, right, top, bottom) = client, extents
    return x - left, y - top, w + left + right, h + top + bottom


def windows() -> list[dict]:
    """Every managed window with its OUTER box (what `move` and `resize` take) and whether it
    is the active one. Helper windows a few pixels square are left out.
    """
    try:
        active = int(run(["xdotool", "getactivewindow"]).strip())
    except (RuntimeError, ValueError):
        active = -1
    rows = []
    for row in parse_wmctrl(run(["wmctrl", "-lp"])):
        try:
            client = parse_xwininfo(run(["xwininfo", "-id", row["id"]], timeout=5))
            extents = parse_extents(run(["xprop", "-id", row["id"], "_NET_FRAME_EXTENTS"],
                                        timeout=5))
        except (RuntimeError, KeyError, ValueError):
            continue                    # the window closed between the listing and the look
        x, y, w, h = outer_box(client, extents)
        if w <= 4 or h <= 4:
            continue
        row.update(x=x, y=y, w=w, h=h, extents=list(extents),
                   active=int(row["id"], 16) == active)
        rows.append(row)
    return rows


def active_window() -> dict | None:
    return next((w for w in windows() if w["active"]), None)


WINDOW_OPS = ("focus", "close", "minimize", "maximize", "unmaximize", "fullscreen", "move",
              "resize", "geometry")


def window_argv(op: str, wid: str, *, x: int | None = None, y: int | None = None,
                w: int | None = None, h: int | None = None,
                extents: tuple[int, int, int, int] = (0, 0, 0, 0)) -> list[str]:
    """One EWMH operation on the window `wid` (hex id as `windows` lists it).

    x/y/w/h are the OUTER box, as `windows` reports it. wmctrl places the outer frame but sizes
    the CLIENT area, so a width and height are converted with the window's decoration extents —
    `resize` to the size `windows` printed leaves the window that size.
    """
    if not re.fullmatch(r"0x[0-9a-fA-F]+", wid or ""):
        raise InputError(f"window id must be hex as `windows` lists it, got {wid!r}")
    simple = {
        "focus": ["wmctrl", "-i", "-a", wid],
        "close": ["wmctrl", "-i", "-c", wid],
        "minimize": ["xdotool", "windowminimize", str(int(wid, 16))],
        "maximize": ["wmctrl", "-i", "-r", wid, "-b", "add,maximized_vert,maximized_horz"],
        "unmaximize": ["wmctrl", "-i", "-r", wid, "-b", "remove,maximized_vert,maximized_horz"],
        "fullscreen": ["wmctrl", "-i", "-r", wid, "-b", "toggle,fullscreen"],
    }
    if op in simple:
        return simple[op]
    if op in ("move", "resize", "geometry"):
        # -1 keeps a coordinate as it is: `move` changes only x/y, `resize` only w/h.
        left, right, top, bottom = extents
        cw = None if w is None else max(1, int(w) - left - right)
        ch = None if h is None else max(1, int(h) - top - bottom)
        vals = [-1 if v is None else int(v) for v in (x, y, cw, ch)]
        if all(v == -1 for v in vals):
            raise InputError(f"{op} needs x/y and/or w/h")
        return ["wmctrl", "-i", "-r", wid, "-e", "0," + ",".join(map(str, vals))]
    raise InputError(f"window op is one of {', '.join(WINDOW_OPS)}")


def workspace_argv(index: int) -> list[str]:
    return ["wmctrl", "-s", str(int(index))]


# ---- clipboard -----------------------------------------------------------------------------

def clipboard_get() -> str:
    try:
        return run(["xclip", "-selection", "clipboard", "-o"], timeout=5)
    except RuntimeError:
        return ""                       # an empty clipboard is an error to xclip, not to us


def clipboard_set(text: str) -> None:
    # xclip forks to serve the selection and keeps it until something else owns it; -loops 0
    # would be "forever" too, but detaching is what lets this call return.
    subprocess.Popen(["xclip", "-selection", "clipboard", "-i"], stdin=subprocess.PIPE,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True).communicate(text.encode(), timeout=5)


def screen_size() -> tuple[int, int]:
    out = run(["xdotool", "getdisplaygeometry"]).split()
    return int(out[0]), int(out[1])
