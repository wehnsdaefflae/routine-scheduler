"""The grant-entity vocabulary: id parsing per class, fs canonicalization, the
never-grantable credential stores, and the routine.yaml `grants:` normalization rules
(true rows only where no native switch exists)."""

from pathlib import Path

from rsched import entities


def test_parse_entity_accepts_every_class():
    assert entities.parse_entity("action:write_util") == ("action", "write_util")
    assert entities.parse_entity("util:discord") == ("util", "discord")
    assert entities.parse_entity("secret:FOO_KEY") == ("secret", "FOO_KEY")
    assert entities.parse_entity("connection:google") == ("connection", "google")
    assert entities.parse_entity("machine:omen-laptop") == ("machine", "omen-laptop")
    assert entities.parse_entity("runs:last") == ("runs", "last")
    assert entities.parse_entity("runs:all") == ("runs", "all")
    assert entities.parse_entity("workflows:generate") == ("workflows", "generate")
    assert entities.parse_entity("recreate:doomed-util") == ("recreate", "doomed-util")


def test_parse_entity_rejects_malformed_ids():
    assert entities.parse_entity("write_util") is None            # no class
    assert entities.parse_entity("nonsense:x") is None            # unknown class
    assert entities.parse_entity("action:util") is None           # base kind, not gated
    assert entities.parse_entity("action:no-such-kind") is None
    assert entities.parse_entity("util:Not A Slug") is None
    assert entities.parse_entity("secret:not-a-var!") is None
    assert entities.parse_entity("runs:sometimes") is None        # not a depth level
    assert entities.parse_entity("workflows:catalog") is None     # the baseline needs no grant
    assert entities.parse_entity("util:") is None
    assert entities.parse_entity(42) is None
    assert entities.parse_entity(None) is None


def test_fs_entities_canonicalize_to_absolute_paths():
    cls, name = entities.parse_entity("fs-write:~/project")
    assert cls == "fs-write" and Path(name).is_absolute() and "~" not in name
    # the same directory always yields the same id
    assert entities.canonical("fs-write:~/project") == entities.canonical(
        f"fs-write:{Path('~/project').expanduser()}")


def test_never_grantable_fs_guards_the_credential_stores():
    assert entities.never_grantable_fs("~/.ssh")
    assert entities.never_grantable_fs("~/.ssh/id_ed25519")          # inside
    assert entities.never_grantable_fs("~")                          # contains them
    assert entities.never_grantable_fs("~/.config/routine-scheduler/secrets.env")
    assert not entities.never_grantable_fs("~/projects/site")


def test_is_resource_separates_the_classes():
    assert entities.is_resource("secret:FOO")
    assert entities.is_resource("fs-write:/tmp/x")
    assert entities.is_resource("connection:google")
    assert entities.is_resource("machine:omen")
    assert not entities.is_resource("action:write_util")
    assert not entities.is_resource("util:discord")
    assert not entities.is_resource("recreate:doomed")
    assert not entities.is_resource("not-an-id")


def test_normalize_grants_keeps_valid_rows_and_reports_the_rest():
    out, problems = entities.normalize_grants({
        "secret:FOO_KEY": True,           # legal true row (no native switch)
        "util:discord": False,            # legal tombstone
        "util:discord2": "yes",           # non-bool → dropped
        "bogus": False,                   # unparseable id → dropped
        "util:page-fetch": True,          # true row outside secret:* → dropped
    })
    assert out == {"secret:FOO_KEY": True, "util:discord": False}
    assert len(problems) == 3
    assert any("not an entity id" in p for p in problems)
    assert any("true (allowed) or false" in p for p in problems)
    assert any("only valid for secret:*" in p for p in problems)


def test_normalize_grants_handles_absent_and_junk():
    assert entities.normalize_grants(None) == ({}, [])
    out, problems = entities.normalize_grants(["not", "a", "mapping"])
    assert out == {} and problems and "mapping" in problems[0]
