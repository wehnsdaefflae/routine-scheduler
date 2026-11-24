"""D306 point 2 in the browser: the conversation rail's BRANCHES section. The operator kept
conversation branches and diagnosed their non-use as the console's fault — *"unused only because
the UI is bad"* — and this point names what was missing: branches were "a lineage LINE in the
header: names joined by commas, no state, no way to tell a branch working from one long finished".
So a row per relative carries its state chip, its fork turn and "handed back" once it has; the
section exists only when there IS a family (point 5 made the lineage read conditional and a
section reading it unconditionally would undo that), and it is fed by the header's own single
lineage read rather than fetching the same answer a second time.
"""

from __future__ import annotations

import json

import pytest
from playwright.sync_api import expect


def _conversation(ui, slug: str, *, title: str, parent: dict | None = None) -> None:
    """One conversation on disk, with a finished run so the catalog gives it a state."""
    d = ui.conversations / slug
    d.mkdir(parents=True, exist_ok=True)
    cfg = {"slug": slug, "name": title, "workflow": "converse"}
    if parent:
        cfg["parent"] = parent
    (d / "routine.yaml").write_text(json.dumps(cfg), encoding="utf-8")
    run = d / "runs" / "20261124-140000"
    run.mkdir(parents=True, exist_ok=True)
    (run / "transcript.jsonl").write_text(
        json.dumps({"type": "header", "run_id": f"{slug}:20261124-140000",
                    "routine": slug, "depth": 0}) + "\n", encoding="utf-8")
    (run / "status.json").write_text(
        json.dumps({"state": "finished", "turn": 1, "summary": "done"}), encoding="utf-8")


@pytest.fixture
def family(ui):
    """A parent with one branch, both on disk — the minority of conversations that have a
    family at all, which is the only case this section exists for."""
    _conversation(ui, "c-parent", title="The parent")
    _conversation(ui, "c-branch", title="A branch",
                  parent={"slug": "c-parent", "turn": 2, "forked": "20261124-140500"})
    return ui


def test_a_conversation_with_a_branch_shows_it_in_the_rail_with_its_state(family, ui_page):
    """The row a reader came for: the branch's name as a link, its STATE as the same chip every
    other surface uses, and the turn it was forked at — which is the whole of what the
    comma-joined header line could not say."""
    ui_page.goto(f"{family.url}/#/conversations/c-parent")
    expect(ui_page.locator('[data-rail="branches"]')).to_have_count(1)
    row = ui_page.locator('.branch-row[data-branch="c-branch"]')
    expect(row).to_have_count(1)
    expect(row.locator(".branch-name")).to_have_text("A branch")
    expect(row.locator(".branch-name")).to_have_attribute("href", "#/conversations/c-branch")
    expect(row).to_contain_text("finished")
    expect(row).to_contain_text("at turn 2")
    expect(row.locator(".branch-role")).to_have_text("branch")


def test_a_branch_shows_the_parent_it_came_from(family, ui_page):
    """Both directions, because a branch needs to offer the way back as readily as the way down —
    the row says "from" and links the parent, with the parent's own state beside it."""
    ui_page.goto(f"{family.url}/#/conversations/c-branch")
    row = ui_page.locator('.branch-row[data-branch="c-parent"]')
    expect(row).to_have_count(1)
    expect(row.locator(".branch-role")).to_have_text("from")
    expect(row.locator(".branch-name")).to_have_text("The parent")
    expect(row).to_contain_text("finished")


def test_a_handed_back_branch_says_so(family, ui_page):
    """"Handed back" is the state that matters most and the one the header line could never show:
    a branch that has finished its work and delivered it is done with, and a reader must not have
    to open it to find that out."""
    inbox = family.conversations / "c-parent" / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / "branch-c-branch-20261124-150000.json").write_text(
        json.dumps({"text": "what it concluded", "via": "branch"}), encoding="utf-8")
    ui_page.goto(f"{family.url}/#/conversations/c-parent")
    row = ui_page.locator('.branch-row[data-branch="c-branch"]')
    expect(row).to_contain_text("handed back")


def test_a_conversation_with_no_family_shows_nothing_and_asks_nothing(ui, ui_page):
    """The overwhelming majority, and the reason point 5 exists: a conversation with neither a
    parent nor a branch shows no branches section AND makes no `/lineage` call — the route reads
    every conversation's routine.yaml to answer, so asking is the expensive half. HIDDEN rather
    than absent is the house contract for a rail section with nothing to say — it is what
    `rail.toggle` does and what `background` has always done, and `test_conversation_rail.py`
    asserts `to_be_hidden()` for a toggled section against `to_have_count(0)` for one never added
    — so the claim is that neither caption nor row is VISIBLE, plus the request never made."""
    _conversation(ui, "c-alone", title="Alone")
    asked = []
    ui_page.on("request", lambda r: asked.append(r.url) if "/lineage" in r.url else None)
    ui_page.goto(f"{ui.url}/#/conversations/c-alone")
    ui_page.wait_for_selector(".conv-head")
    expect(ui_page.locator('.conv-view .rail-cap[data-rail="branches"]')).to_be_hidden()
    expect(ui_page.locator(".branch-row")).to_have_count(0)
    assert asked == [], f"a conversation with no family called /lineage: {asked}"


def test_the_section_is_fed_by_one_lineage_read_and_not_a_second_fetch(family, ui_page):
    """The section makes NO request of its own: the header's branch bar already reads `/lineage`
    exactly once per real family change (it holds a signature guard so the conversation list's
    own reload cannot turn that read into a poll), and a section fetching the same answer again
    would double every family's cost for nothing."""
    reads = []
    ui_page.on("request", lambda r: reads.append(r.url) if "/lineage" in r.url else None)
    ui_page.goto(f"{family.url}/#/conversations/c-parent")
    expect(ui_page.locator('.branch-row[data-branch="c-branch"]')).to_have_count(1)
    assert len(reads) == 1, f"the lineage was read {len(reads)} times for one view: {reads}"
