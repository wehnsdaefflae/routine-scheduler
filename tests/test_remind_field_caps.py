"""A cap a run can only learn by being refused (F680)."""

from rsched.engine.actionschema import ACTION_SCHEMA
from rsched.reminder_checks import MAX_DESCRIPTION_CHARS, MAX_REGEX_CHARS


def _props() -> dict:
    return ACTION_SCHEMA["properties"]["remind"]["properties"]


def test_the_regex_field_states_its_cap():
    text = _props()["regex"]["description"]
    assert str(MAX_REGEX_CHARS) in text, text


def test_the_description_field_states_its_cap():
    text = _props()["description"]["description"]
    assert str(MAX_DESCRIPTION_CHARS) in text, text


def test_the_stated_caps_are_the_enforced_ones():
    """The numbers are interpolated, never retyped: a documented cap that drifts from the
    enforced one is the same defect with a different number."""
    from rsched.engine import schema_remind

    assert f"{MAX_REGEX_CHARS} characters" in schema_remind.REMIND_FIELD[
        "properties"]["regex"]["description"]
    assert f"{MAX_DESCRIPTION_CHARS} characters" in schema_remind.REMIND_FIELD[
        "properties"]["description"]["description"]


def test_every_capped_remind_field_says_so():
    """The guard against the next cap added without its wording: a field whose write path
    enforces a length must name it. Extend the mapping when a cap is added."""
    capped = {"regex": MAX_REGEX_CHARS, "description": MAX_DESCRIPTION_CHARS}
    missing = [name for name, cap in capped.items()
               if str(cap) not in _props()[name]["description"]]
    assert not missing, (
        f"remind fields enforcing a cap the schema does not state: {missing} — a run can only "
        f"learn such a limit by being refused by it, which costs it a turn")


def test_the_id_field_says_what_shape_an_id_has():
    """`remind.id` is refused unless it matches `reminders.ID_RE`, and a run that invents an id
    learns that the same expensive way."""
    assert "rem-" in _props()["id"]["description"]
