"""User slash commands: the grammar (engine/commands.parse_command) and the help catalog
the chat composer's autocomplete + reference panel feed from.
"""

import pytest

from rsched.engine.commands import CommandError, command_catalog, parse_command


def test_parse_util_with_quotes():
    action = parse_command('/util websearch "two words" --json')
    assert action["kind"] == "util"
    assert action["name"] == "websearch"
    assert action["args"] == ["two words", "--json"]
    assert action["say"]                       # schema requires say — the parser supplies it


def test_parse_read_file_single_and_multi():
    assert parse_command("/read_file notes.md")["path"] == "notes.md"
    multi = parse_command("/read_file a.md b.md")
    assert multi["paths"] == ["a.md", "b.md"]
    assert "path" not in multi


def test_parse_write_file_keeps_content_verbatim():
    action = parse_command("/write_file state/x.md Hello  world = fine")
    assert action["path"] == "state/x.md"
    assert action["content"] == "Hello  world = fine"


def test_parse_edit_file_quoted_anchor_replacement():
    action = parse_command('/edit_file f.md anchor="old text" replacement="new text"')
    assert (action["anchor"], action["replacement"]) == ("old text", "new text")
    with pytest.raises(CommandError, match="usage"):
        parse_command("/edit_file f.md anchor=only")


def test_parse_llm_view_image_and_memory():
    assert parse_command("/llm summarize this file")["prompt"] == "summarize this file"
    view = parse_command("/view_image shots/a.png what changed?")
    assert (view["path"], view["prompt"]) == ("shots/a.png", "what changed?")
    assert parse_command("/memory_read env-quirks")["name"] == "env-quirks"
    note = parse_command('/memory_write env-quirks about="server quirks" the body text')
    assert note["name"] == "env-quirks"
    assert note["content"] == "the body text"
    assert note["about"] == "server quirks"


def test_unknown_and_malformed_commands_teach():
    with pytest.raises(CommandError, match="unknown command /spawn"):
        parse_command("/spawn do a thing")       # loop control is the assistant's, not a command
    with pytest.raises(CommandError, match="usage"):
        parse_command("/util")
    with pytest.raises(CommandError, match="unbalanced quotes"):
        parse_command('/util x "unclosed')
    with pytest.raises(CommandError, match="start with /"):
        parse_command("plain text")


def test_catalog_filters_kinds_by_policy_and_lists_utils():
    class Policy:
        def allows_kind(self, kind):
            return kind not in ("memory_read", "memory_write")

    catalog = command_catalog(Policy(), [{"name": "websearch", "summary": "search the web",
                                          "usage": "gu websearch QUERY"}])
    kinds = [k["kind"] for k in catalog["kinds"]]
    assert "util" in kinds and "read_file" in kinds
    assert "memory_read" not in kinds and "memory_write" not in kinds
    assert all(k["usage"].startswith("/") and k["summary"] for k in catalog["kinds"])
    assert catalog["utils"] == [{"name": "websearch", "summary": "search the web",
                                 "usage": "gu websearch QUERY"}]
