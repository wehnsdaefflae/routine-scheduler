"""The conversation right rail against the REAL console: the browser-session section
(D86 / R262 pt2 — rows from the persisted util handle, blob-rendered screenshot, close
control hitting the stop endpoint) and the per-section collapse toggles with localStorage
persistence (F296 / R262 pt1). Plus the one state the left rail and the chat must agree with
the rest of the console on: a conversation waiting on YOU."""

from __future__ import annotations

import json
import re
import socket

from playwright.sync_api import expect

from rsched.paths import atomic_write_json

# a 1x1 transparent PNG, byte-for-byte
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d4944415478da63f8ffff3f0300050001a5f645400000000049454e44ae426082")


def _start_conversation(ui, ui_page):
    ui_page.goto(f"{ui.url}/#/conversations")
    ui_page.locator(".conv-new textarea").fill("Drive a browser for me.")
    ui_page.get_by_role("button", name="start conversation").click()
    ui_page.wait_for_url("**/conversations/**")
    slug = ui_page.url.rsplit("/", 1)[-1]
    return slug, ui.conversations / slug


def _write_handle(conv_dir, *, port: int, pid: int = 999_999_999) -> None:
    state = conv_dir / "state"
    state.mkdir(exist_ok=True)
    (state / "browser-view.png").write_bytes(PNG)
    (state / "browser-session.json").write_text(json.dumps({
        "cdp": f"http://127.0.0.1:{port}", "host": "127.0.0.1", "port": port,
        "pid": pid, "url": "https://example.com", "name": "default",
        "view": "state/browser-view.png", "started": 1754700000.0}), encoding="utf-8")


def test_browser_section_renders_and_close_clears_session(ui, ui_page):
    """With a live-looking handle (a really-listening port) the rail grows a 'browser'
    section: url line, screenshot, and a ✕ that hits the stop endpoint — after which the
    handle is gone and the section hides again."""
    _slug, conv_dir = _start_conversation(ui, ui_page)
    cap = ui_page.locator(".conv-view .rail-cap", has_text="browser")
    expect(cap).to_be_hidden()   # no session yet

    # a listening socket makes the liveness probe (one TCP connect) report alive=True,
    # which is what arms the close control
    srv = socket.socket()
    try:
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        _write_handle(conv_dir, port=srv.getsockname()[1])
        ui_page.reload()

        expect(cap).to_be_visible()
        expect(ui_page.locator(".browser-line")).to_contain_text("https://example.com")
        # the screenshot arrives via an authed fetch -> blob URL, never a bare <img src>
        shot = ui_page.locator(".browser-shot")
        expect(shot).to_be_visible()
        assert shot.evaluate("el => el.src.startsWith('blob:')")

        ui_page.locator(".browser-sess .bg-cancel").click()
        # the stop endpoint deletes the model-written handle (the fake pid kills nothing)
        expect(cap).to_be_hidden()
        assert not (conv_dir / "state" / "browser-session.json").exists()
    finally:
        srv.close()


def test_a_refused_cancel_is_an_error_toast(ui, ui_page):
    """A refusal on the rail's ✕ is a FAILURE toast (red, and traced as UI friction), never the
    plain one a success prints — the two looked identical, so a cancel that did nothing read as
    one that worked."""
    slug, _conv_dir = _start_conversation(ui, ui_page)
    row = [{"taskid": "t1", "state": "running", "label": "crawl the archive", "summary": ""}]
    ui_page.route(f"**/api/conversations/{slug}/background", lambda route: route.fulfill(
        status=200, content_type="application/json", body=json.dumps(row)))
    ui_page.route(f"**/api/conversations/{slug}/background/t1/cancel", lambda route: route.fulfill(
        status=409, content_type="application/json", body='{"detail": "the task already ended"}'))
    ui_page.reload()

    ui_page.locator(".bg-tasks .bg-cancel").click()
    expect(ui_page.locator("#toast.err:not([hidden])")).to_contain_text("the task already ended")


def test_rail_sections_collapse_and_persist(ui, ui_page):
    """F296: a rail cap is a toggle — clicking collapses just that section, the choice
    sticks in localStorage across a full reload, and clicking again reopens it. R341: the
    key is `rail:<name>` (not `convrail:`) because the run view renders the SAME component
    — a fold meant in one view is meant in the other."""
    _start_conversation(ui, ui_page)
    cap = ui_page.locator(".conv-view .rail-cap", has_text="state").first
    graph = ui_page.locator(".stategraph")
    expect(graph).to_be_visible()

    cap.click()
    expect(graph).to_be_hidden()
    assert ui_page.evaluate("localStorage.getItem('rail:state')") == "closed"

    ui_page.reload()
    expect(ui_page.locator(".stategraph")).to_be_hidden()

    ui_page.locator(".conv-view .rail-cap", has_text="state").first.click()
    expect(ui_page.locator(".stategraph")).to_be_visible()
    assert ui_page.evaluate("localStorage.getItem('rail:state')") == "open"


def test_a_conversation_has_no_goal_panel_and_no_compression_dial(ui, ui_page):
    """A conversation's spine is the plan it writes itself — no finish line, no Done when — so
    its rail carries no goal section; and lossless output compression is engine behaviour, not
    a dial on its header."""
    _start_conversation(ui, ui_page)
    expect(ui_page.locator(".conv-view .rail-cap", has_text="state").first).to_be_visible()
    expect(ui_page.locator('.conv-view .rail-cap[data-rail="goal"]')).to_have_count(0)
    expect(ui_page.get_by_label("Output compression", exact=True)).to_have_count(0)


def test_the_rail_renders_where_this_browser_refuses_storage(ui, ui_page):
    """The rail remembers each section's fold in browser storage, which can THROW (a private
    window, a blocked site-data setting). util.js's `storage` degrades to memory for exactly
    that; the rail called localStorage itself, so a refused read took the whole run view down
    with it. Only the rail's own keys are refused here — the console's token still reads."""
    ui_page.add_init_script("""(() => {
      const get = Storage.prototype.getItem, set = Storage.prototype.setItem;
      const refuse = (k) => String(k).startsWith("rail:");
      Storage.prototype.getItem = function (k) {
        if (refuse(k)) throw new DOMException("refused", "SecurityError");
        return get.call(this, k);
      };
      Storage.prototype.setItem = function (k, v) {
        if (refuse(k)) throw new DOMException("refused", "SecurityError");
        return set.call(this, k, v);
      };
    })()""")
    ui.seed_run("uir", "20260714-070000", "finished", summary="done")
    ui_page.goto(f"{ui.url}/#/run/uir:20260714-070000")
    cap = ui_page.locator(".rail-cap").first
    expect(cap).to_be_visible()
    cap.click()                                   # the fold still works, held in memory
    expect(cap).to_have_class(re.compile(r"\bclosed\b"))


def _computed(page, prop: str, value: str) -> str:
    """What `prop: value` computes to here — a token's colour in the page's current theme."""
    return page.evaluate("""([prop, value]) => { const p = document.createElement("span");
      p.style.setProperty(prop, value); document.body.append(p);
      const c = getComputedStyle(p).getPropertyValue(prop); p.remove(); return c; }""",
                         [prop, value])


def test_a_conversation_waiting_on_you_wears_the_summons_colour(ui, ui_page):
    """SUMMONS (coral) is the console's ONE colour for something waiting on a person — the
    `waiting_user` chip, the transcript's question row, the decisions badge. The conversation
    list's state dot and the chat's question bubble still wore amber, the retired palette's
    catch-all, so a conversation that needed you looked like one that was merely paused."""
    slug, conv_dir = _start_conversation(ui, ui_page)
    question = {"qid": "q-1", "mode": "blocking", "question": "Option A or option B?",
                "options": ["A", "B"], "type": "text", "asked": "20260827-100000"}
    run_dir = conv_dir / "runs" / "20260827-100000"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "transcript.jsonl").write_text(
        json.dumps({"type": "header", "run_id": "X", "routine": "X", "depth": 0}) + "\n"
        + json.dumps({"type": "question", "turn": 1, "payload": question}) + "\n",
        encoding="utf-8")
    atomic_write_json(run_dir / "status.json",
                      {"state": "waiting_user", "turn": 1, "question": question})
    ui_page.reload()

    dot = ui_page.locator(f'.conv-item[href$="/{slug}"] .dot')
    bubble = ui_page.locator(".msg.question-msg")
    expect(dot).to_have_class("dot waiting_user")
    expect(bubble).to_be_visible()
    assert (dot.evaluate("e => getComputedStyle(e).backgroundColor")
            == _computed(ui_page, "background-color", "var(--summons)"))
    assert (bubble.evaluate("e => getComputedStyle(e).backgroundColor")
            == _computed(ui_page, "background-color", "var(--summons-dim)"))


def test_the_default_route_loads_where_this_browser_refuses_session_storage(ui, ui_page):
    """The default route is the new-conversation composer, and it touched `sessionStorage`
    bare twice — the admin toggle's armed token and the fork's prefill hand-off. In a browser
    that blocks site data, merely READING the global throws, so the console's landing page
    failed to load. The toggle now goes through util.js's `session` (degrades to memory) and
    the prefill is module state; arming admin still works, held for the page's life."""
    ui_page.add_init_script("""(() => {
      Object.defineProperty(window, "sessionStorage", {
        configurable: true,
        get() { throw new DOMException("refused", "SecurityError"); } });
    })()""")
    ui_page.goto(f"{ui.url}/#/")
    expect(ui_page.locator(".conv-new textarea")).to_be_visible()
    admin = ui_page.locator(".conv-new button", has_text="admin")
    admin.click()
    dialog = ui_page.get_by_role("dialog")
    dialog.locator("input").fill("adm1n")
    dialog.get_by_role("button", name="ok").click()
    expect(admin).to_have_class(re.compile(r"\barmed\b"))
