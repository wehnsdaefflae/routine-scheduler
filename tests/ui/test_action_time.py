"""Action timing is shared by routine transcripts and conversation work folds."""
from playwright.sync_api import expect


def test_action_time_advances_and_stops_on_observation(ui, ui_page):
    ui_page.goto(f"{ui.url}/#/routines")
    ui_page.wait_for_selector("h1")
    ui_page.evaluate("""async () => {
      const {createTranscript} = await import('/static/components/transcript.js');
      const box = document.createElement('div'); document.body.append(box);
      window.timingFixture = createTranscript(box, {isLive: () => true});
      window.timingFixture.add({type:'assistant_action', turn:1, ts:new Date().toISOString(),
        payload:{kind:'shell', command:'sleep 5', timeout_s:10, say:'Timing fixture'}});
    }""")
    clock = ui_page.locator(".action-time").last
    expect(clock).to_contain_text("10s operation limit")
    expect(clock).to_have_class("action-time faint small running")
    ui_page.wait_for_function("document.querySelector('.action-time progress').value > 0.5")
    ui_page.evaluate("window.timingFixture.add({type:'observation', turn:1, ts:new Date().toISOString(), payload:{kind:'shell',exit:0}})")
    expect(clock).to_contain_text("completed")
    expect(clock).not_to_have_class("action-time faint small running")


def test_a_running_call_offers_the_cancel_and_sends_its_turn(ui, ui_page):
    """F586 / D160-C, step B4: the operator's own words were *"i want the option to cancel a util
    run. just the once currently running. via a red x or sth on the message."*

    What this LOADS, rather than asserting about the attribute that points at it: the ✕ is on the
    running row, clicking it calls the view's `cancelAction` with THIS row's turn — the key that
    stops the cancel landing on the next call — and the control then takes itself away.
    """
    ui_page.goto(f"{ui.url}/#/routines")
    ui_page.wait_for_selector("h1")
    ui_page.evaluate("""async () => {
      const {createTranscript} = await import('/static/components/transcript.js');
      const box = document.createElement('div'); document.body.append(box);
      window.cancelCalls = [];
      window.cancelFixture = createTranscript(box, {isLive: () => true,
        cancelAction: (turn) => { window.cancelCalls.push(turn); return Promise.resolve({ok:true}); }});
      window.cancelFixture.add({type:'assistant_action', turn:12, ts:new Date().toISOString(),
        payload:{kind:'util', name:'slowpoke', timeout_s:300, say:'Cancel fixture'}});
    }""")
    cancel = ui_page.locator(".action-cancel").last
    expect(cancel).to_be_visible()
    expect(cancel).to_have_attribute("title", "Stop this util call (turn 12). The run itself carries on.")
    cancel.click()
    ui_page.wait_for_function("window.cancelCalls.length === 1")
    assert ui_page.evaluate("window.cancelCalls") == [12]
    # the row says the cancel is on its way, not that the call is already over: the engine polls
    # control.json every quarter second
    expect(ui_page.locator(".action-time").last).to_contain_text("cancel sent for turn 12")
    expect(cancel).to_be_hidden()


def test_the_cancel_is_absent_where_there_is_no_call_to_stop(ui, ui_page):
    """Three cases in one load, because each would be a promise the system cannot keep:
    a replayed (not live) row, a row whose action runs no child process, and a view that was
    given no cancel at all.
    """
    ui_page.goto(f"{ui.url}/#/routines")
    ui_page.wait_for_selector("h1")
    ui_page.evaluate("""async () => {
      const {createTranscript} = await import('/static/components/transcript.js');
      const mk = (opts, payload) => {
        const box = document.createElement('div'); document.body.append(box);
        const t = createTranscript(box, opts);
        t.add({type:'assistant_action', turn:3, ts:new Date().toISOString(), payload});
        return box;
      };
      const cancelAction = () => Promise.resolve({});
      window.notLive = mk({isLive: () => false, cancelAction},
                          {kind:'util', name:'slowpoke', say:'x'});
      window.notAProcess = mk({isLive: () => true, cancelAction},
                              {kind:'read_file', path:'note.md', say:'x'});
      window.noHandler = mk({isLive: () => true}, {kind:'shell', command:'sleep 9', say:'x'});
    }""")
    for box in ("notLive", "notAProcess", "noHandler"):
        count = ui_page.evaluate(f"window.{box}.querySelectorAll('.action-cancel').length")
        assert count == 0, f"{box} offered a cancel there is no running process for"


def test_a_failed_cancel_does_not_read_as_a_successful_one(ui, ui_page):
    """The failure that matters: a cancel the server refused (a finished run answers 409) must
    not leave the row looking stopped. The button comes back and the reason is ON the row —
    a silent failure here means the operator watches a call they believe they stopped.
    """
    ui_page.goto(f"{ui.url}/#/routines")
    ui_page.wait_for_selector("h1")
    ui_page.evaluate("""async () => {
      const {createTranscript} = await import('/static/components/transcript.js');
      const box = document.createElement('div'); document.body.append(box);
      window.failFixture = createTranscript(box, {isLive: () => true,
        cancelAction: () => Promise.reject(new Error('run is not active; nothing to cancel'))});
      window.failFixture.add({type:'assistant_action', turn:5, ts:new Date().toISOString(),
        payload:{kind:'script', name:'helper', say:'Failing cancel fixture'}});
    }""")
    cancel = ui_page.locator(".action-cancel").last
    cancel.click()
    expect(ui_page.locator(".action-time").last).to_contain_text(
        "cancel failed: run is not active; nothing to cancel")
    expect(cancel).to_be_visible()
    expect(cancel).to_be_enabled()


def test_the_cancel_goes_away_when_the_observation_lands(ui, ui_page):
    """However the call ended, the control must not linger: its turn is now in the past, and the
    endpoint keys by turn, so a stale ✕ would be a button that silently does nothing.
    """
    ui_page.goto(f"{ui.url}/#/routines")
    ui_page.wait_for_selector("h1")
    ui_page.evaluate("""async () => {
      const {createTranscript} = await import('/static/components/transcript.js');
      const box = document.createElement('div'); document.body.append(box);
      window.endFixture = createTranscript(box, {isLive: () => true,
        cancelAction: () => Promise.resolve({})});
      window.endFixture.add({type:'assistant_action', turn:8, ts:new Date().toISOString(),
        payload:{kind:'shell', command:'sleep 1', timeout_s:120, say:'Ending fixture'}});
    }""")
    expect(ui_page.locator(".action-cancel").last).to_be_visible()
    ui_page.evaluate("""window.endFixture.add({type:'observation', turn:8,
      ts:new Date().toISOString(), payload:{kind:'shell', exit:0}})""")
    expect(ui_page.locator(".action-cancel").last).to_be_hidden()


def test_conversation_action_time_has_no_invented_file_deadline(ui, ui_page):
    ui_page.goto(f"{ui.url}/#/routines")
    ui_page.wait_for_selector("h1")
    ui_page.evaluate("""async () => {
      const {createChat} = await import('/static/components/chat.js');
      const box = document.createElement('div'); document.body.append(box);
      const chat = createChat(box, {isLive: () => true});
      chat.add({type:'assistant_action', turn:1, ts:new Date().toISOString(),
        payload:{kind:'read_file', path:'note.md', say:'Read fixture'}});
      document.querySelector('.work-fold').open = true;
    }""")
    expect(ui_page.locator(".action-time").last).to_contain_text("no fixed action deadline")


def test_replayed_action_time_is_static_and_respects_reduced_motion(ui, ui_page):
    """Historical completion uses event timestamps, not time since opening the page."""
    ui_page.emulate_media(reduced_motion="reduce")
    ui_page.goto(f"{ui.url}/#/routines")
    ui_page.wait_for_selector("h1")
    ui_page.evaluate("""async () => {
      const {createTranscript} = await import('/static/components/transcript.js');
      const box = document.createElement('div'); document.body.append(box);
      const transcript = createTranscript(box, {isLive: () => true});
      transcript.add({type:'assistant_action', turn:1, ts:'2026-09-12T00:00:00Z',
        payload:{kind:'util',name:'example',say:'Historical fixture'}});
      transcript.add({type:'observation',turn:1,ts:'2026-09-12T00:00:05Z',
        payload:{kind:'util',exit:0}});
    }""")
    clock = ui_page.locator(".action-time").last
    expect(clock).to_contain_text("completed · 5s elapsed · 5m 0s operation limit")
    expect(clock).not_to_have_class("action-time faint small running")
    assert clock.locator("progress").evaluate("node => node.value") == 5
    assert ui_page.evaluate("matchMedia('(prefers-reduced-motion: reduce)').matches")
