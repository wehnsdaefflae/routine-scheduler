"""An image the RUN looked at is shown to the person reading the transcript.

Operator, 2026-10-01: *"i still want images that the system looks at previewed in the message as
thumbnail. make sure this is being worked on!"* — the standing ask behind F559.

A `view_image` observation had no case in the transcript renderer's per-kind chain, so it fell
through to the `result — {json}` dump: the MODEL was shown the picture and the person was shown a
path. The image is the evidence for everything the run says next, and it was the one thing not
displayed.

These load the REAL transcript renderer in the REAL browser against a stubbed authenticated file
route, because the thumbnail's whole difficulty is that an `<img src>` cannot carry the
Authorization header: the bytes arrive through `apiBlobUrl` and the element gets a `blob:` URL. A
test that only asserted the element existed would pass over a thumbnail that never loaded.
"""
from playwright.sync_api import expect

# a 1x1 transparent PNG, byte-for-byte (the same one the attachment tests use)
PNG_HEX = ("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
           "0000000d4944415478da63f8ffff3f0300050001a5f645400000000049454e44ae426082")

#: Mount the real transcript component, feed it one action + one observation, and open the
#: observation's own <details> (obsBody collapses every result). `fileUrl` is the console's
#: generic per-conversation file route, which the page stubs.
_FIXTURE = """async ([payload]) => {
  const {createTranscript} = await import('/static/components/transcript.js');
  const box = document.createElement('div'); box.id = 'vitx'; document.body.append(box);
  const t = createTranscript(box, {
    isLive: () => true,
    fileUrl: (rel) => '/api/conversations/x/file?path=' + encodeURIComponent(rel),
  });
  t.add({type:'assistant_action', turn:1, ts:'2026-10-01T00:00:00Z',
         payload:{kind:'view_image', path:'attachments/shot.png', say:'Looking at it.'}});
  t.add({type:'observation', turn:1, ts:'2026-10-01T00:00:01Z', payload});
  for (const d of box.querySelectorAll('details')) d.open = true;
  return box.querySelectorAll('.obs-collapse').length;
}"""


def _mount(ui, ui_page, payload, png=True):
    ui_page.goto(f"{ui.url}/#/routines")
    ui_page.wait_for_selector("h1")
    if png:
        # the authenticated file route the thumbnail fetches through, stubbed with real bytes
        ui_page.route("**/api/conversations/x/file**", lambda route: route.fulfill(
            status=200, content_type="image/png", body=bytes.fromhex(PNG_HEX)))
    assert ui_page.evaluate(_FIXTURE, [payload]) == 1, "the observation did not render at all"


def test_a_viewed_image_renders_as_a_loaded_thumbnail(ui, ui_page):
    """The ask itself: the image the run looked at appears in the message."""
    _mount(ui, ui_page, {"kind": "view_image",
                         "files": [{"path": "attachments/shot.png"}]})
    thumb = ui_page.locator("#vitx img.att-thumb")
    expect(thumb).to_be_visible()
    # it really LOADED — through the authed blob route, not a bare src the header cannot reach
    assert thumb.evaluate("el => el.src.startsWith('blob:')")
    assert thumb.evaluate("el => el.naturalWidth") == 1
    # and the OBSERVATION no longer falls through to the JSON dump. Scoped to `.obs-collapse`:
    # the turn also renders an ACTION JSON block, which legitimately contains the same text, so
    # asserting over the whole subtree would fail on a correct element.
    expect(ui_page.locator("#vitx .obs-collapse")).not_to_contain_text('"files"')
    # the path is still named, in the summary line — the thumbnail adds to it, it does not hide it
    expect(ui_page.locator("#vitx .obs-collapse")).to_contain_text("attachments/shot.png")


def test_a_vision_description_is_kept_beside_the_thumbnail(ui, ui_page):
    """`text` is what a text-only model was actually given to reason about (the `vision` util's
    description), so it stays — the thumbnail adds the image, it does not replace the words.
    """
    _mount(ui, ui_page, {"kind": "view_image",
                         "files": [{"path": "attachments/shot.png",
                                    "text": "a screenshot of the console's run view"}]})
    expect(ui_page.locator("#vitx img.att-thumb")).to_be_visible()
    expect(ui_page.locator("#vitx")).to_contain_text("a screenshot of the console's run view")


def test_an_unreadable_image_says_so_and_shows_no_thumbnail(ui, ui_page):
    """The negative case that earns its place: a file the console cannot serve must report its
    error, not render a broken frame. A per-file `error` means there is nothing to show.
    """
    _mount(ui, ui_page, {"kind": "view_image",
                         "files": [{"path": "attachments/gone.png",
                                    "error": "no such file"}]},
           png=False)
    expect(ui_page.locator("#vitx")).to_contain_text("no such file")
    expect(ui_page.locator("#vitx img.att-thumb")).to_have_count(0)
