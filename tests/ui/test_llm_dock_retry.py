"""The LLM activity dock while a call is RETRYING — F655, operator-reported.

His case: a resumed run's first call hit the engine's 600 s read timeout, `with_retries` sent it
again, and the second attempt answered 11 minutes after the first had started. The dock showed
ONE call in flight the whole time, so a hung run and a slow run were indistinguishable from the
outside and he had to read the proxy's container log to find out which. What he asked for is
that a retrying call not look the same as one that is thinking: which attempt it is on, what
ended the previous one, and that a retry is under way.

Asserted on what RENDERS, never on the class name: F644's third defect was class names that
matched no CSS rule at all, so two states the code distinguished painted in identical ink and a
console read of the JS proved nothing. A `retrying` dot with no rule of its own would fall
through to the neutral grey of an unknown state — which getComputedStyle catches and a DOM
assertion does not.
"""

from __future__ import annotations

from playwright.sync_api import expect

STATES = ("running", "retrying", "done", "error")


def _open_dock(page, url):
    page.set_viewport_size({"width": 1400, "height": 900})
    page.goto(f"{url}/#/routines")
    expect(page.locator(".lt-pill")).to_be_visible()
    page.locator(".lt-pill").click()          # open the panel (the pill hides itself)


def _fire(page, events):
    page.evaluate(
        """(evs) => { for (const d of evs)
             window.dispatchEvent(new CustomEvent("rsched-bus", { detail: d })); }""", events)


def _task(id, status, **extra):
    return {"event": "llm_task", "id": id, "status": status, "endpoint": "claude-proxy",
            "model": "claude-opus-5", "purpose": f"turn {id}", **extra}


def test_a_retrying_call_says_which_attempt_and_what_ended_the_last_one(ui, ui_page):
    _open_dock(ui_page, ui.url)
    _fire(ui_page, [_task("7", "retrying", phase="attempt", attempt=2, tries=3,
                          retry_in=1.0,
                          after="claude-proxy: read timeout after 600s (connected, but no "
                                "reply within the bound)")])
    row = ui_page.locator(".lt-task").first
    expect(row).to_contain_text("attempt 2 of 3")
    expect(row).to_contain_text("read timeout after 600s")
    # it is still IN FLIGHT: the pill counts it, because it is the thing holding the run up
    expect(ui_page.locator(".lt-head-n")).to_have_text("1 running")


def test_a_first_attempt_adds_no_noise(ui, ui_page):
    """Attempt 1 is just a call. The note appears only once a retry has happened, so an
    ordinary call's row is unchanged."""
    _open_dock(ui_page, ui.url)
    _fire(ui_page, [_task("1", "running", phase="started", attempt=1, tries=3)])
    expect(ui_page.locator(".lt-task").first).to_contain_text("turn 1")
    expect(ui_page.locator(".lt-attempt")).to_have_count(0)


def test_retrying_is_painted_as_its_own_state(ui, ui_page):
    """Four states, four inks — and `retrying` is not the `running` one, which is the whole
    point: a retrying call must not look like a healthy call that is thinking."""
    _open_dock(ui_page, ui.url)
    _fire(ui_page, [
        _task("a", "running", phase="started"),
        _task("b", "retrying", phase="attempt", attempt=2, tries=3, after="read timeout"),
        _task("c", "done", phase="finished", usage={"in": 1, "out": 2}),
        _task("d", "error", phase="failed", error="gave up"),
    ])
    # wait for the render the colour read depends on — the component debounces 80ms, and
    # reading a style off a node that is not there yet is how a UI test pins nothing
    expect(ui_page.locator(".lt-task")).to_have_count(4)
    expect(ui_page.locator('[data-lt-status="retrying"] .lt-dot')).to_be_visible()

    ink = {s: ui_page.evaluate(
        "(s) => getComputedStyle(document.querySelector("
        "`[data-lt-status='${s}'] .lt-dot`)).backgroundColor", s) for s in STATES}
    assert len(set(ink.values())) == 4, f"two dock states render in the same ink: {ink}"
    # and the retrying ink is the warn one the stylesheet defines, not a fallthrough grey
    warn = ui_page.evaluate(
        "() => { const p = document.createElement('div');"
        " p.style.backgroundColor = 'var(--warn)'; document.body.append(p);"
        " const c = getComputedStyle(p).backgroundColor; p.remove(); return c; }")
    assert ink["retrying"] == warn, f"retrying paints {ink['retrying']}, not --warn ({warn})"


def test_a_retrying_child_keeps_its_group_row_honest(ui, ui_page):
    """The group row counts what is in flight. Counting only `running` reported a group whose
    single child was retrying as `no calls` — the F572 shape, rebuilt by a second in-flight
    status that one predicate did not know about."""
    _open_dock(ui_page, ui.url)
    _fire(ui_page, [
        {"event": "llm_process", "phase": "opened", "id": "p1", "kind": "wizard",
         "label": "Routine build"},
        _task("x", "retrying", phase="attempt", attempt=2, tries=3, after="read timeout",
              process_id="p1"),
    ])
    expect(ui_page.locator(".lt-proc-count").first).to_have_text("1 running")
