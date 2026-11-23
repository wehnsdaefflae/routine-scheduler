"""Every item id the console renders is a link to its item — and only the ids that have one: the
operator asked for it about a run's own narration (2026-11-23, *"ids like D306, D307, D307b, or
D310 in messages should all be hyperlinks to the actual item"*), where an id was plain text while
the Messages page linked the same id, so the linkification moved into the two `md.js` functions
all console prose passes through; these guards hold both halves of that — a resolvable id links
from narration, a summary and an observation alike, an id the index does not carry stays plain
text (in arbitrary prose a bare `D1` is ordinary text far more often than an item), and a LETTER
SUFFIX is part of the id rather than a stray character after the link (`F557b` exists in the
ledger today, and `D307b` is how a run names a follow-on increment while it builds it).
"""

from __future__ import annotations

#: Two ids the index knows and two it does not, seeded through `ref-titles.__seed` — the browser
#: suite's own seam for a renderer whose input is a fetch.
SEED = """() => import('/static/ref-titles.js').then((m) => {
    m.__seed({
        'D306': 'conversation branches: a UI people can find and use',
        'D307b': 'playbooks reachable where a conversation ends',
        'F557b': 'the second half of a split finding',
    });
    return true;
})"""


def _render(page, fn: str, text: str):
    """Render `text` as console PROSE — `components/prose.js`, the module that composes the
    markdown renderer with the item linker — and report what became a link.
    """
    return page.evaluate(
        """async ([fn, text]) => {
            const prose = await import('/static/components/prose.js');
            const node = prose[fn](text);
            document.body.append(node);
            return {
                links: [...node.querySelectorAll('a.ref-link')].map((a) => ({
                    ref: a.dataset.ref, href: a.getAttribute('href'), text: a.textContent,
                })),
                text: node.textContent,
            };
        }""",
        [fn, text])


def test_an_id_in_narration_becomes_a_link_to_its_item(ui, ui_page):
    """A run's `say` is rendered with `mdInline`, which is where the operator saw a bare id."""
    ui_page.goto(ui.url)
    ui_page.wait_for_selector(".topbar")
    ui_page.evaluate(SEED)
    out = _render(ui_page, "mdInline", "Creating D307b's worktree while I build D306.")
    assert [link["ref"] for link in out["links"]] == ["D307b", "D306"]
    assert all(link["href"] == f"#/messages?focus={link['ref']}" for link in out["links"])


def test_a_letter_suffix_is_part_of_the_id_and_not_left_outside_the_link(ui, ui_page):
    """`F557b` is one id: a pattern without the suffix links `F557` and leaves a stray `b`,
    which both points at the wrong item and reads as a typo."""
    ui_page.goto(ui.url)
    ui_page.wait_for_selector(".topbar")
    ui_page.evaluate(SEED)
    out = _render(ui_page, "mdInline", "F557b is the second half.")
    assert [link["ref"] for link in out["links"]] == ["F557b"]
    assert out["links"][0]["text"].startswith("F557b")
    restored = out["text"].replace(out["links"][0]["text"], "F557b")
    assert restored == "F557b is the second half."


def test_an_id_the_index_does_not_carry_stays_plain_text(ui, ui_page):
    """This reaches arbitrary prose, so a token that is not a known item must not become a dead
    link — in a sentence about a grade or a count, `D1` and `F5` are ordinary text."""
    ui_page.goto(ui.url)
    ui_page.wait_for_selector(".topbar")
    ui_page.evaluate(SEED)
    out = _render(ui_page, "mdInline", "The D1 grade and the F5 key are not items.")
    assert out["links"] == []
    assert out["text"] == "The D1 grade and the F5 key are not items."


def test_prose_with_no_known_id_keeps_its_own_text_nodes(ui, ui_page):
    """Linkifying rebuilds a text node as a fragment, which is fine where a link replaces part
    of it and wrong where nothing does: the console lets a person select and copy a reply, and
    swapping the node out from under a live selection drops it. So a node none of whose ids are
    known must be the SAME node afterwards, not an identical-looking replacement."""
    ui_page.goto(ui.url)
    ui_page.wait_for_selector(".topbar")
    ui_page.evaluate(SEED)
    same = ui_page.evaluate(
        """async () => {
            const md = await import('/static/md.js');
            const refs = await import('/static/components/reflinks.js');
            // The SAME node before and after, which is the only way to see identity: an
            // equal-looking replacement passes every content assertion.
            const node = md.mdInline('The D1 grade and the F5 key are not items.');
            const before = node.firstChild;
            refs.linkifyKnownRefs(node);
            return {kept: node.firstChild === before, text: node.textContent};
        }""")
    assert same["kept"] is True
    assert same["text"] == "The D1 grade and the F5 key are not items."


def test_a_finish_summary_links_its_ids_too(ui, ui_page):
    """A summary is rendered with the block-level `md`, a different function from narration's —
    both had to be covered, or half the surfaces would keep carrying bare ids."""
    ui_page.goto(ui.url)
    ui_page.wait_for_selector(".topbar")
    ui_page.evaluate(SEED)
    out = _render(ui_page, "md", "**Work:** shipped D306, and D307b is next.")
    assert [link["ref"] for link in out["links"]] == ["D306", "D307b"]


def test_an_id_inside_code_is_left_alone(ui, ui_page):
    """A backticked id is a literal — a path, a branch name, a command — and linkifying it would
    rewrite something the reader is meant to copy exactly."""
    ui_page.goto(ui.url)
    ui_page.wait_for_selector(".topbar")
    ui_page.evaluate(SEED)
    out = _render(ui_page, "md", "The branch is `audit/D306` and the item is D306.")
    assert [link["ref"] for link in out["links"]] == ["D306"]


def test_the_link_carries_the_items_subject_so_the_id_is_not_the_whole_message(ui, ui_page):
    """The point of the link is that the reader needs no second document: the text is the id AND
    its one-line subject, which is what the index was seeded with."""
    ui_page.goto(ui.url)
    ui_page.wait_for_selector(".topbar")
    ui_page.evaluate(SEED)
    out = _render(ui_page, "mdInline", "D306 is the branches order.")
    assert "conversation branches" in out["links"][0]["text"]
