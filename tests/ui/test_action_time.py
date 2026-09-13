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
