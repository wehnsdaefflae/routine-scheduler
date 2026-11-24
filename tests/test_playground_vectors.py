"""The playground's vector store: content-addressed, persistent, and exact over this corpus.
The guarantees worth pinning are the ones that make it self-maintaining — an unchanged library
costs no provider call, an edited condition is re-embedded, a deleted one is pruned, and two
models never share a vector space.
"""

from __future__ import annotations

import hashlib

from rsched.playground.stores import Condition
from rsched.playground.vectorstore import VectorStore, condition_key

DIM = 16


def fake_embed(texts):
    """A deterministic bag-of-tokens 'embedding': no provider, same arithmetic."""
    out = {}
    for text in texts:
        vec = [0.0] * DIM
        for token in set(text.lower().split()):
            vec[int(hashlib.sha256(token.encode()).hexdigest(), 16) % DIM] += 1.0
        out[text] = vec
    return out


def _conditions():
    return [
        Condition(store="utils", ident="mail", condition="send email smtp mailbox"),
        Condition(store="utils", ident="web-request", condition="fetch url http download"),
        Condition(store="memory", ident="quirks", condition="what breaks on this host",
                  owner="alpha"),
    ]


def test_sync_is_idempotent(tmp_path):
    """The second sync of an unchanged library makes no provider call — the property that
    makes a per-turn push channel affordable."""
    store = VectorStore(tmp_path, "m1")
    conds = _conditions()
    first = store.sync(conds, fake_embed)
    assert first["added"] == 3

    calls: list[int] = []

    def counting(texts):
        calls.append(len(texts))
        return fake_embed(texts)

    second = store.sync(conds, counting)
    assert second["added"] == 0
    assert calls == []          # not one text was sent


def test_the_index_survives_a_restart(tmp_path):
    VectorStore(tmp_path, "m1").sync(_conditions(), fake_embed)
    fresh = VectorStore(tmp_path, "m1")
    assert fresh.load() == 3
    assert fresh.dim() == DIM


def test_an_edited_condition_is_re_embedded_and_the_stale_row_pruned(tmp_path):
    """Keying by CONTENT is what makes this work without any invalidation protocol."""
    store = VectorStore(tmp_path, "m1")
    conds = _conditions()
    store.sync(conds, fake_embed)
    conds[0] = Condition(store="utils", ident="mail",
                         condition="send email smtp mailbox and drafts")
    assert store.sync(conds, fake_embed)["added"] == 1
    assert store.count() == 4           # both the old and the new vector are held…
    assert store.prune(conds) == 1       # …until a prune drops what no longer exists
    assert store.count() == 3


def test_search_ranks_by_cosine_and_filters_by_store(tmp_path):
    store = VectorStore(tmp_path, "m1")
    store.sync(_conditions(), fake_embed)
    query = fake_embed(["send email"])["send email"]
    ranked = store.search(query, limit=3)
    assert ranked[0][1].ident == "mail"
    assert ranked[0][0] > 0.5
    only = store.search(query, stores=["memory"], limit=3)
    assert [row.ident for _s, row in only] == ["quirks"]


def test_two_models_never_share_a_vector_space(tmp_path):
    """A model switch must re-embed rather than silently compare incomparable vectors."""
    a = VectorStore(tmp_path, "m1")
    a.sync(_conditions(), fake_embed)
    b = VectorStore(tmp_path, "m2")
    assert b.load() == 0                 # its own file
    assert a.path != b.path
    assert condition_key("m1", "x") != condition_key("m2", "x")


def test_a_torn_line_does_not_take_the_store_down(tmp_path):
    """The file is a cache of embeddings; a bad line is skipped, never fatal."""
    store = VectorStore(tmp_path, "m1")
    store.sync(_conditions(), fake_embed)
    with store.path.open("a", encoding="utf-8") as fh:
        fh.write('{"key": "truncated", "vec": [0.1,\n')
    assert VectorStore(tmp_path, "m1").load() == 3


def test_a_blank_condition_is_never_embedded(tmp_path):
    store = VectorStore(tmp_path, "m1")
    conds = [*_conditions(), Condition(store="rules", ident="empty", condition="   ")]
    assert store.sync(conds, fake_embed)["added"] == 3
