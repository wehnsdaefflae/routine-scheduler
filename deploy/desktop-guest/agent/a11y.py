"""The accessibility tree — what is on screen, by NAME, without reading a single pixel.

Screen readers ask every toolkit (GTK, Qt, Firefox, Chromium/Electron with accessibility on)
for its widgets over AT-SPI: role, name, state, the actions it supports and — under X11 — its
box in SCREEN coordinates. That is the most reliable way to find a click target there is: no
vision call, no estimate, and an element can be activated by its own action instead of a
synthesized click. (Under Wayland a client cannot know where its window is, so the boxes come
back window-relative; the guest runs X11 for this reason as much as for input.)

What is enumerated is bounded — a node budget and a time budget — because every property is a
D-Bus round trip and a busy web page holds thousands of nodes. A walk that hit either bound says
so (`truncated`), so a short list is never mistaken for a complete one.

The filter is pure and tested on the host; `pyatspi` is imported only inside the guest.
"""
from __future__ import annotations

import time

#: Roles a person clicks, types into or otherwise operates.
INTERACTIVE = frozenset({
    "push button", "toggle button", "check box", "radio button", "menu item",
    "check menu item", "radio menu item", "menu", "combo box", "entry", "password text",
    "text", "link", "page tab", "list item", "table cell", "tree item", "icon", "slider",
    "spin button", "toggle", "button", "tool bar item", "split button", "terminal",
    "editbar", "table column header", "table row header",
})
#: Roles that only carry words (or a picture's alt text) — kept with `all=True`, so a run can
#: read a dialog's message without a screenshot.
TEXTUAL = frozenset({"label", "heading", "static", "paragraph", "status bar", "alert",
                     "notification", "caption", "description term", "tooltip", "image"})
#: Structure, never a target by itself: a frame, a section, a landmark, a list around its items.
#: One of these is kept only when it has a NAME and a real action of its own.
CONTAINERS = frozenset({
    "frame", "window", "panel", "filler", "scroll pane", "viewport", "internal frame",
    "section", "landmark", "form", "list", "tool bar", "menu bar", "separator", "split pane",
    "layered pane", "root pane", "glass pane", "page tab list", "table", "tree table", "tree",
    "document frame", "document web", "unknown", "redundant object", "block quote", "grouping",
    "article", "image map", "file chooser", "dialog", "scroll bar", "header", "footer",
})
#: Structure that is NEVER a target, whatever it claims: a window's frame or a page's document
#: says "click" (Firefox), but clicking it means nothing a person would aim at.
NEVER = frozenset({"frame", "window", "document web", "document frame", "internal frame",
                   "root pane", "layered pane", "glass pane", "viewport", "filler",
                   "scroll pane", "desktop frame", "application"})
#: Actions that do not make a node a target. Firefox gives EVERY node `clickAncestor` ("a click
#: here lands on some ancestor's handler"), which once marked 220 elements on one page.
NOISE_ACTIONS = frozenset({"clickAncestor", "showContextMenu", "scroll", "customAction"})
#: Interactive roles worth nothing without a name: a table row's empty cells, a sidebar row's
#: decorative icon. A nameless button stays — it is often an icon a screenshot can show.
NAMELESS_DROP = frozenset({"table cell", "icon", "list item", "table column header"})
#: Top-level roles that are transient over their owner: kept even when not the active window.
TRANSIENT = frozenset({"menu", "popup menu", "window", "dialog", "alert", "tool tip",
                       "notification", "file chooser"})
NODE_BUDGET = 4000
TIME_BUDGET_S = 8.0
MAX_DEPTH = 64
NAME_MAX = 120


def real_actions(info: dict) -> list[str]:
    return [a for a in info.get("actions") or [] if a not in NOISE_ACTIONS]


def keep(info: dict, *, everything: bool, screen: tuple[int, int],
         clip: tuple[int, int, int, int] | None) -> bool:
    """Whether one enumerated node becomes a listed element.

    It must be showing, have an area that reaches the screen (and the clip, when the walk is
    scoped to a window), and be something a person operates — or, with `everything`, at least
    carry words.
    """
    if not info.get("showing") or info["w"] <= 0 or info["h"] <= 0:
        return False
    x, y, w, h = info["x"], info["y"], info["w"], info["h"]
    if x + w <= 0 or y + h <= 0 or x >= screen[0] or y >= screen[1]:
        return False
    if clip is not None:
        cx0, cy0, cx1, cy1 = clip
        mx, my = x + w // 2, y + h // 2
        if not (cx0 <= mx < cx1 and cy0 <= my < cy1):
            return False
    role, named, acts = info["role"], bool(info.get("name")), real_actions(info)
    if role in NEVER:
        return False
    if role in CONTAINERS:
        return named and bool(acts)
    if role in INTERACTIVE:
        return named or role not in NAMELESS_DROP
    if acts:
        return True
    return everything and role in TEXTUAL and named


def dedupe(found: list[dict]) -> list[dict]:
    """Drop an element that only WRAPS another with the same name — a list item around its link,
    a cell around its button: one target, listed once, as the innermost (most specific) node.
    """
    by_name: dict[str, list[dict]] = {}
    for el in found:
        if el["name"]:
            by_name.setdefault(el["name"], []).append(el)
    drop: set[int] = set()
    for group in by_name.values():
        for outer in group:
            for inner in group:
                if inner is outer or id(inner) in drop:
                    continue
                if (inner["x"] >= outer["x"] - 2 and inner["y"] >= outer["y"] - 2
                        and inner["x"] + inner["w"] <= outer["x"] + outer["w"] + 2
                        and inner["y"] + inner["h"] <= outer["y"] + outer["h"] + 2
                        and (inner["w"] * inner["h"] < outer["w"] * outer["h"]
                             or (inner.get("depth", 0) > outer.get("depth", 0)))):
                    drop.add(id(outer))
                    break
    return [el for el in found if id(el) not in drop]


def number(found: list[dict]) -> list[dict]:
    """Give each kept element its mark number, in reading order (top to bottom, then left to
    right, on a 12-px row band so a row of buttons reads as one row).
    """
    found.sort(key=lambda e: (e["y"] // 12, e["x"]))
    for i, el in enumerate(found, start=1):
        el["n"] = i
    return found


def matches(el: dict, text: str, role: str | None) -> bool:
    if role and el["role"] != role:
        return False
    needle = text.casefold()
    return needle in el["name"].casefold() or needle in el.get("description", "").casefold()


# ---- the walk (guest only) -----------------------------------------------------------------

def _info(node, pyatspi) -> dict | None:
    try:
        states = node.getState()
        role = node.getRoleName()
        ext = node.queryComponent().getExtents(pyatspi.DESKTOP_COORDS)
        name = (node.name or "").strip().replace("\n", " ")[:NAME_MAX]
        description = (node.description or "")[:NAME_MAX]
    except Exception:
        return None                     # a node that vanished mid-walk, or has no component
    try:
        action = node.queryAction()
        actions = [action.getName(i) for i in range(action.nActions)]
    except Exception:
        actions = []                    # most nodes implement no Action interface at all
    return {
        "role": role, "name": name, "x": ext.x, "y": ext.y, "w": ext.width, "h": ext.height,
        "showing": states.contains(pyatspi.STATE_SHOWING),
        "focusable": states.contains(pyatspi.STATE_FOCUSABLE),
        "focused": states.contains(pyatspi.STATE_FOCUSED),
        "checked": states.contains(pyatspi.STATE_CHECKED),
        "enabled": states.contains(pyatspi.STATE_ENABLED),
        "actions": actions,
        "description": description,
    }


def _tops(desktop, *, scope: str, active: dict | None, pyatspi):
    """(app name, top-level node, its info, clip) for every window the walk should enter.

    `active` scope: the focused window's application, clipped to that window, plus its transient
    popups (a menu, a dialog) and the panel; `all`: everything; otherwise an application name.
    """
    for app in desktop:
        if app is None:
            continue
        try:
            app_name, pid = app.name or "", app.get_process_id()
        except Exception:  # noqa: S112 — an application that left mid-walk is simply not listed
            continue
        panel = app_name.startswith("xfce4-panel")
        if scope == "active":
            if not panel and (active is None or pid != active.get("pid")):
                continue
        elif scope not in ("all", app_name):
            continue
        for top in app:
            top_info = None if top is None else _info(top, pyatspi)
            if top_info is None or not top_info["showing"]:
                continue
            clip = None
            if scope == "active" and not panel and active is not None:
                transient = top_info["role"] in TRANSIENT
                title = active.get("title") or ""
                same = (top_info["name"] in (title, title.lstrip("*"))
                        or abs(top_info["x"] - active["x"]) + abs(top_info["y"] - active["y"]) < 64)
                if not (same or transient):
                    continue
                if same and not transient:
                    # The toolkit's own box for its window: the window manager's numbers are
                    # the outer frame, and only the client area holds widgets.
                    clip = (top_info["x"], top_info["y"], top_info["x"] + top_info["w"],
                            top_info["y"] + top_info["h"])
            yield app_name, top, top_info, clip


def walk(*, scope: str, active: dict | None, screen: tuple[int, int],
         everything: bool) -> tuple[list[dict], dict, bool]:
    """(elements, {n: accessible}, truncated) for `scope` (see `_tops`)."""
    import pyatspi
    deadline = time.monotonic() + TIME_BUDGET_S
    visited, truncated = 0, False
    found: list[tuple[dict, object]] = []
    for app_name, top, top_info, clip in _tops(pyatspi.Registry.getDesktop(0), scope=scope,
                                               active=active, pyatspi=pyatspi):
        stack = [(top, 0)]
        while stack:
            node, depth = stack.pop()
            visited += 1
            if visited > NODE_BUDGET or time.monotonic() > deadline:
                truncated = True
                break
            info = top_info if node is top else _info(node, pyatspi)
            if info is None or not info["showing"]:
                continue
            info["app"] = app_name
            info["depth"] = depth
            if not info["name"] and info["role"] in INTERACTIVE:
                info["name"] = _derived_name(node)
            if keep(info, everything=everything, screen=screen, clip=clip):
                found.append((info, node))
            if depth < MAX_DEPTH:
                try:
                    kids = [node.getChildAtIndex(i) for i in range(node.childCount)]
                except Exception:
                    kids = []
                stack.extend((k, depth + 1) for k in reversed(kids) if k is not None)
        if truncated:
            break
    kept = {id(info) for info in dedupe([info for info, _ in found])}
    found = [(info, node) for info, node in found if id(info) in kept]
    elements = number([info for info, _ in found])
    refs = {}
    for info, node in found:
        refs[info["n"]] = node
        info.pop("showing", None)
        info.pop("depth", None)
    return elements, refs, truncated


def _derived_name(node, budget: int = 8) -> str:
    """A nameless control's words from its descendants — a sidebar row is a list item whose
    label is its child. Breadth-first, a few nodes at most: every look is a D-Bus call.
    """
    queue = [node]
    while queue and budget > 0:
        current = queue.pop(0)
        try:
            kids = [current.getChildAtIndex(i) for i in range(min(current.childCount, 4))]
        except Exception:
            return ""
        for kid in kids:
            budget -= 1
            try:
                name = (kid.name or "").strip()
            except Exception:
                name = ""
            if name:
                return name.replace("\n", " ")[:NAME_MAX]
            queue.append(kid)
    return ""


def do_action(node, name: str | None) -> str:
    """Run one of the element's own AT-SPI actions (its first when unnamed); returns its name."""
    action = node.queryAction()
    names = [action.getName(i) for i in range(action.nActions)]
    if not names:
        raise ValueError("this element exposes no actions — click its centre instead")
    index = names.index(name) if name in names else 0
    if name and name not in names:
        raise ValueError(f"no action {name!r}; it has {names}")
    action.doAction(index)
    return names[index]


def set_text(node, text: str) -> None:
    node.queryEditableText().setTextContents(text)


def read_text(node, limit: int = 20000) -> str:
    try:
        t = node.queryText()
        return t.getText(0, min(t.characterCount, limit))
    except NotImplementedError:
        return node.name or ""


def grab_focus(node) -> None:
    node.queryComponent().grabFocus()
