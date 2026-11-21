"""Fix links on the effective surface — the panel that diagnoses a dependency handing the
reader the panel that owns it.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml
from playwright.sync_api import expect

from helpers import write_util
from rsched import lanes, secrets
from rsched.config import MachineConfig
from rsched.oauth import store as oauth_store
from rsched.oauth.store import Connection
from rsched.readmodels.remedies import REMEDIES

from .helpers import GATE_ROW, configure, seed_gate, seed_overhead, stored_config, unfold

# What an unmet row carries, in two parts. The FIX line is the offer as a whole — `data-fix`
# holds the server's `kind`, which is the vocabulary the halves have to agree on, so it is
# asserted by name. CONTROL is the thing you press inside it: a button that jumps within this
# page, or a link that leaves for the panel elsewhere that owns the value.
FIX = "[data-fix]"
CONTROL = ".fix-link"

# Everything a satisfied row must NOT have. Written as the union rather than as one selector:
# the claim is that there is nothing to click, not that one spelling of it is absent.
CLICKABLE = "[data-fix], .fix-link, a, button"

# The two readers of one join: the strip above the hero and the full panel further down.
STRIP = "[data-setup-check]"
PANEL = "[data-surface-view]"

# Where an in-page fix aims. Every config section is built through settingsSection's
# { title, id } form, so `sec-permissions` is an address that outlives a retitled heading; an
# ability card is addressed by the doc slug abilities.js stamps on it.
SECRET_EXPOSURE = "#sec-secret-exposure"
SCHEDULE = "#sec-schedule"
MACHINES = "#sec-machines"
FS_ROOTS = "#sec-fs-roots"
CONNECTIONS = "#sec-connections"
SECRETS_SECTION = "#sec-secrets"         # Settings' own secrets section — where a journey lands
ABILITY = '.ability[data-ability="needs-shell"]'

# The CONTROLS those panels own, each addressed as the one thing that performs one act rather
# than as "a control in the right region": a card holds several — `.ability select` counted
# every one of them — so the day a second dial lands in that card, the assertion reds with a
# message about the wrong control.
SWITCH = f"{ABILITY} [data-switch-on]"      # abilities.js: the card's own switch
SCHED_FREQ = f"{SCHEDULE} + .panel div.row > select"          # the cadence select
SCHED_CLEAR = f"{SCHEDULE} + .panel [data-clear-cron]"
ACCEPT = ".accept-bar [data-accept]"           # the settings page's ONE save

_STATIC = Path(__file__).resolve().parents[2] / "static"

# A flash is a class put on for a second or two and taken off again, so it is read by name
# FRAGMENT rather than by one spelling. WHERE it lands is the whole question; the two
# relations answer different claims: `on` means a flashed node IS the target or sits inside it
# ("you are looking at the control"), `around` means a flashed node CONTAINS it ("the region
# holding it lit up"). Landing on a whole panel when the row named one card inside it satisfies
# `around` and fails `on` — which is exactly the difference this file has to be able to state.
FLASH = """(sel) => {
  const target = document.querySelector(sel);
  if (!target) return null;
  const flashed = [...document.querySelectorAll('[class]')].filter(
    (n) => [...n.classList].some((c) => c.toLowerCase().includes('flash')));
  if (!flashed.length) return null;
  return { classes: flashed.map((n) => [...n.classList].join(' ')),
           on: flashed.some((n) => target.contains(n)),
           around: flashed.some((n) => n.contains(target)) };
}"""

ANY_FLASH = """() => [...document.querySelectorAll('[class]')].filter(
  (n) => [...n.classList].some((c) => c.toLowerCase().includes('flash'))).length"""

# In view = the reader is looking at it. Asserted on the geometry rather than on a screenshot:
# what they need is the control in front of them, by whichever scroll. A block TALLER than the
# window can never satisfy "wholly inside", so for that one the top edge being on screen is what
# arriving means.
IN_VIEW = """(sel) => {
  const n = document.querySelector(sel);
  if (!n) return false;
  const r = n.getBoundingClientRect();
  const h = window.innerHeight;
  return r.height > 0 && r.top >= 0 && r.top < h && (r.bottom <= h || r.height > h);
}"""

# What a person can actually operate. A `div` styled to look pressable answers `to_be_enabled`
# — that predicate is about the `disabled` property, which a div does not have — so the tag is
# asked for as well, or a landing could satisfy every assertion here while carrying nothing.
CONTROL_KIND = """(n) => {
  const tag = n.tagName.toLowerCase();
  if (tag === 'a') return n.getAttribute('href') ? 'a' : 'a (no href)';
  return tag;
}"""
OPERABLE = ("button", "input", "select", "textarea", "a")


def _rule(ui, slug: str, expects: dict) -> None:
    """A library RULE declaring a soft edge. `expects:` is legal on a rule where `requires:` is
    not, so this is the only way a `connection:` need reaches the surface at all.
    """
    meta = yaml.safe_dump({"effect": {"with": "binds the account the prose assumes",
                                      "without": "reaches the provider as nobody",
                                      "when": "the work runs as an account you own"},
                           "tags": ["test"], "expects": expects}, allow_unicode=True)
    (ui.server_cfg.rules_home / f"{slug}.md").write_text(
        f"---\n{meta}---\n# rule: {slug} — a fixture rule\n\nBind the account first.\n",
        encoding="utf-8")


def _permission(ui, slug: str, *, requires: dict | None = None,
                expects: dict | None = None) -> None:
    """A library CONDUCT DOC of the shape a case needs, written into the fixture library rather
    than borrowed from the seed: the seed's catalogue changes with the product; what these
    tests pin is how the surface and the page treat a doc of this SHAPE — one whose required
    action is not switched on, one that expects a machine it names no instance of.
    """
    meta = {"effect": {"with": "does the thing this fixture stands for",
                       "without": "does not", "when": "a test needs a doc of this shape"},
            "tags": ["test"], "requires": requires or {},
            **({"expects": expects} if expects else {})}
    (ui.server_cfg.permissions_home / f"{slug}.md").write_text(
        f"---\n{yaml.safe_dump(meta, allow_unicode=True)}---\n"
        f"# permission: {slug} — a fixture doc\n\nFixture conduct.\n", encoding="utf-8")


def _hold_needs_shell(ui) -> None:
    """Held: a doc requiring the `shell` action, with the action switched off — a file edited by
    hand, since every save raises the mapping to cover what a held doc requires."""
    _permission(ui, "needs-shell", requires={"actions": ["shell"]})
    _configure(ui, permissions=["needs-shell"])


def _hold_remote_machines(ui) -> None:
    """Held: a doc that expects "a machine" — any one — and none bound."""
    _permission(ui, "remote-machines", expects={"machine": ["*"]})
    _configure(ui, permissions=["remote-machines"])


def _configure(ui, *, permissions=(), capabilities=None, **over) -> None:
    """Rewrite the fixture routine's config. Both layers are written EXPLICITLY every time:
    leaving either implicit means the model's defaults apply (write_util plus the doc that
    covers it), which adds rows these tests are not about — one gap, one row.
    """
    configure(ui, permissions=list(permissions),
              capabilities={"actions": [], "utils": [], **(capabilities or {})}, **over)


def _open(ui_page, ui):
    """The routine page at a laptop viewport. The size is realism, NOT a precondition: nothing
    here asserts what happens to be above or below the fold at this height."""
    ui_page.set_viewport_size({"width": 1100, "height": 620})
    ui_page.goto(f"{ui.url}/#/routine/uir")
    unfold(ui_page)


def _row(ui_page, ui, entity: str):
    """Open the routine page and return the effective-surface row for one entity."""
    _open(ui_page, ui)
    row = ui_page.locator(f'{PANEL} [data-surface-row="{entity}"]')
    row.wait_for(state="visible")
    return row


def _strip_row(ui_page, entity: str):
    """The same node as the STRIP renders it — above the hero, where a failure is read first."""
    strip = ui_page.locator(STRIP)
    strip.wait_for(state="attached")
    if strip.evaluate("(n) => n.tagName === 'DETAILS' && !n.open"):
        strip.locator("summary").click()
    row = ui_page.locator(f'{STRIP} .setup-row[data-entity="{entity}"]')
    row.wait_for(state="visible")
    return row


def _wording(offer) -> str:
    """What the control puts in front of the reader — its text and whatever a hover adds."""
    return offer.evaluate(
        "(n) => [n.textContent, n.title, n.getAttribute('aria-label')].filter(Boolean).join(' ')")


def _lands_on(ui_page, control, selector: str) -> dict:
    """Press `control` and prove the reader ends up looking at `selector`."""
    control.scroll_into_view_if_needed()
    settled = ui_page.evaluate(IN_VIEW, selector)
    start = ui_page.evaluate("() => window.scrollY")
    control.click()
    flash = ui_page.wait_for_function(FLASH, arg=selector).json_value()
    ui_page.wait_for_function(IN_VIEW, arg=selector)
    moved = ui_page.evaluate("() => window.scrollY") != start
    assert settled or moved, (
        f"the press neither moved the page nor found {selector} already on screen")
    return flash


def _operable(control, act: str) -> None:
    """`control` is ONE thing a person can press; it is not greyed out."""
    expect(control).to_have_count(1)
    kind = control.evaluate(CONTROL_KIND)
    assert kind in OPERABLE, f"{act}: the landing carries a <{kind}>, which nobody can operate"
    expect(control).to_be_enabled()


def _orphan_row(entity: str) -> str:
    """The ability panel's row for a capability no held conduct doc requires. Addressed by the
    FULL entity id, class included, because `util:spawn` and `action:spawn` are two different
    things to hold — and carried by EVERY row of that card, the ones this panel cannot act on
    included, so a reader who arrives always lands on something that says where the act lives.
    """
    return f'[data-orphan="{entity}"]'


def _drop_control(page, entity: str):
    """The button that switches that capability off. abilities.js stamps it ONLY where pressing
    it does something real, which is what makes its absence an assertable claim rather than a
    guess about markup.
    """
    return page.locator(f'[data-drop="{entity}"]')


def _settings_post(request) -> bool:
    return request.method == "POST" and request.url.endswith("/routines/uir/settings")


# ---- the binding: every kind the console renders lands on a control that performs it --------
# One row per `fix` kind: the state that makes the server emit it, the ONE control. `landing` is
# plain CSS (the flash and viewport check use `document.querySelector`); `control` a locator, so
# it may name a label. A kind nothing can perform carries neither: prose, not a dead link.


@dataclass(frozen=True)
class Case:
    kind: str                                   # the server's `fix.kind` — the shared vocabulary
    entity: str                                 # the surface row it rides
    seed: Callable[[object, object], None]      # the routine state that produces that row
    control: Callable[[object], object] | None  # what performs the act, or None for prose
    landing: str = ""                           # plain CSS proving the reader arrived
    route: str = ""                             # the hash route an off-page fix travels to
    arrive: str = ""                            # plain CSS that must exist once it lands


def _seed_cover_or_drop(ui, mp) -> None:
    write_util(ui.server_cfg, "poster")
    _configure(ui, capabilities={"utils": ["poster"]})


def _seed_grant(ui, mp) -> None:
    secrets.set_secret("UI_FIX_TOKEN", "v")
    write_util(ui.server_cfg, "poster", secrets="UI_FIX_TOKEN")
    _configure(ui, capabilities={"utils": ["poster"]})


def _seed_add_secret(ui, mp) -> None:
    write_util(ui.server_cfg, "poster", secrets="UI_FIX_TOKEN")
    _configure(ui, capabilities={"utils": ["poster"]})


def _seed_add_root(ui, mp) -> None:
    write_util(ui.server_cfg, "store", fs="rw /srv/fix-store")
    _configure(ui, capabilities={"utils": ["store"]})


def _seed_bind_machine(ui, mp) -> None:
    mac = MachineConfig(host="10.0.0.9", user="rsched", description="RTX 4090", tags=["gpu"])
    mac.name = "gpu-box"
    # A catalog with a box in it: an EMPTY catalog is the one state where the Machines panel
    # honestly has nothing to bind, which the row's own effect line says out loud.
    ui.server_cfg.machines = {"gpu-box": mac}
    _hold_remote_machines(ui)


def _seed_bind_connection(ui, mp) -> None:
    mp.setattr(oauth_store, "connections_path", lambda: ui.tmp / "connections.json")
    oauth_store.set_connection(Connection(provider="notion", account="acme", access_token="AT"))
    _rule(ui, "fixture-connection", {"connection": ["notion"]})
    _configure(ui, rules=["fixture-connection"])


def _seed_install_util(ui, mp) -> None:
    _configure(ui, capabilities={"utils": ["ghost-util"]})


def _seed_set_schedule(ui, mp) -> None:
    _configure(ui, schedule={"cron": "", "tz": "Europe/Berlin"})


def _seed_lane_schedule(ui, mp) -> None:
    lanes.create(ui.routines, name="Nightly", members=[{"slug": "uir"}], cron="0 3 * * *")
    _configure(ui)


def _seed_fix_phase(ui, mp) -> None:
    _configure(ui)
    ui.seed_run("uir", "20260901-070000", "done", summary="done")


def _write_root_add(page):
    """The add control in the WRITE half of the roots editor — addressed by the label beside it,
    because the two editors are the same component twice."""
    return page.locator(f"{FS_ROOTS} + .panel .field").filter(
        has_text="write roots").locator("button")


def _needed_secret_set(page):
    """Settings → Secrets lists what the installed utils declare; the row for an unset one
    carries the control that sets it."""
    return page.locator("tr").filter(
        has=page.locator('[data-secret-status="UI_FIX_TOKEN"]')).locator("button")


CASES = (
    Case("switch_on", "permission:needs-shell", lambda ui, _: _hold_needs_shell(ui),
         landing=ABILITY, control=lambda p: p.locator(SWITCH)),
    Case("cover_or_drop", "util:poster", _seed_cover_or_drop,
         landing=_orphan_row("util:poster"),
         control=lambda p: _drop_control(p, "util:poster")),
    Case("grant", "secret:UI_FIX_TOKEN", _seed_grant,
         landing='[data-secret-row="UI_FIX_TOKEN"]',
         control=lambda p: p.locator('[data-secret-row="UI_FIX_TOKEN"] select')),
    Case("add_secret", "secret:UI_FIX_TOKEN", _seed_add_secret,
         route="#/settings?section=secrets", arrive=SECRETS_SECTION,
         control=_needed_secret_set),
    Case("add_root", "fs-write:/srv/fix-store", _seed_add_root,
         landing=FS_ROOTS, control=_write_root_add),
    Case("bind_machine", "machine:*", _seed_bind_machine,
         landing=MACHINES,
         control=lambda p: p.locator(
             f'{MACHINES} + .panel label:has-text("gpu-box") input[type=checkbox]')),
    Case("bind_connection", "connection:notion", _seed_bind_connection,
         landing=f'{CONNECTIONS} + .panel [data-conn-row="notion"]',
         control=lambda p: p.locator(f'{CONNECTIONS} + .panel [data-conn-row="notion"] select')),
    # A util the library does not have is written by a RUN, never by hand — so the half of this
    # offer a person can perform is switching it off, which is on this page.
    Case("install_util", "util:ghost-util", _seed_install_util,
         landing=_orphan_row("util:ghost-util"),
         control=lambda p: _drop_control(p, "util:ghost-util")),
    Case("set_schedule", "schedule:none", _seed_set_schedule,
         landing=SCHEDULE, control=lambda p: p.locator(SCHED_FREQ)),
    # The cadence select is the LANE's here, so it is disabled by design; the act this row names
    # is clearing the cron the lane overrides, which needs a control of its own.
    Case("lane_schedule", "schedule:cron", _seed_lane_schedule,
         landing=SCHED_CLEAR, control=lambda p: p.locator(SCHED_CLEAR)),
    # Nothing on any page records a phase — the recipe does, at its next run. Words, no link.
    Case("fix_phase", "state:phase", _seed_fix_phase, control=None),
    Case("declare_outcome", "recipe:outcome", lambda ui, _: _configure(ui, outcome=None),
         control=None),
    Case("revise_recipe", "recipe:overhead", lambda ui, _: seed_overhead(ui), control=None),
    Case("edit_gate", "gate:queue", lambda ui, _: seed_gate(ui), landing=GATE_ROW,
         control=lambda p: p.locator(f"{GATE_ROW} button.danger")),
)


def _console_fix_kinds() -> set[str]:
    """The kind vocabulary the console can render, read off the map that owns it."""
    src = (_STATIC / "components" / "surface-view.js").read_text(encoding="utf-8")
    block = re.search(r"^const FIX = \{$(.+?)^\};$", src, re.DOTALL | re.MULTILINE)
    assert block, "surface-view.js no longer declares the FIX map as `const FIX = {` … `};`"
    kinds = set(re.findall(r"^  (\w+): \(", block.group(1), re.MULTILINE))
    assert kinds, "no kinds parsed from the FIX map — keep one key per line, indented two spaces"
    return kinds


def test_no_fix_kind_reaches_the_console_without_a_case_here():
    """The guard that ends the pattern. A kind is added in three places — the server emits it,
    `remedies.py` words it for a terminal, `surface-view.js` maps it to a panel — and the fourth
    is the only one that asks whether the panel can perform the act. Adding the kind without
    adding its row here is what let a link ship three times to a place with no control.
    """
    console = _console_fix_kinds()
    covered = {c.kind for c in CASES}
    assert console == covered, (
        "every kind the console renders needs a case naming the control that performs it — "
        f"unexercised: {sorted(console - covered)}; gone from the console: "
        f"{sorted(covered - console)}")
    # …and the two audiences say the same set of things. A kind the CLI words and the console
    # ignores is a remedy an operator gets on a terminal and not on the page that owns the dial.
    served = {k.split(":")[0] for k in REMEDIES}     # `:any` is one kind's wording, not a kind
    assert console == served, (
        "the console and readmodels/remedies.py disagree about the fix vocabulary — "
        f"console only: {sorted(console - served)}; remedies only: {sorted(served - console)}")


@pytest.mark.parametrize("case", CASES, ids=[c.kind for c in CASES])
def test_every_fix_kind_lands_on_a_control_that_can_perform_it(ui, ui_page, monkeypatch, case):
    """One row, one act, one control that is there and operable."""
    case.seed(ui, monkeypatch)
    row = _row(ui_page, ui, case.entity)
    offer = row.locator(FIX)
    expect(offer).to_have_count(1)
    expect(offer).to_have_attribute("data-fix", case.kind)

    if case.control is None:
        # Prose, deliberately: an act with no control anywhere is stated, never linked.
        expect(row.locator(CONTROL)).to_have_count(0)
        expect(offer.locator(".fix-act")).to_have_count(1)
        assert _wording(offer).strip(), f"{case.kind} renders an empty offer"
        return

    control = row.locator(CONTROL).first
    # read before the press: a fix that travels leaves the page, taking the node with it
    act = _wording(control)
    if case.route:
        base = case.route.split("?")[0]
        expect(control).to_have_attribute("href", case.route)
        assert base.lstrip("#/") in act.lower(), (
            f"a fix that leaves the page has to say where it goes: {act!r}")
        control.click()
        ui_page.wait_for_function("(h) => location.hash.startsWith(h)", arg=base)
        ui_page.wait_for_selector(case.arrive)
    else:
        ui_page.wait_for_selector(case.landing, state="attached")
        flash = _lands_on(ui_page, control, case.landing)
        assert flash["on"] or flash["around"], (
            f"{case.kind} flashed something unrelated to its control: {flash['classes']}")
    _operable(case.control(ui_page), f"{case.kind} — {act}")


# ---- the three acts this round made performable ---------------------------------------------


def test_a_capability_this_routine_owns_can_be_switched_off_where_the_row_says(ui, ui_page):
    """`cover_or_drop` offers "switch it off". Until now nothing switched a capability off: the
    orphan card listed uncovered capabilities read-only; dropping one happened as an
    invisible side effect of a save — the server's floor strips a capability no held doc
    requires. The act was reachable, by a control that did not say so.
    """
    write_util(ui.server_cfg, "poster")
    _configure(ui, capabilities={"utils": ["poster"]})

    row = _row(ui_page, ui, "util:poster")
    expect(row.locator(".setup-sev")).to_have_text("note")
    expect(row.locator(FIX)).to_have_attribute("data-fix", "cover_or_drop")
    _lands_on(ui_page, row.locator(CONTROL).first, _orphan_row("util:poster"))

    drop = _drop_control(ui_page, "util:poster")
    _operable(drop, "drop a capability no held doc requires")
    drop.click()
    with ui_page.expect_request(_settings_post) as sent:
        ui_page.locator(ACCEPT).click()
    caps = sent.value.post_data_json["changes"]["capabilities"]
    assert "poster" not in (caps.get("utils") or []), (
        f"the press changed nothing the accept carried: {caps}")
    expect(ui_page.locator("#toast:not([hidden])")).to_contain_text("accepted")
    assert "poster" not in (stored_config(ui)["capabilities"].get("utils") or [])


def test_a_missing_util_offers_the_half_a_person_can_perform(ui, ui_page):
    """A util the library does not have is written by a RUN through write_util. The Library page
    offers "+ new" for rules and permissions and none for utils, so an offer to go there and
    write one is the third repetition of this file's whole subject: a link to a place where the
    act cannot be performed.
    """
    _configure(ui, capabilities={"utils": ["ghost-util"]})

    row = _row(ui_page, ui, "util:ghost-util")
    expect(row.locator(".setup-sev")).to_have_text("fails")
    offer = row.locator(FIX)
    expect(offer).to_have_attribute("data-fix", "install_util")
    # nothing here travels: the Library cannot author a util, so no control claims it can
    expect(row.locator(".fix-link.fix-away")).to_have_count(0)
    expect(row.locator(CONTROL)).to_have_count(1)
    assert "run" in offer.inner_text().lower(), (
        f"the offer does not say who writes the util: {offer.inner_text()!r}")

    _lands_on(ui_page, row.locator(CONTROL), _orphan_row("util:ghost-util"))
    _operable(_drop_control(ui_page, "util:ghost-util"), "stop holding a util nothing can write")


def test_a_lane_suppressed_cron_can_actually_be_cleared(ui, ui_page):
    """`lane_schedule` leads with "clear this routine's cron" and lands on the Schedule panel,
    whose cadence select is disabled in exactly the state that emits the row — a lane's schedule
    suppresses every member's own cron, so the select is the lane's to set and the page's save
    sends no schedule at all. Clearing the stale line needs its own control; the file is
    where the act has to show up.
    """
    lanes.create(ui.routines, name="Nightly", members=[{"slug": "uir"}], cron="0 3 * * *")
    _configure(ui)

    row = _row(ui_page, ui, "schedule:cron")
    _lands_on(ui_page, row.locator(CONTROL).first, SCHED_CLEAR)
    # The state the row is complaining of: the lane owns the cadence, so the select offers no
    # cadence to pick — only "Lane managed" plus the Disabled escape (F448), which switches the
    # routine off WITHOUT rewriting the cron the lane suppresses. It is therefore no longer
    # `disabled` as an element; what makes the cron unclearable from here is the absent cadence.
    freq = ui_page.locator(SCHED_FREQ)
    expect(freq).to_have_value("lane-managed")
    assert sorted(freq.locator("option").all_text_contents()) == ["Disabled", "Lane managed"], (
        "a lane member's cadence select must offer only the lane-managed state and the "
        f"Disabled escape: {freq.locator('option').all_text_contents()}")

    clear = ui_page.locator(SCHED_CLEAR)
    _operable(clear, "clear a cron the lane suppresses")
    clear.click()
    expect(ui_page.locator("#toast:not([hidden])")).to_contain_text("cleared")
    assert not (stored_config(ui).get("schedule") or {}).get("cron"), (
        f"the cron the lane overrides is still in the file: {stored_config(ui).get('schedule')}")


# ---- the rows themselves ---------------------------------------------------------------------


def test_a_failing_row_offers_the_switch_it_named(ui, ui_page):
    """The case the affordance was asked for: a held conduct doc whose required action is off.
    The row diagnoses it precisely and the switch is in a card further down the same page, which
    the reader had to know unaided.
    """
    _hold_needs_shell(ui)
    row = _row(ui_page, ui, "permission:needs-shell")
    expect(row.locator(".setup-sev")).to_have_text("fails")
    expect(row).to_contain_text("shell")              # the diagnosis names the missing switch
    offer = row.locator(FIX)
    expect(offer).to_have_count(1)
    expect(offer).to_have_attribute("data-fix", "switch_on")
    # …and so does the offer: "switch it on" over an unnamed capability sends the reader
    # hunting through a panel of toggles for the one the row was talking about.
    assert "switch on shell" in _wording(offer), (
        f"the fix does not name the missing capability: {_wording(offer)!r}")


def test_the_offer_lands_on_the_control_not_the_top_of_the_panel(ui, ui_page):
    """Clicking is the whole point; WHERE it puts you is the difference between an answer and
    another search. The permissions panel is a column of ability cards; flashing the lot of them
    hands back the hunt the row had just ended. The failing card carries its own badge and the
    switch inside it, so that is where the press lands — and pressing it stages exactly the
    capability the row named, for the page's one accept.
    """
    _hold_needs_shell(ui)
    row = _row(ui_page, ui, "permission:needs-shell")
    ui_page.wait_for_selector(ABILITY)

    flash = _lands_on(ui_page, row.locator(CONTROL), ABILITY)
    assert flash["on"], (
        "the flash lit a region around the failing ability rather than the ability itself: "
        f"{flash['classes']}")
    # the control really is what you arrived at: the switch in that card, asked for by the row
    # it sits in rather than as "a button somewhere inside the card"
    switch = ui_page.locator(SWITCH)
    _operable(switch, "switch on what the held doc requires")
    assert ui_page.evaluate("() => location.hash").startswith("#/routine/uir")
    switch.click()
    with ui_page.expect_request(_settings_post) as sent:
        ui_page.locator(ACCEPT).click()
    assert "shell" in sent.value.post_data_json["changes"]["capabilities"]["actions"]


def test_a_satisfied_row_offers_nothing_to_click(ui, ui_page):
    """A met row is a statement, not a task. The panel's worth is that it reads without touching
    anything; one clickable row in a column of settled ones re-opens every question it just
    closed.
    """
    store = ui.tmp / "ok-store"
    store.mkdir()
    write_util(ui.server_cfg, "okstore", fs=f"rw {store}")
    _configure(ui, capabilities={"utils": ["okstore"]}, fs_write_roots=[str(store)])

    row = _row(ui_page, ui, f"fs-write:{store}")
    assert "sev-ok" in (row.get_attribute("class") or "")
    expect(row.locator(".setup-sev")).to_have_text("ok")
    expect(row.locator(CLICKABLE)).to_have_count(0)


def test_a_fix_that_lives_elsewhere_says_so_and_goes_there(ui, ui_page):
    """A secret is added to the INSTANCE store, which no panel on this page holds. An in-page
    jump would scroll to nothing, so the row reads as a journey and makes it — landing on the
    section that owns the store rather than at the top of Settings.
    """
    write_util(ui.server_cfg, "poster", secrets="UI_FIX_TOKEN")
    _configure(ui, capabilities={"utils": ["poster"]})

    row = _row(ui_page, ui, "secret:UI_FIX_TOKEN")
    expect(row.locator(".setup-sev")).to_have_text("fails")
    offer = row.locator(FIX)
    expect(offer).to_have_attribute("data-fix", "add_secret")
    assert "settings" in _wording(offer).lower(), (
        f"a fix that leaves the page has to say where it goes: {_wording(offer)!r}")

    # A journey, not a jump: a link to a route whose landing is the section owning the store
    # rather than the top of Settings.
    control = row.locator(CONTROL)
    expect(control).to_have_attribute("href", "#/settings?section=secrets")
    control.click()
    ui_page.wait_for_function("() => location.hash.startsWith('#/settings')")
    ui_page.wait_for_selector(SECRETS_SECTION)


def test_an_offer_for_one_of_a_class_never_prints_the_asterisk(ui, ui_page):
    """`expects: machine: ["*"]` is a doc saying "at least one of this class" — the prose naming
    which one lives in the doc body, never in the key. `remote-machines` declares exactly that,
    so every routine holding it with nothing bound read "bind *": a glyph out of the read model's
    internals, addressed to nobody, in the one line on the row that is supposed to be an
    instruction.
    """
    _hold_remote_machines(ui)
    row = _row(ui_page, ui, "machine:*")
    expect(row.locator(".setup-sev")).to_have_text("interrupts")
    offer = row.locator(FIX)
    expect(offer).to_have_attribute("data-fix", "bind_machine")

    words = _wording(offer)
    assert "*" not in words, f"the wildcard reached the screen: {words!r}"
    assert "machine" in words.lower(), (
        f"the offer names neither a machine nor the class it wants one from: {words!r}")
    # and it still goes somewhere: an unnamed need is not an unactionable one
    _lands_on(ui_page, row.locator(CONTROL), MACHINES)


def test_a_withheld_secret_is_a_statement_with_nothing_to_press(ui, ui_page):
    """F609: a secret the operator DECLINED is his settled decision, so its row says so and
    offers nothing.
    """
    secrets.set_secret("UI_FIX_TOKEN", "v")
    write_util(ui.server_cfg, "poster", secrets="UI_FIX_TOKEN")
    _configure(ui, capabilities={"utils": ["poster"]},
               grants={"secret:UI_FIX_TOKEN": False})

    row = _row(ui_page, ui, "secret:UI_FIX_TOKEN")
    expect(row.locator(".setup-sev")).to_have_text("note")
    expect(row.locator(FIX)).to_have_count(0)
    expect(row.locator(CONTROL)).to_have_count(0)
    # the row still SAYS what his decision costs — demoting it is not silencing it
    assert "declined" in row.inner_text().lower()

    # …and the dial he would use to change his own mind is where it always was, at "withhold"
    dial = ui_page.locator('[data-secret-row="UI_FIX_TOKEN"] select')
    expect(dial).to_have_value("false")
    expect(ui_page.locator(SECRET_EXPOSURE)).to_have_count(1)


def test_a_lane_suppressed_cron_offers_this_routines_own_schedule(ui, ui_page):
    """The row's complaint is that THIS file records a cron it will never fire at, because its
    lane's schedule wins. Two things settle that; they are not equals: clearing this
    routine's cron makes the file say what happens, while rescheduling the lane changes when
    every OTHER member fires — instance state, spent to repair one stale line in one config.
    The primary act is therefore the routine's own Schedule panel, on this page.
    """
    lane = lanes.create(ui.routines, name="Nightly", members=[{"slug": "uir"}], cron="0 3 * * *")
    _configure(ui, capabilities={"actions": ["write_recipe"]})

    row = _row(ui_page, ui, "schedule:cron")
    expect(row.locator(".setup-sev")).to_have_text("note")
    expect(row).to_contain_text("0 7 * * 1")          # what the file says
    expect(row).to_contain_text("0 3 * * *")          # what actually fires
    offer = row.locator(FIX)
    expect(offer).to_have_attribute("data-fix", "lane_schedule")

    # the PRIMARY act stays on this page and is this routine's own schedule; whether the lane is
    # offered after it is a judgement call the row is free to make
    primary = row.locator(CONTROL).first
    assert "fix-away" not in (primary.get_attribute("class") or ""), (
        "the first thing offered leaves for instance state shared with every other member")
    expect(primary).to_have_attribute("data-fix-section", "sec-schedule")
    _lands_on(ui_page, primary, SCHEDULE)

    # the row in the same table that is NOT unmet and therefore says nothing
    settled = ui_page.locator(f'{PANEL} [data-surface-row="action:write_recipe"]')
    expect(settled.locator(".setup-sev")).to_have_text("note")
    expect(settled.locator(FIX)).to_have_count(0)
    expect(settled.locator(CONTROL)).to_have_count(0)

    # …and the SECOND offer, which leaves for the page that owns the lane, arrives at the
    # control that reschedules it — the same claim the primary makes, on the other surface.
    alt = row.locator(CONTROL).nth(1)
    if alt.count():
        assert (alt.get_attribute("href") or "").startswith("#/routines")
        alt.click()
        ui_page.wait_for_function("() => location.hash.startsWith('#/routines')")
        _operable(ui_page.locator(f'tr[data-lane-row="{lane["id"]}"] [data-lane-edit]'),
                  "reschedule the lane")


def test_the_strip_offers_the_same_act_as_the_panel(ui, ui_page):
    """The strip is the surface an operator reads FIRST — it sits above the hero and shows only
    what is unmet. Rendering the diagnosis there and the way out six collapsed groups further
    down abandons exactly the reader the strip was built for.
    """
    _hold_needs_shell(ui)
    _open(ui_page, ui)

    strip = _strip_row(ui_page, "permission:needs-shell")
    panel = ui_page.locator(f'{PANEL} [data-surface-row="permission:needs-shell"]')
    panel.wait_for(state="visible")

    up = strip.locator(FIX)
    expect(up).to_have_count(1)
    expect(up).to_have_attribute("data-fix", "switch_on")
    # Compared on the CONTROL's own words rather than on the rendered line, so the claim is about
    # what the two say and not about how compactly either lays it out.
    here, there = _wording(strip.locator(CONTROL)), _wording(panel.locator(CONTROL))
    assert here == there, f"the two readings of one row disagree: {here!r} vs {there!r}"

    # and it is an affordance, not a caption: pressing it from the strip lands where the panel's
    # copy lands
    flash = _lands_on(ui_page, strip.locator(CONTROL), ABILITY)
    assert flash["on"], (
        f"the strip's offer flashed something other than the failing ability: {flash['classes']}")


def test_the_strip_stays_silent_about_a_row_that_is_not_unmet(ui, ui_page):
    """The strip filters on "not ok", which is a WIDER test than "unmet": a NOTE reporting a
    deliberate switch reaches it too. That row carries no offer by design, so the strip has to
    read as a statement there rather than as a task somebody forgot to finish.
    """
    _configure(ui, capabilities={"actions": ["write_recipe"]})
    _open(ui_page, ui)

    strip = ui_page.locator(STRIP)
    expect(strip).to_be_visible()
    expect(strip.locator("summary")).to_contain_text("1 note")
    assert strip.evaluate("(n) => n.tagName === 'DETAILS' && !n.open"), \
        "a note-only strip must rest folded — a flag that is always up is a flag nobody reads"
    expect(strip.locator('.setup-row[data-entity="action:write_recipe"]')).to_be_hidden()

    # and opening it, the way a reader does, reaches the statement itself
    row = _strip_row(ui_page, "action:write_recipe")
    expect(row.locator(".setup-sev")).to_have_text("note")
    expect(row.locator(FIX)).to_have_count(0)
    expect(row.locator(CONTROL)).to_have_count(0)
    assert "rewrite its own instructions" in row.inner_text()


def test_a_fix_with_nowhere_to_land_disables_itself(ui, ui_page):
    """The no-dead-link guard — the thing that would have caught a fix aimed at the wrong
    panel if that panel had simply been absent. A control that scrolls to nothing leaves the
    reader unsure whether they missed the movement or the page did; a control that greys out and
    says where the destination is not tells them what happened.
    """
    _hold_needs_shell(ui)
    row = _row(ui_page, ui, "permission:needs-shell")
    ui_page.wait_for_selector(ABILITY)

    # take the whole destination away — heading and panel, so nothing the fix could name remains
    ui_page.evaluate("""() => {
      const h = document.getElementById('sec-permissions');
      h?.nextElementSibling?.remove();
      h?.remove();
    }""")

    control = row.locator(CONTROL)
    control.scroll_into_view_if_needed()
    start = ui_page.evaluate("() => window.scrollY")
    if control.is_enabled():
        control.click()

    expect(control).to_be_disabled()
    assert ui_page.evaluate("() => window.scrollY") == start, "the page moved to nothing"
    assert ui_page.evaluate(ANY_FLASH) == 0, "something flashed for a destination that is gone"
    assert "not on this page" in (control.get_attribute("title") or "").lower(), (
        f"a disabled offer has to say why: {control.get_attribute('title')!r}")
