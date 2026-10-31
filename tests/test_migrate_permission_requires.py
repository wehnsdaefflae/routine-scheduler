"""A permission's `requires:` reaches the live library — the add-only seed sync cannot.

The reported symptom (R3081, from `predatory-publisher-tarpit` via `rules-review`): two FAIL
rows at the start of EVERY run of that routine, because `outbound-mail`'s `requires:` still
named `fau-mail` and `gmail` after those two utils were consolidated into `mail`. A routine
cannot edit the library, so nothing inside it could fix this; and `sync_seed_library_docs` is
add-only, so correcting the seed alone would reach a fresh instance and never a live one.

What is pinned here is the narrowness, because that is what makes a text migration safe to run
over operator-editable files:

* it corrects the file that names the dead utils;
* it leaves the PROSE below the frontmatter byte for byte — the body is his;
* it leaves a file he already corrected alone, so the migration can never revert a
  hand-correction (the shape `migrate_rule_assists` uses when a live declaration outranks the
  seed's);
* it runs ONCE — the record is the guard, as with every migration in this family.
"""

from __future__ import annotations

import json
from pathlib import Path

from helpers import server_config
from rsched.migrate_permission_requires import RECORD, REQUIRES, run_migration

DEAD, LIVE = REQUIRES["outbound-mail"]

BODY = """\
# permission: outbound-mail — send email from the operator's own mailboxes

Send the text as it stands in his approval, not your draft (he edits drafts).
"""


def _doc(utils: str) -> str:
    return ("---\n"
            "effect:\n"
            "  with: sends email in your name\n"
            "  without: reads and drafts, never sends\n"
            "  when: correspondence is part of the task\n"
            "tags: [communication, email, outbound]\n"
            "requires:\n"
            f"  utils: {utils}\n"
            "---\n") + BODY


def _server(tmp_path: Path):
    home, library = tmp_path / "routines", tmp_path / "library"
    (library / "permissions").mkdir(parents=True)
    (home / ".control").mkdir(parents=True)
    return server_config(routines_home=home, libraries_home=library)


def test_the_dead_util_names_are_corrected_and_the_prose_is_untouched(tmp_path):
    server = _server(tmp_path)
    live = Path(server.libraries_home) / "permissions" / "outbound-mail.md"
    live.write_text(_doc(DEAD), encoding="utf-8")

    record = run_migration(server)

    assert record["carried"] == ["permissions/outbound-mail.md"], record
    text = live.read_text(encoding="utf-8")
    assert f"utils: {LIVE}" in text
    assert "gmail" not in text and "fau-mail" not in text
    assert text.endswith(BODY), "the body prose is the operator's and must survive verbatim"


def test_a_file_he_already_corrected_is_left_alone(tmp_path):
    """The migration may never revert a hand-correction: if the live `requires:` no longer
    names the dead utils, there is nothing to carry and the file is not written at all."""
    server = _server(tmp_path)
    live = Path(server.libraries_home) / "permissions" / "outbound-mail.md"
    already = _doc("[mail:send, zulip:send]")
    live.write_text(already, encoding="utf-8")

    record = run_migration(server)

    assert record["carried"] == []
    assert "already corrected by hand" in record["left"]["outbound-mail"]
    assert live.read_text(encoding="utf-8") == already


def test_a_permission_missing_from_the_live_library_stays_missing(tmp_path):
    """He may have deleted it. Resurrecting a deleted library doc is the one thing the
    add-only sync already refuses, and a migration must not route around that."""
    server = _server(tmp_path)
    record = run_migration(server)

    assert record["carried"] == []
    assert "deleted permissions stay deleted" in record["left"]["outbound-mail"]
    assert not (Path(server.libraries_home) / "permissions" / "outbound-mail.md").exists()


def test_it_runs_once(tmp_path):
    server = _server(tmp_path)
    live = Path(server.libraries_home) / "permissions" / "outbound-mail.md"
    live.write_text(_doc(DEAD), encoding="utf-8")

    first = run_migration(server)
    assert first["carried"]
    assert (Path(server.routines_home) / RECORD).exists()

    # a file that somehow regressed is NOT corrected a second time — the record is the guard
    live.write_text(_doc(DEAD), encoding="utf-8")
    assert run_migration(server) == {}
    assert DEAD in live.read_text(encoding="utf-8")


PATTERN_YAML = """\
slug: mailbox-service
name: Serve one mailbox
settings:
  capabilities:
    actions:
    - revise_util
    - write_util
    utils:
{utils}    confirm: creations
questions:
- which mailbox, and which senders matter?
"""

DEAD_ITEMS = "    - fau-mail:send\n    - gmail:send\n"
LIVE_ITEMS = "    - mail:send\n"


def test_a_pattern_reserving_the_dead_verbs_is_corrected(tmp_path):
    """The half the repo's own test found: `floor_capabilities` keeps a reserved util only
    while a HELD doc requires it, so once `outbound-mail` names `mail:send` a pattern naming
    only the dead verbs hands out a reservation the first save floors straight back out."""
    server = _server(tmp_path)
    (Path(server.libraries_home) / "patterns").mkdir(parents=True)
    live = Path(server.libraries_home) / "patterns" / "mailbox-service.yaml"
    live.write_text(PATTERN_YAML.format(utils=DEAD_ITEMS), encoding="utf-8")

    record = run_migration(server)

    assert "patterns/mailbox-service.yaml" in record["carried"], record
    text = live.read_text(encoding="utf-8")
    assert text == PATTERN_YAML.format(utils=LIVE_ITEMS)
    assert "gmail" not in text and "fau-mail" not in text


def test_a_pattern_he_already_corrected_is_left_alone(tmp_path):
    server = _server(tmp_path)
    (Path(server.libraries_home) / "patterns").mkdir(parents=True)
    live = Path(server.libraries_home) / "patterns" / "mailbox-service.yaml"
    already = PATTERN_YAML.format(utils=LIVE_ITEMS)
    live.write_text(already, encoding="utf-8")

    record = run_migration(server)

    assert "patterns/mailbox-service.yaml" not in record["carried"]
    assert "already corrected by hand" in record["left"]["patterns/mailbox-service.yaml"]
    assert live.read_text(encoding="utf-8") == already


def test_a_pattern_missing_from_the_live_library_stays_missing(tmp_path):
    server = _server(tmp_path)
    (Path(server.libraries_home) / "patterns").mkdir(parents=True)

    record = run_migration(server)

    assert not any(r.startswith("patterns/") for r in record["carried"])
    assert "deleted patterns stay deleted" in record["left"]["patterns/radar.yaml"]


def test_the_record_is_readable_json_naming_what_it_did(tmp_path):
    """A migration nobody can read the outcome of is a migration nobody can trust ran."""
    server = _server(tmp_path)
    live = Path(server.libraries_home) / "permissions" / "outbound-mail.md"
    live.write_text(_doc(DEAD), encoding="utf-8")
    run_migration(server)

    doc = json.loads((Path(server.routines_home) / RECORD).read_text(encoding="utf-8"))
    assert doc["carried"] == ["permissions/outbound-mail.md"]
    assert doc["started"]
