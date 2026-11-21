"""The two CATALOGS in the capabilities section are an INDEX, not a description (goal b5 step 4)."""

from __future__ import annotations

from typing import ClassVar

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
        utils: ClassVar[frozenset[str]] = frozenset()
        gated_verbs: ClassVar[dict[str, set[str]]] = {"mail": {"send"}}
        gated_utils: ClassVar[frozenset[str]] = frozenset({"darknet"})
        denied: ClassVar[frozenset[str]] = frozenset()

    text = util_catalog_block(
        [*_UTILS, {"name": "darknet", "summary": "darknet — fetch .onion pages.",
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
    """The other half of option 3: 4,232 chars on every prompt that carries the block."""
    from rsched.engine import capabilities

    patterns = [{"slug": "answer-a-mailbox",
                 "description": "Serve one mailbox: scan every watched folder, classify what is "
                                "new, keep the server-side filter current."},
                {"slug": "train-a-model",
                 "description": "Advance a model by verified increments on a remote machine."}]
    monkeypatch.setattr("rsched.workflows.library.list_workflows",
                        lambda _home: list(patterns))
    text = capabilities.capabilities_digest(_ctx(tmp_path), allowed_kinds={"child", "util"})
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
