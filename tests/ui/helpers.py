"""Steps through the console that several browser tests take the same way.

Each one used to be copied into every file that needed it, so a markup change (a renamed
selector, a section moved into a fold) had to be found and answered in each copy. Plain
functions over a page or the `ui` harness rather than fixtures: none of them holds state or
needs a teardown. A helper with one caller stays in its test file.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from rsched import lanes


def unfold(page) -> None:
    """Open every routine-page settings group and each group's "more" menu.

    The page ships with only its two leading groups open (views/routine-config.js): seven open at
    once made it 11-12 000px tall. The rarely needed sections fold once more behind each group's
    "more". A control inside a fold is not visible, so a test that reads one unfolds first. What
    the DEFAULT is — and that the choice is remembered — is pinned in test_routine_groups.py, not
    here.
    """
    page.wait_for_selector(".rgroup-head")
    page.evaluate("() => { for (const d of document.querySelectorAll("
                  "'details.rgroup, details.rmore')) d.open = true; }")


def open_section(ui, page, section: str):
    """The fixture routine's page with `#<section>` — a section that sits in its group's
    "more" — unfolded; returns the section's panel."""
    page.goto(f"{ui.url}/#/routine/uir")
    page.wait_for_selector(f"#{section}", state="attached")
    page.evaluate(f"() => document.getElementById('{section}').closest('details').open = true")
    return page.locator(f"#{section} + .panel")


def visible_toast(page):
    """The console's toast while it shows — where a save, a refusal or a confirm reports."""
    return page.locator("#toast:not([hidden])")


def accept(page) -> None:
    """Click the accept bar's one button: the routine page writes its draft nowhere else."""
    page.locator(".accept-bar [data-accept]").click()


def confirm_modal(page, label: str) -> None:
    """Answer the themed confirm dialog (components/dialog.js) with its button named `label`.
    Native dialogs are gone; one appearing anywhere would block and fail the test, which is
    the point."""
    page.locator(".modal-overlay").get_by_role("button", name=label, exact=True).click()


def stored_config(ui, slug: str = "uir") -> dict:
    """What a routine's routine.yaml says now — where an act that claims to have landed has to
    show up."""
    return yaml.safe_load((ui.routines / slug / "routine.yaml").read_text(encoding="utf-8"))


def configure(ui, slug: str = "uir", **over) -> None:
    """Merge `over` into a routine's routine.yaml, as a hand edit would."""
    path = ui.routines / slug / "routine.yaml"
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    cfg.update(over)
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")


def start_conversation(ui, page, text: str) -> tuple[str, Path]:
    """Start a conversation from the composer with `text` as its first message, the way a
    person does (the composer posts and routes). Returns its slug and its directory, once the
    page has moved to it."""
    page.goto(f"{ui.url}/#/conversations")
    page.locator(".conv-new textarea").fill(text)
    page.get_by_role("button", name="start conversation").click()
    page.wait_for_url("**/conversations/**")
    slug = page.url.rsplit("/", 1)[-1]
    return slug, ui.conversations / slug


def lane_members(ui, lane_id: str) -> list[str]:
    """The slugs a lane holds, in fire order, as the lane store has them."""
    rec = lanes.get(ui.routines, lane_id)
    return [m["slug"] for m in (rec["members"] if rec else [])]


def hold_requests(page, pattern) -> list:
    """Route `pattern` and HOLD every request it matches: each one waits, unanswered, in the
    returned list until the test continues or fulfils it."""
    held = []

    def hold(route):        # Playwright wraps a Python function, never a bound builtin
        held.append(route)

    page.route(pattern, hold)
    return held


def watch_requests(page, pattern) -> list[str]:
    """Collect the URL of every request from now on that `pattern` (a compiled regex) finds.
    Returns the list, which keeps filling."""
    seen: list[str] = []
    page.on("request", lambda r: seen.append(r.url) if pattern.search(r.url) else None)
    return seen


#: A desktop noVNC with NOTHING behind it (port 1 refuses at once), like the browser tests'
#: DEAD_SCREEN: a preview then settles on "screen unreachable" instead of waiting out a timeout.
DEAD_DESKTOP = "http://127.0.0.1:1/vnc.html"


def desktop(name: str, vnc: str, *, idle_s: int = 5, ready: bool = True,
            folders: list[dict] | None = None) -> dict:
    """One `/fleet` row as the broker reports it."""
    return {"name": name, "slot": 0, "ready": ready, "stopping": False, "up_s": 300,
            "idle_s": idle_s, "shares": [f["name"] for f in folders or []], "vnc": vnc,
            "folders": folders or []}


class FakeBroker:
    """The desktop broker's two operator operations, answered from a list the test edits.

    It replaces `api_desktops._broker` — the one outbound hop — so the console's real route,
    owner mapping and token filtering all run; only the sidecar is absent.
    """

    def __init__(self, rows: list[dict]):
        self.desktops = list(rows)
        self.stopped: list[str] = []

    async def __call__(self, _request, op: str, body: dict) -> dict:
        if op == "fleet":
            return {"slots": 2, "idle_limit_s": 1200, "desktops": list(self.desktops)}
        self.stopped.append(body["name"])
        self.desktops = [d for d in self.desktops if d["name"] != body["name"]]
        return {"stopped": True}


def run_desktops(ui, monkeypatch, *rows: dict) -> FakeBroker:
    """Point the fixture console at a desktop service running `rows`."""
    from rsched.web import api_desktops

    broker = FakeBroker(list(rows))
    monkeypatch.setattr(api_desktops, "_broker", broker)
    ui.server_cfg.desktop_broker_url = "http://127.0.0.1:1"
    ui.server_cfg.desktop_view_url = DEAD_DESKTOP
    return broker
