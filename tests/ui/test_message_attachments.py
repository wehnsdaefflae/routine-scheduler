"""A file attached to a routine's inbox message, in the browser (D275).

Operator, 2026-11-07: *"I wanna be able to add an attachment to a routine's inbox message
field"* — and, asked how far it should go, 2026-11-08: *"page too: also serve it back for
viewing/downloading on the Messages page"*. So both halves are claims a test has to make: the
composer takes files, and the queued message shows them back.

It is written against the REAL console and the REAL server — nothing here is stubbed — because
the two things that can silently fail are exactly the two a shallow test passes over: a
multipart POST whose files never reach disk, and a thumbnail whose `<img>` exists while its
bytes never arrived (an `<img src>` cannot carry the Authorization header, so the bytes come
through `apiBlobUrl` and the element gets a `blob:` URL). `naturalWidth` is therefore the
assertion: it is non-zero only if the browser decoded an actual image.

A new module rather than a case in test_routine_page.py: that file sits AT its size ratchet
(492/492, tests/test_file_size.py).
"""

from __future__ import annotations

import json

from playwright.sync_api import expect

from .helpers import visible_toast

# a 1x1 transparent PNG, byte-for-byte (the same bytes tests/ui/test_view_image_thumbnail.py
# decodes — a real image, so a thumbnail that loads can be told from one that does not)
PNG_HEX = ("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
           "0000000d4944415478da63f8ffff3f0300050001a5f645400000000049454e44ae426082")


def _png(tmp_path, name="shot.png"):
    p = tmp_path / name
    p.write_bytes(bytes.fromhex(PNG_HEX))
    return str(p)


def test_a_queued_message_carries_its_attachment_and_shows_it_back(ui, ui_page, tmp_path):
    """The whole ask, end to end: attach a screenshot to the inbox composer, queue it, and
    the message that comes back shows the image — fetched through the authenticated
    attachment route and DECODED, not an element pointing at bytes that never arrived."""
    notes = tmp_path / "notes.txt"
    notes.write_text("the numbers behind it", encoding="utf-8")

    ui_page.goto(f"{ui.url}#/routine/uir")
    ui_page.wait_for_selector("h2:has-text('Messages')")
    box = ui_page.locator('textarea[data-persist="nextrun-msg-uir"]')
    box.fill("look at this before you start")
    ui_page.locator(".msg-composer input[type=file]").set_input_files(
        [_png(tmp_path), str(notes)])
    expect(ui_page.locator(".msg-composer .attach-chip")).to_have_count(2)
    ui_page.get_by_role("button", name="queue for the next run").click()
    expect(visible_toast(ui_page)).to_contain_text("next run reads it")

    # the record the next run will drain: the rels ride it (that is what drives the engine's
    # auto-attach) and the bytes are under the ROUTINE dir, where a run's read_file resolves
    rdir = ui.routine_dir("uir")
    msgs = list((rdir / "inbox").glob("msg-*.json"))
    assert len(msgs) == 1
    rec = json.loads(msgs[0].read_text(encoding="utf-8"))
    assert len(rec["attachments"]) == 2
    assert rec["text"].startswith("look at this before you start")
    assert "[attached files" in rec["text"]
    for rel in rec["attachments"]:
        assert (rdir / rel).exists(), rel

    # and the page serves them BACK: the image as a loaded thumbnail, the text file as a chip
    card = ui_page.locator(".msg-item.inbox", has_text="look at this before you start")
    thumb = card.locator("img.att-thumb")
    expect(thumb).to_be_visible()
    assert thumb.evaluate("el => el.src.startsWith('blob:')")
    assert thumb.evaluate("el => el.naturalWidth") == 1, "the thumbnail never loaded"
    expect(card.locator("button.att-file")).to_contain_text("notes.txt")

    # the composer is clear again — a queued file must not be queued twice by the next message
    expect(ui_page.locator(".msg-composer .attach-chip")).to_have_count(0)


def test_an_attachment_is_a_message_even_with_nothing_typed(ui, ui_page, tmp_path):
    """The case the operator will actually hit: drag a screenshot in and say nothing. The
    composer must send it (an empty text field used to be refused before the request was even
    made, which reads as a broken upload), and the queued message shows the image."""
    ui_page.goto(f"{ui.url}#/routine/uir")
    ui_page.wait_for_selector("h2:has-text('Messages')")
    ui_page.locator(".msg-composer input[type=file]").set_input_files([_png(tmp_path)])
    ui_page.get_by_role("button", name="queue for the next run").click()
    expect(visible_toast(ui_page)).to_contain_text("next run reads it")

    msgs = list((ui.routine_dir("uir") / "inbox").glob("msg-*.json"))
    assert len(msgs) == 1
    rec = json.loads(msgs[0].read_text(encoding="utf-8"))
    assert rec["text"].startswith("[attached files")   # never an empty body
    assert len(rec["attachments"]) == 1

    thumb = ui_page.locator(".msg-item.inbox img.att-thumb")
    expect(thumb).to_be_visible()
    assert thumb.evaluate("el => el.naturalWidth") == 1, "the thumbnail never loaded"
