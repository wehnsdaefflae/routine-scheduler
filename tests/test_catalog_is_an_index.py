"""The two CATALOGS in the capabilities section are an INDEX, not a description (goal b5 step 4).

The operator's decision of 2026-11-14, answering the measurement below with option 3 and a
condition: *"Let's try 3 for three days and see how it goes"*. So this is a TRIAL, reversible by
restoring two compositions, and what it trades is stated here rather than in a release note
nobody re-reads.

WHAT WAS MEASURED, over 52 live `prompt-sections.json` captures across the fleet. The capabilities
section means 15,656 chars of every composed prompt, attributed by non-overlapping segmentation on
its own markers: **util catalog 6,696** · **sub-workflow patterns 3,011** (4,232 on the 37 of 52
runs that carry them) · capabilities-enabled glosses 2,226 · permission notes 1,589 · secrets
1,328 · action-kinds list 312 · model line 224 · decision models 210 · machines 269.

WHY A CUT HERE NEEDED HIS DECISION AND STEPS 1-3 DID NOT. Those three removed prose the engine
already enforces or already stores — the schema dump the wire carries (−33,391), the kind bullets'
field recitals (−7,012), the digest's history tails rebounded in chars (−11,677) — and a 4-gram
overlap measurement is what justified each. Here that same measurement finds **0.0-5.4% overlap
with harness+schema for every part**: nothing in this section is a duplicate. What remains is a
catalog of what exists, so shortening it does not remove a copy — it removes a description a run
reads when choosing a tool, and makes that description cost a turn.

THE TRADE, stated plainly: a util's or a pattern's one-line summary is no longer in front of every
run; it is one call away (`util name=list`, `util name=search`, and the pattern catalog the
`create_routine` draft observation carries). The NAMES all stay, which is what keeps nothing
unfindable, and the header has to say where the descriptions went — a catalog that silently loses
them would teach a run that the library is smaller than it is. These tests pin both halves.
"""

from __future__ import annotations

from rsched.engine.utilcatalog import util_catalog_block

#: A util as `utils_lib.list_utils` yields it, with the fields the catalog block reads.
_UTILS = [
    {"name": "websearch", "summary": "websearch — web search via DuckDuckGo (keyless): a query "
                                     "in, ranked results out.", "tags": ["web", "search"]},
    {"name": "mail", "summary": "mail — everything about a MAILBOX account (Gmail or FAU): "
                                "list, read, attachments, draft, mark, delete/trash, filters, "
                                "send.", "tags": ["email", "inbox"]},
    {"name": "oddball", "summary": "oddball — a util whose tags match no category.",
     "tags": ["nothing-matches-this"]},
]


def test_the_util_catalog_carries_names_and_not_summaries() -> None:
    """The cut itself: 6,696 chars a run no longer pays on every turn."""
    text = util_catalog_block(_UTILS, ["util"], None)
    assert "websearch" in text and "mail" in text, text
    assert "DuckDuckGo" not in text, (
        "a util's one-line summary is still in the always-on prompt:\n" + text)
    assert "attachments" not in text, text


def test_a_util_is_still_findable_because_the_header_says_where_its_summary_is() -> None:
    """A catalog of bare names with no route to what they DO would make the library read as
    smaller than it is — the one way this trial could cost more than it saves."""
    text = util_catalog_block(_UTILS, ["util"], None)
    assert "name=search" in text, text
    assert "name=list" in text, text


def test_the_groups_still_carry_their_own_names() -> None:
    """The controlled category vocabulary (D52 Phase 1) is what makes a bare-name list scannable
    at all, so the cut keeps it: a run looking for a mail tool reads one group, not 56 names.
    """
    text = util_catalog_block(_UTILS, ["util"], None)
    assert "### Email & messaging" in text, text
    assert "### Web, browser & scraping" in text, text
    assert "### Other" in text, text


def test_a_reserved_util_keeps_its_annotation() -> None:
    """A reservation is not a description — it is the one fact that decides whether a call is
    even possible, and a run that cannot see it spends a turn discovering a refusal.
    """
    class _Grants:
        utils: frozenset[str] = frozenset()
        gated_verbs = {"mail": {"send"}}
        gated_utils: frozenset[str] = frozenset({"darknet"})
        denied: frozenset[str] = frozenset()

    text = util_catalog_block(
        _UTILS + [{"name": "darknet", "summary": "darknet — fetch .onion pages.",
                   "tags": ["tor"]}], ["util"], _Grants())
    assert "send reserved — not granted to this routine" in text, text
    assert "reserved — not granted to this routine" in text, text


def test_a_run_that_cannot_call_utils_still_reads_the_catalog_as_a_map() -> None:
    """The no-`util`-kind header is the other branch, and it must survive the cut: the list then
    tells a clarify session what a routine could be BUILT to do.
    """
    text = util_catalog_block(_UTILS, [], None)
    assert "cannot CALL" in text, text
    assert "DuckDuckGo" not in text, text


def test_the_pattern_list_carries_slugs_and_not_descriptions(tmp_path, monkeypatch) -> None:
    """The other half of option 3: 4,232 chars on every prompt that carries the block.

    A slug states its own purpose well enough to pick from, the library holds what each covers,
    and the child's own prompt carries the pattern whole — so a wrong pick is correctable where
    a missing name would not be.
    """
    from rsched.engine import capabilities

    patterns = [{"slug": "answer-a-mailbox",
                 "description": "Serve one mailbox: scan every watched folder, classify what is "
                                "new, keep the server-side filter current."},
                {"slug": "train-a-model",
                 "description": "Advance a model by verified increments on a remote machine."}]
    monkeypatch.setattr("rsched.workflows.library.list_workflows",
                        lambda _home: list(patterns))
    text = capabilities.capabilities_digest(_ctx(tmp_path), allowed_kinds={"spawn", "util"})
    assert "answer-a-mailbox" in text and "train-a-model" in text, text
    assert "Serve one mailbox" not in text, (
        "a pattern's description is still in the always-on prompt:\n" + text)
    assert "workflows/<slug>" in text, (
        "nothing tells the run where a pattern's description is:\n" + text)


def _ctx(tmp_path):
    """The smallest RunContext stand-in `capabilities_digest` reads — it must fail OPEN on every
    optional part, which is why a stand-in can exercise it at all.
    """
    from types import SimpleNamespace

    class _Registry:
        @staticmethod
        def for_model(*_a, **_k):
            raise RuntimeError("no registry in this test")

    return SimpleNamespace(
        registry=_Registry(),
        grants=None,
        routine=SimpleNamespace(slug="probe", dir=tmp_path, models={}, efforts={}, machines=[]),
        server=SimpleNamespace(libraries_home=tmp_path, routines_home=tmp_path, machines={}),
        granted_now=set(), granted_once=set(), grant_args={}, depth=0)
