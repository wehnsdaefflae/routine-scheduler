"""A NEW conversation starts with the reminder layer and run history at the ROUTINE defaults
(decided 2026-11-05): a layer nobody switches on never learns anything, and that holds for a
conversation as much as for a routine. The create path and the composer's preview agree."""

from __future__ import annotations

from conftest import authed_client, seeded_server
from rsched import conversations as conv_mod
from rsched.config import DEFAULT_CAPABILITIES, load_routine


def test_a_new_conversation_holds_the_routine_reminder_and_history_defaults(tmp_path):
    server = seeded_server(tmp_path)
    d = conv_mod.create_conversation(server, slug="c-defaults", first_message="hello")
    cfg, problems = load_routine(d)
    assert cfg is not None and not problems
    assert cfg.capabilities["reminders"] == DEFAULT_CAPABILITIES["reminders"] == "local"
    assert cfg.capabilities["runs"] == DEFAULT_CAPABILITIES["runs"] == "last"


def test_the_composer_preview_shows_what_create_persists(tmp_path):
    server = seeded_server(tmp_path)
    with authed_client(server) as c:
        caps = c.get("/api/conversations/defaults").json()["capabilities"]
    assert caps["active"]["reminders"] == "local" and caps["active"]["runs"] == "last"
