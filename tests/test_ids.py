"""Identifier generation: one slug rule, one run-ts rule."""

from __future__ import annotations

import pytest

from rsched import ids


@pytest.mark.parametrize(("name", "expected"), [
    ("Nightly Digest", "nightly-digest"),
    ("  weird   NAME!!  ", "weird-name"),
    ("a—b", "a-b"),
])
def test_slugify_reduces_a_name_to_the_slug_alphabet(name, expected):
    assert ids.slugify(name) == expected
    assert ids.is_slug(ids.slugify(name))


def test_slugify_names_what_an_empty_result_becomes():
    """One slug rule for every kind of thing that has a slug. A second copy with its own
    fallback meant the same name round-tripped two different ways depending on who asked."""
    assert ids.slugify("!!!") == "routine"
    assert ids.slugify("!!!", default="playbook") == "playbook"


def test_parse_run_id_rejects_a_malformed_id():
    assert ids.parse_run_id("uir:20260922-120000") == ("uir", "20260922-120000")
    with pytest.raises(ValueError, match="malformed run id"):
        ids.parse_run_id("uir:yesterday")
