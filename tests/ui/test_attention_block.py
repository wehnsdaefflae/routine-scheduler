"""The attention block a reader actually sees (D289): his seven classes lead the run page, the
narrative follows, every row carries its own content rather than an identifier pointing at one,
and a run with nothing for him renders no block at all.
"""

from __future__ import annotations

import json

from playwright.sync_api import expect

from rsched.paths import atomic_write_json


def _run_with_attention(ui, slug: str, ts: str) -> None:
    """A finished run carrying a met goal, a partial finish and a health event."""
    run_dir = ui.seed_run(slug, ts, "finished", summary="the digest went out")
    status = json.loads((run_dir / "status.json").read_text(encoding="utf-8"))
    status.update({
        "outcome": "partial",
        "goals": [{"id": "b1", "text": "the weekly digest is published",
                   "status": "met", "evidence": "artifacts/digest-2026-w47.md, 1.4 kB"}],
    })
    atomic_write_json(run_dir / "status.json", status)
    stream = ui.routines / ".control" / "health-events.jsonl"
    stream.parent.mkdir(parents=True, exist_ok=True)
    stream.write_text(json.dumps({"run_id": f"{slug}:{ts}", "event": "endpoint_down",
                                  "detail": "the mail endpoint refused twice"}) + "\n",
                      encoding="utf-8")


def test_the_block_leads_the_run_page_with_his_classes_in_his_order(ui, ui_page):
    _run_with_attention(ui, "uir", "20261101-160000")
    ui_page.goto(f"{ui.url}/#/run/uir:20261101-160000")

    block = ui_page.locator(".att-block")
    expect(block).to_have_count(1)
    classes = block.locator(".att-row").evaluate_all(
        "rows => rows.map(r => r.dataset.attClass)")
    assert classes == sorted(classes), f"the server's order (a)…(g) was re-sorted: {classes}"
    assert classes[0] == "b" and "d" in classes and "e" in classes


def test_every_row_carries_its_own_content_not_an_identifier(ui, ui_page):
    """His requirement, verbatim: "worst case scenario is content i need to know about being
    referenced only by an identifier"."""
    _run_with_attention(ui, "uir", "20261101-161000")
    ui_page.goto(f"{ui.url}/#/run/uir:20261101-161000")

    block = ui_page.locator(".att-block")
    expect(block).to_have_count(1)
    expect(block).to_contain_text("the weekly digest is published")
    expect(block).to_contain_text("artifacts/digest-2026-w47.md")
    expect(block).to_contain_text("the mail endpoint refused twice")


def test_the_block_sits_above_the_narrative_not_below_it(ui, ui_page):
    _run_with_attention(ui, "uir", "20261101-162000")
    ui_page.goto(f"{ui.url}/#/run/uir:20261101-162000")

    expect(ui_page.locator(".att-block")).to_have_count(1)
    order = ui_page.evaluate(
        "() => { const b = document.querySelector('.att-block');"
        " const t = b.parentElement;"
        " return [...t.children].indexOf(b); }")
    assert order == 0, "the block must be the first thing in the transcript column"


def test_a_run_with_nothing_for_him_renders_no_block_at_all(ui, ui_page):
    """The block is a lead, not a frame: such a run must read exactly as it did before."""
    ui.seed_run("uir", "20261101-163000", "finished", summary="nothing of note")
    ui_page.goto(f"{ui.url}/#/run/uir:20261101-163000")

    ui_page.wait_for_selector(".turn, .finish-banner, .ev", state="attached")
    expect(ui_page.locator(".att-block")).to_have_count(0)
