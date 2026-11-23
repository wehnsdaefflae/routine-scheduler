"""D306 points 1 and 4 in the real console: the fork control is a LABELLED footer control every
reply carries at reading contrast, and one click on it asks for the branch's name and an
optional first instruction — which, given, starts the branch at once. The operator kept
conversation branches and diagnosed their non-use as the UI's fault (2026-11-21 code review,
"unused only because the UI is bad"): the one move branches needed people to find was a corner
glyph at opacity 0 until the bubble was hovered, beside three others, and invisible on a phone.
"""

from __future__ import annotations

import json

from playwright.sync_api import expect

from rsched.paths import atomic_write_json

from .conftest import TOKEN
from .helpers import start_conversation

ASK = "Weigh the two options."

#: Both inks as the BROWSER resolves them (`light-dark()` tokens, so the literal in base.css is
#: not what an element gets): a painted probe per token, read back as `color`.
_RESOLVED_INKS = """() => {
    const out = {};
    for (const token of ["--ink-2", "--ink-3"]) {
      const n = document.createElement("span");
      n.style.color = `var(${token})`;
      document.body.append(n);
      out[token] = getComputedStyle(n).color;
      n.remove();
    }
    return out;
}"""

#: header, one complete turn, and the finished REPLY a per-message fork control reads.
REPLY_EVENTS = [
    {"type": "header", "run_id": "X", "routine": "X",
     "workflow": {"slug": "converse", "commit": "abc", "version": 3}, "depth": 0},
    {"type": "assistant_action", "turn": 1, "usage": {"in": 9, "out": 1},
     "payload": {"kind": "read_file", "say": "orienting", "path": "state/plan.md"}},
    {"type": "observation", "turn": 1, "payload": {"kind": "read_file", "content": "PLAN"}},
    {"type": "finish", "turns": 1, "usage_total": {"in": 18, "out": 2},
     "payload": {"status": "ok", "summary": "Option B, on the cost curve."}},
]


def _with_a_reply(ui, ui_page):
    """A conversation whose transcript holds one finished reply — the fork point."""
    slug, conv_dir = start_conversation(ui, ui_page, ASK)
    run_dir = conv_dir / "runs" / "20260827-100000"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "transcript.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in REPLY_EVENTS), encoding="utf-8")
    atomic_write_json(run_dir / "status.json", {"state": "finished", "turn": 1})
    ui_page.reload()
    return slug, conv_dir


def _reply(page):
    reply = page.locator(".msg.assistant", has_text="Option B, on the cost curve")
    expect(reply).to_be_visible()
    return reply


def test_the_branch_control_is_labelled_and_in_the_replys_footer_row(ui, ui_page):
    """D306 pt 1: a LABELLED text control, in the footer row beside the reply's turn count and
    time — not a glyph in the corner family (⧉ ⟲ ⚑), which is where it used to live."""
    _with_a_reply(ui, ui_page)
    reply = _reply(ui_page)
    control = reply.locator(".branch-from")
    expect(control).to_have_count(1)
    expect(control).to_have_text("⑂ branch")               # a word, not a bare glyph
    expect(control).to_have_attribute("data-branch-turn", "1")
    # it is IN the footer row the reader is already reading
    expect(reply.locator(".msg-meta .branch-from")).to_have_count(1)
    # and the corner family no longer holds it, while the other two are untouched
    expect(reply.locator(".branch-msg")).to_have_count(0)
    expect(reply.locator(".rewind-msg")).to_have_count(1)
    expect(reply.locator(".flag-msg")).to_have_count(1)


def test_the_branch_control_is_readable_without_a_hover(ui, ui_page):
    """The whole of pt 1. The control it replaced was `opacity: 0` until the bubble was hovered
    and `.45` then, so it read as decoration where it was visible at all. This one is at full
    opacity at rest, in the footer's own ink — and the corner glyphs beside it still hide, so
    the test cannot pass by the mellowing having been dropped wholesale."""
    _with_a_reply(ui, ui_page)
    reply = _reply(ui_page)
    style = """(e) => { const s = getComputedStyle(e);
                        return { opacity: s.opacity, color: s.color }; }"""
    branch = reply.locator(".branch-from").evaluate(style)
    assert float(branch["opacity"]) == 1.0, f"⑂ branch is faded at rest: {branch}"
    # READING ink (--ink-2), brighter than the corner glyphs' --ink-3: the control is an offer
    # to be taken up, not a glyph that steps back until the pointer finds it.
    inks = ui_page.evaluate(_RESOLVED_INKS)
    assert branch["color"] == inks["--ink-2"], (
        f"⑂ branch is not in the reading ink --ink-2 ({inks['--ink-2']}): {branch['color']}")
    assert inks["--ink-2"] != inks["--ink-3"], "the two inks resolved the same — no evidence here"
    rewind = reply.locator(".rewind-msg").evaluate(style)
    assert float(rewind["opacity"]) == 0.0, (
        f"⟲ is no longer hover-revealed — the mellowing was dropped, not the glyph: {rewind}")
    assert rewind["color"] == inks["--ink-3"], (
        f"⟲ left the corner glyphs' quieter ink: {rewind['color']}")


def test_the_branch_control_is_visible_on_a_touch_screen(ui, ui_page, browser):
    """A phone cannot hover, so the glyph was never visible there at all — and an opacity-0
    control still takes a tap. The labelled control shows at full contrast on a touch pointer,
    the same as on a desktop."""
    slug, _conv_dir = _with_a_reply(ui, ui_page)
    phone = browser.new_context(has_touch=True, is_mobile=True, ignore_https_errors=True,
                                viewport={"width": 390, "height": 844})
    try:
        page = phone.new_page()
        page.add_init_script(f"localStorage.setItem('rsched_token', {TOKEN!r})")
        page.goto(f"{ui.url}/#/conversations/{slug}")
        assert page.evaluate("matchMedia('(hover: none)').matches"), "not a no-hover pointer"
        control = _reply(page).locator(".branch-from")
        expect(control).to_be_visible()
        expect(control).to_have_text("⑂ branch")
        opacity = control.evaluate("e => getComputedStyle(e).opacity")
        assert float(opacity) == 1.0, f"⑂ branch is faded on a phone: {opacity}"
    finally:
        phone.close()


def test_one_click_asks_for_a_name_and_an_optional_instruction(ui, ui_page):
    """D306 pt 4: a fork used to create the branch and navigate to it, leaving the person to
    type its first instruction there — two acts for one intention. One dialog now carries both,
    the name prefilled from the parent's title."""
    _with_a_reply(ui, ui_page)
    title = ui_page.locator(".conv-h1").first.inner_text().strip()
    assert title, "the conversation has no title to prefill from"
    _reply(ui_page).locator(".branch-from").click()

    dlg = ui_page.locator(".modal-overlay")
    expect(dlg).to_be_visible()
    name = dlg.locator(".fork-form input")
    expect(name).to_have_count(1)
    expect(name).to_have_value(f"{title} (branch)")        # the parent's own title, prefilled
    box = dlg.locator(".fork-form textarea")
    expect(box).to_have_count(1)
    expect(box).to_have_value("")                          # never prefilled with a guess
    # the instruction is never forced, and the dialog says what each choice does
    assert "optional" in (box.get_attribute("placeholder") or "")
    expect(dlg.locator(".fork-hint")).to_contain_text("leave it empty")
    dlg.get_by_role("button", name="cancel", exact=True).click()
    expect(dlg).to_have_count(0)


def test_an_instruction_starts_the_branch_at_once(ui, ui_page):
    """The point of pt 4: the typed instruction is filed as the branch's first message and the
    branch is woken, so it is working when the person arrives — the operator's own click, which
    is one of the three things allowed to start a run."""
    slug, _conv_dir = _with_a_reply(ui, ui_page)
    _reply(ui_page).locator(".branch-from").click()
    dlg = ui_page.locator(".modal-overlay")
    dlg.locator(".fork-form input").fill("Cost curve")
    dlg.locator(".fork-form textarea").fill("Cost out option B over three years.")
    dlg.get_by_role("button", name="branch", exact=True).click()

    ui_page.wait_for_url(f"**/conversations/{slug}-b1")
    expect(ui_page.locator("#toast")).to_contain_text("working on your instruction")
    branch = ui.conversations / f"{slug}-b1"
    msgs = list((branch / "inbox").glob("msg-*.json"))
    assert len(msgs) == 1, msgs
    record = json.loads(msgs[0].read_text(encoding="utf-8"))
    assert record["text"] == "Cost out option B over three years."
    assert record["via"] == "conversation"       # the same channel a typed message rides
    # the name typed in the dialog is the branch's own
    import yaml
    assert yaml.safe_load((branch / "routine.yaml").read_text())["name"] == "Cost curve"


def test_an_empty_instruction_leaves_the_branch_idle(ui, ui_page):
    """Pt 4 keeps today's behaviour when the field is left alone: the branch opens with nothing
    queued and waits for the person — no message, no wake."""
    slug, _conv_dir = _with_a_reply(ui, ui_page)
    _reply(ui_page).locator(".branch-from").click()
    ui_page.locator(".modal-overlay").get_by_role("button", name="branch", exact=True).click()

    ui_page.wait_for_url(f"**/conversations/{slug}-b1")
    expect(ui_page.locator("#toast")).to_contain_text("opening the branch")
    branch = ui.conversations / f"{slug}-b1"
    assert list((branch / "inbox").glob("msg-*.json")) == []
