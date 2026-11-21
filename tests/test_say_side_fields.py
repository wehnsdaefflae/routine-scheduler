"""A side field written inside `say` as markup (F679)."""

from rsched.engine.actions import validate_action


def _problems(say: str, **extra) -> list[str]:
    return validate_action({"say": say, "kind": "read_file", "path": "state/notes.md", **extra})


def test_a_note_tag_in_say_is_corrected_not_published():
    out = _problems("looking at the plan <note>the gate lock is held by a sibling</note>")
    assert len(out) == 1, out
    assert "`note` field" in out[0]
    assert "published verbatim" in out[0]


def test_each_side_field_is_caught_by_name():
    for field in ("note", "remind", "remind_feedback"):
        out = _problems(f"narration <{field}>text</{field}>")
        assert out and f"<{field}>" in out[0], (field, out)


def test_a_tag_with_attributes_is_still_the_mistake():
    assert _problems('checking <note kind="finding">x</note>')


def test_prose_about_a_note_tag_is_not_the_mistake():
    """The engine's own documentation names these tags; so may a run's narration."""
    for say in ("the docs say a <note> tag inside say is published verbatim — avoid it",
                "my note field carries it instead",
                "closing </note> appeared in the fetched page",
                "reading notes.md for the tail"):
        assert _problems(say) == [], say


def test_a_clean_action_is_untouched():
    assert _problems("reading the plan") == []
