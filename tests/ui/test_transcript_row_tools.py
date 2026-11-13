"""A RUN's transcript rows carry the affordances a conversation's bubbles always had, and a
`view_image` row shows the file under the action and an answer in the result.

Two operator items, one renderer:

F217 (handed 2026-11-06, passed over by two runs) — *"A run transcript's messages carry NO copy
button and nothing anywhere carries report-as-bug — copy shipped for conversations only."* The ⧉
helper existed in `components/chat.js` and only chat mounted it; a run's rows had none, and
nothing anywhere could file a row as a bug.

R3357 (asked during `bahnbonus-seat-position:20261111-210421`, turn 29) — a `view_image` row put
the FILE in the result element ("--- state/x.png (image/png) — shown to you below; look at it
now.") and nothing in it answered the question the image was opened for. He asked for the link
under the ACTION ("part of what was *done*") and the reading in the RESULT.

These load the REAL run page in the REAL browser, because every claim here is about what a
person sees: a hover-revealed control, which element a link sits in, and what the result cell
says when the engine recorded no reading of its own. An attribute assertion would pass over a
control that renders nowhere.

THE RESULT CELL'S HONEST LIMIT, pinned below rather than papered over. On the vision-util path
the observation CARRIES the answer (`via: "vision-util"` + `text`), so the row shows it. On the
direct multimodal path the image is attached to the prompt and the model's reading arrives in its
NEXT turn — the observation holds no answer at all — so the row says exactly that and points at
the turn that does. `test_the_direct_path_does_not_invent_an_answer` is the test that keeps a
later change from filling that cell with something nobody read off the image.
"""

from __future__ import annotations

import json

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.ui

#: a 1x1 transparent PNG, byte-for-byte (the one every attachment test uses)
PNG_HEX = ("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
           "0000000d4944415478da63f8ffff3f0300050001a5f645400000000049454e44ae426082")

TS = "20261112-090000"
RUN = f"uir:{TS}"

#: One finished run: a util turn (the row F217's controls hang on), a DIRECT view_image (the
#: multimodal path, whose reading is the next turn), a VISION-UTIL view_image (whose description
#: is the answer), a BATCH view_image (`paths`, which used to render an empty action brief) and
#: an unreadable one (nothing to show, nothing to point at).
EVENTS = [
    {"type": "header", "run_id": RUN, "routine": "uir"},
    {"type": "assistant_action", "turn": 1, "ts": "2026-11-12T09:00:00Z",
     "payload": {"kind": "util", "name": "fs-ops", "args": ["mv", "a", "b"],
                 "say": "moving the file into place"}},
    {"type": "observation", "turn": 1, "ts": "2026-11-12T09:00:01Z",
     "payload": {"kind": "util", "name": "fs-ops", "exit": 0, "stdout": "moved a -> b"}},
    {"type": "assistant_action", "turn": 2, "ts": "2026-11-12T09:00:02Z",
     "payload": {"kind": "view_image", "path": "state/formation.png",
                 "say": "Reading the coach order off the formation diagram."}},
    {"type": "observation", "turn": 2, "ts": "2026-11-12T09:00:03Z",
     "payload": {"kind": "view_image", "media": True,
                 "files": [{"path": "state/formation.png", "media_type": "image/png",
                            "native": True}]}},
    {"type": "assistant_action", "turn": 3, "ts": "2026-11-12T09:00:04Z",
     "payload": {"kind": "view_image", "path": "state/seatmap.png",
                 "say": "And the seat map."}},
    {"type": "observation", "turn": 3, "ts": "2026-11-12T09:00:05Z",
     "payload": {"kind": "view_image",
                 "files": [{"path": "state/seatmap.png", "via": "vision-util",
                            "text": "a seat map: window seats 11A and 12A are free"}]}},
    {"type": "assistant_action", "turn": 4, "ts": "2026-11-12T09:00:06Z",
     "payload": {"kind": "view_image",
                 "paths": ["state/left.png", "state/right.png"],
                 "say": "Both halves at once."}},
    {"type": "observation", "turn": 4, "ts": "2026-11-12T09:00:07Z",
     "payload": {"kind": "view_image", "media": True,
                 "files": [{"path": "state/left.png", "media_type": "image/png",
                            "native": True},
                           {"path": "state/right.png", "media_type": "image/png",
                            "native": True}]}},
    {"type": "assistant_action", "turn": 5, "ts": "2026-11-12T09:00:08Z",
     "payload": {"kind": "view_image", "path": "state/gone.png", "say": "The third one."}},
    {"type": "observation", "turn": 5, "ts": "2026-11-12T09:00:09Z",
     "payload": {"kind": "view_image",
                 "files": [{"path": "state/gone.png", "error": "no such file"}]}},
]


def _seed(ui, ui_page, *, serve_png: bool = True) -> None:
    """The run on the page, with the authenticated run-file route answering real PNG bytes.

    The route is STUBBED for the same reason tests/ui/test_view_image_thumbnail.py stubs it:
    what changed is the renderer, and the thumbnail's difficulty is that an `<img src>` cannot
    carry the Authorization header — the bytes arrive through `apiBlobUrl` as a `blob:` URL.
    """
    run_dir = ui.seed_run("uir", TS, "finished", summary="done")
    (run_dir / "transcript.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in EVENTS), encoding="utf-8")
    if serve_png:
        ui_page.route("**/api/runs/**/file**", lambda route: route.fulfill(
            status=200, content_type="image/png", body=bytes.fromhex(PNG_HEX)))
    ui_page.goto(f"{ui.url}/#/run/{RUN}")
    expect(ui_page.locator('.turn[data-turn="1"]')).to_be_visible()


def _turn(page, n: int):
    return page.locator(f'.turn[data-turn="{n}"]')


# ---- F217: the row's own controls ---------------------------------------------------------

def test_every_transcript_row_carries_a_copy_button(ui, ui_page):
    """The ask itself: a run's rows get the ⧉ a conversation's bubbles have."""
    _seed(ui, ui_page)
    rows = ui_page.locator(".turn")
    assert rows.count() >= 5, "the transcript did not render its rows"
    for i in range(rows.count()):
        expect(rows.nth(i).locator("> .row-copy")).to_have_count(1)


def test_the_copy_button_copies_what_the_row_says(ui, ui_page):
    """Not a glyph and not the rendered HTML: the row's address, its narration, its brief and
    the result it got — the text a person pastes into a report."""
    ui_page.context.grant_permissions(["clipboard-read", "clipboard-write"])
    _seed(ui, ui_page)
    row = _turn(ui_page, 1)
    row.hover()
    row.locator("> .row-copy").click()
    got = ui_page.evaluate("navigator.clipboard.readText()")
    assert RUN in got, got
    assert "turn 1" in got and "util fs-ops" in got, got
    assert "moving the file into place" in got, got
    assert "moved a -> b" in got, got          # the RESULT, read off the row at click time
    assert "⧉" not in got and "🐞" not in got, got      # never the controls' own glyphs


def test_a_row_can_be_reported_as_a_bug_into_the_owning_routine(ui, ui_page):
    """report-as-bug, end to end: the 🐞 opens a pre-filled editable message that names where it
    goes, and queueing it lands in that routine's inbox through the route the operator's own
    composer uses (`POST /api/routines/{slug}/messages`) — no new outward channel."""
    _seed(ui, ui_page)
    row = _turn(ui_page, 1)
    row.hover()
    row.locator("> .row-bug").click()
    dialog = ui_page.locator(".modal-overlay")
    expect(dialog).to_be_visible()
    expect(dialog).to_contain_text("uir")              # the reader is told where it goes
    box = dialog.locator(".bug-box")
    prefilled = box.input_value()
    assert RUN in prefilled and "turn 1" in prefilled, prefilled
    assert "moving the file into place" in prefilled, prefilled
    box.fill(prefilled + "the destination was overwritten without a warning")
    dialog.get_by_role("button", name="queue it").click()
    expect(dialog).to_have_count(0)
    inbox = ui.routine_dir("uir") / "inbox"
    filed = sorted(inbox.glob("msg-*.json"))
    assert len(filed) == 1, f"nothing queued into {inbox}"
    record = json.loads(filed[0].read_text(encoding="utf-8"))
    assert "the destination was overwritten without a warning" in record["text"]
    assert RUN in record["text"] and "turn 1" in record["text"]


def test_a_rows_address_names_the_run_the_mount_gave_it(ui, ui_page):
    """The address is only as good as what the MOUNT passes, and nothing else tests that.

    `rowAddress` prefers `opts.runId`, falls back to `opts.slug` and names `opts.sub` when it
    has one — so a view that mounts the transcript without them yields "routine uir, turn 1",
    which cannot say WHICH run or whose turn 1 (a sub-run's turns are its own). That is what
    `views/run.js` passed until F217 was verified. Read straight off the helper, because the
    defect is in the arguments a caller supplies, not in the rendering.
    """
    _seed(ui, ui_page)
    got = ui_page.evaluate("""async () => {
      const {rowAddress} = await import('/static/components/row-tools.js');
      const ev = {turn: 7}, a = {kind: 'util', name: 'fs-ops'};
      return {full: rowAddress(ev, a, {runId: 'uir:20261112-090000', slug: 'uir', sub: 2}),
              bare: rowAddress(ev, a, {slug: 'uir'})};
    }""")
    assert got["full"] == "run uir:20261112-090000, sub-run 2, turn 7 (util fs-ops)", got
    assert got["bare"] == "routine uir, turn 7 (util fs-ops)", got
    # and the run view really hands those over — the mount, not just the helper
    mounted = ui_page.evaluate("""() => {
      const row = document.querySelector('.turn[data-turn="1"]');
      row.querySelector('.row-bug').click();
      const text = document.querySelector('.bug-box').value;
      document.querySelector('.modal-overlay button')?.click();
      return text;
    }""")
    assert "run uir:20261112-090000" in mounted, mounted


def test_the_bug_control_is_absent_where_no_routine_owns_the_rows(ui, ui_page):
    """A transcript mounted without a routine (a conversation's work fold mounts the same
    renderer) has no inbox to reach, so the control is ABSENT rather than present and broken —
    while ⧉, which needs nothing, is still there."""
    ui_page.goto(f"{ui.url}/#/routines")
    ui_page.wait_for_selector("h1")
    count = ui_page.evaluate("""async () => {
      const {createTranscript} = await import('/static/components/transcript.js');
      const box = document.createElement('div'); box.id = 'noslug'; document.body.append(box);
      const t = createTranscript(box, { isLive: () => true });
      t.add({type:'assistant_action', turn:1, payload:{kind:'util', name:'x', say:'hi'}});
      return {copy: box.querySelectorAll('.row-copy').length,
              bug: box.querySelectorAll('.row-bug').length};
    }""")
    assert count == {"copy": 1, "bug": 0}, count


def test_the_row_controls_are_reachable_without_a_hover(ui, ui_page):
    """F433's lesson, which cost the chat buttons a release: a touch pointer cannot hover, so a
    hover-only control is invisible on a phone — and an opacity-0 control still takes a tap.
    Read as the computed style, because `.btn` labels do not render in this harness."""
    _seed(ui, ui_page)
    opacity = ui_page.evaluate("""() => {
      const row = document.querySelector('.turn');
      return ['.row-copy', '.row-bug'].map((sel) =>
        getComputedStyle(row.querySelector(sel)).opacity);
    }""")
    assert opacity == ["0", "0"], opacity        # quiet for a mouse until the row is hovered
    labels = ui_page.evaluate("""() => {
      const row = document.querySelector('.turn');
      return ['.row-copy', '.row-bug'].map((sel) => row.querySelector(sel).textContent);
    }""")
    assert labels == ["⧉", "🐞"], labels
    hidden = ui_page.evaluate("""() => {
      const row = document.querySelector('.turn');
      return [...document.styleSheets].flatMap((s) => {
        try { return [...s.cssRules]; } catch { return []; }
      }).some((r) => r.conditionText?.includes('hover: none')
                     && r.cssText.includes('row-copy')
                     && r.cssText.includes('row-bug'));
    }""")
    assert hidden, "no (hover: none) rule reveals the row controls on a touch pointer"


# ---- R3357: the file under the action, the answer in the result ---------------------------

def test_the_image_link_sits_in_the_action_element(ui, ui_page):
    """R3357 (1): "the link to the image belongs in the message's action element". The path and
    a loaded thumbnail are in `.act`, and the result fold no longer holds the image."""
    _seed(ui, ui_page)
    row = _turn(ui_page, 2)
    act = row.locator("> .act")
    expect(act).to_contain_text("state/formation.png")
    thumb = act.locator("img.att-thumb")
    expect(thumb).to_be_visible()
    assert thumb.evaluate("el => el.src.startsWith('blob:')")   # through the authed route
    assert thumb.evaluate("el => el.naturalWidth") == 1         # it really LOADED
    expect(row.locator(".obs-collapse img.att-thumb")).to_have_count(0)


def test_a_batched_view_image_names_its_files_under_the_action(ui, ui_page):
    """`BRIEF_FIELD.view_image` is `path`, so a `view_image paths=[…]` batch rendered its action
    brief EMPTY — the one row whose subject is a list of files named none of them."""
    _seed(ui, ui_page)
    act = _turn(ui_page, 4).locator("> .act")
    expect(act).to_contain_text("state/left.png")
    expect(act).to_contain_text("state/right.png")
    expect(act.locator("img.att-thumb")).to_have_count(2)


def test_the_direct_path_does_not_invent_an_answer(ui, ui_page):
    """R3357 (2) at its honest bound: the image was shown DIRECTLY to a multimodal model, so the
    observation holds no reading of it — the reading is the next turn. The result row says so in
    those words and points there; it must never show prose nobody read off the image."""
    _seed(ui, ui_page)
    link = _turn(ui_page, 2).locator(".obs-answer-link")
    expect(link).to_have_count(1)
    expect(link).to_contain_text("shown directly to the model")
    expect(link).to_contain_text("turn 3")
    assert link.get_attribute("data-turn-link") == "3"
    # and the engine's placeholder sentence is not left standing as the answer
    expect(_turn(ui_page, 2).locator(".obs-collapse")).not_to_contain_text("look at it now")


def test_the_answer_pointer_is_visible_without_expanding_anything(ui, ui_page):
    """The defect R3357 was RAISED about, reintroduced one element over — and the reason this
    test exists at all: `to_have_count(1)` passes on an element nobody can see.

    The pointer was appended to the observation's own `<details class="obs-collapse">`, which
    starts COLLAPSED (`obsBody`), so the one line saying where the image's reading is was
    invisible until the reader expanded the fold — exactly what the thumbnail did before F559
    moved it out. It rides the TURN instead, beside the rule-fire strip, which is this
    renderer's own place for something a reader must see without expanding anything.
    """
    _seed(ui, ui_page)
    row = _turn(ui_page, 2)
    assert row.locator(".obs-collapse").first.evaluate(
        "el => !el.open"), "the result fold no longer starts collapsed — rewrite this test"
    link = row.locator(".obs-answer-link")
    expect(link).to_be_visible()
    assert link.evaluate("el => !el.closest('details')"), "the pointer is back inside a fold"


def test_the_vision_utils_description_is_the_answer_where_it_ran(ui, ui_page):
    """The other path: a text-only model got the `vision` util's description, which IS what the
    run reasoned about — so it stands as the answer, and no pointer is drawn."""
    _seed(ui, ui_page)
    row = _turn(ui_page, 3)
    expect(row.locator(".obs-collapse")).to_contain_text(
        "a seat map: window seats 11A and 12A are free")
    expect(row.locator(".obs-answer-link")).to_have_count(0)


def test_the_row_reads_at_both_widths(ui, ui_page):
    """LOOK AT IT, at the two widths the console is used at: desktop 1280 and phone 390.

    The assertions are what a screenshot cannot settle — a `.btn` renders as a blank pill in
    this container (no 500-weight face), so a button is verified by its COMPUTED TEXT, never by
    eye — and `RSCHED_UI_SHOTS` (set only when a human is going to look) also writes the two
    PNGs for that look. Nothing here depends on the variable being set.
    """
    import os
    from pathlib import Path

    _seed(ui, ui_page)
    shots = os.environ.get("RSCHED_UI_SHOTS")
    for width, height, label in ((1280, 900, "desktop"), (390, 844, "phone")):
        ui_page.set_viewport_size({"width": width, "height": height})
        row = _turn(ui_page, 2)
        expect(row).to_be_visible()
        # the image's file is under the ACTION at both widths, and the answer pointer with it
        expect(row.locator("> .act")).to_contain_text("state/formation.png")
        expect(row.locator(".obs-answer-link")).to_have_count(1)
        # the row's own controls are THERE and carry their labels (read, not eyeballed)
        labels = row.evaluate("""(el) => ['.row-copy', '.row-bug']
            .map((sel) => el.querySelector(sel)?.textContent ?? 'MISSING')""")
        assert labels == ["⧉", "🐞"], (label, labels)
        # nothing sideways: the phone width is where a 260px thumbnail in a flex row would
        # push the document into a horizontal scroll (tests/ui/test_mobile_nav.py's invariant)
        over = ui_page.evaluate(
            "() => document.documentElement.scrollWidth - window.innerWidth")
        assert over <= 0, f"{label} scrolls sideways by {over}px"
        if shots:
            ui_page.locator('.turn[data-turn="2"]').hover()
            out = Path(shots) / f"f217-{label}-{width}.png"
            out.parent.mkdir(parents=True, exist_ok=True)
            ui_page.screenshot(path=str(out), full_page=False)


def test_an_unreadable_image_points_nowhere(ui, ui_page):
    """Nothing was shown, so there is nothing to read and nothing to point at: the error stands
    alone. The negative case that keeps the pointer from becoming decoration on every row."""
    _seed(ui, ui_page, serve_png=False)
    row = _turn(ui_page, 5)
    expect(row).to_contain_text("no such file")
    expect(row.locator(".obs-answer-link")).to_have_count(0)
    expect(row.locator("img.att-thumb")).to_have_count(0)
