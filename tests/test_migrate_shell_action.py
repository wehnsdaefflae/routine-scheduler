"""MIGRATION(expires=2026-10-03) guard: a holder of the reserved `shell` util keeps its hatch.

`shell` is an ACTION KIND from 0.287.0. On the live instance 14 routines (plus a dozen
conversations) still name it under `capabilities.utils`, where it would gate nothing and switch
nothing on — the permission would say they hold the escape hatch and the engine would refuse
every call. The contract asserted here is therefore the EFFECTIVE one: after the migration the
routine's policy allows the kind, and the reserved-util entry is gone rather than left inert.

The GROUP half has its own test because it was the miss: a group's config is a LIVE layer (D82),
it lives in `groups.json` rather than in any routine dir, and the first deploy converted every
routine file while leaving the FAU group re-supplying the dead entry to its four members —
`rsched validate` against the running instance is what caught it.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import yaml

from rsched.config import load_routine
from rsched.migrate_shell_action import migrate_shell_action
from rsched.policyload import load_policy

SEED_DOC = Path(__file__).resolve().parents[1] / "library-seed" / "permissions" / "shell.md"
OLD_DOC = """---
effect:
  with: run arbitrary shell commands on the host
  without: runs code only through selftested, sandboxed utils
  when: the task genuinely cannot be done by a util — this is the escape hatch
tags: [tool-use, shell, escape-hatch]
requires:
  utils: [shell]
---
# permission: shell — run arbitrary shell commands (escape hatch)

Unlocks the reserved `shell` util: one-off shell commands on the host
(`util` name `shell`, args `["<command>", "--json"]`).
"""


def _server(tmp_path: Path):
    lib = tmp_path / "lib"
    (lib / "permissions").mkdir(parents=True)
    (lib / "utils" / "shell").mkdir(parents=True)
    (lib / "utils" / "shell" / "main.py").write_text('"""shell — old."""\n', encoding="utf-8")
    (lib / "permissions" / "shell.md").write_text(OLD_DOC, encoding="utf-8")
    home = tmp_path / "routines"
    home.mkdir()
    return SimpleNamespace(routines_home=home, conversations_home=tmp_path / "conv",
                           background_home=tmp_path / "bg", libraries_home=lib)


def _routine(server, slug: str, caps: dict) -> Path:
    d = server.routines_home / slug
    d.mkdir(parents=True)
    (d / "routine.yaml").write_text(
        yaml.safe_dump({"description": "t", "permissions": ["shell"], "capabilities": caps}),
        encoding="utf-8")
    return d


def test_a_holder_ends_up_with_the_action_and_the_policy_allows_it(tmp_path):
    server = _server(tmp_path)
    d = _routine(server, "operator", {"actions": ["memory_read"], "utils": ["shell", "remote"]})

    assert migrate_shell_action(server) is True

    cfg, _ = load_routine(d)
    assert cfg.capabilities["utils"] == ["remote"]           # the inert entry is gone
    assert "shell" in cfg.capabilities["actions"]
    policy = load_policy(server.libraries_home / "permissions", cfg.permissions,
                         cfg.capabilities)
    assert policy.allows_kind("shell") is True               # the hatch actually works again
    assert policy.deny({"kind": "shell", "command": "true"}) is None


def test_the_library_doc_is_replaced_wholesale_and_the_util_deleted(tmp_path):
    """Frontmatter alone would not do: the BODY is inlined into every holder's prompt, so a
    doc still telling 14 routines to call `util name=shell` teaches a rejected call."""
    server = _server(tmp_path)
    _routine(server, "operator", {"utils": ["shell"]})

    migrate_shell_action(server)

    doc = (server.libraries_home / "permissions" / "shell.md").read_text(encoding="utf-8")
    assert doc == SEED_DOC.read_text(encoding="utf-8")
    assert "requires:\n  actions: [shell]" in doc
    assert not (server.libraries_home / "utils" / "shell").exists()


def test_a_verb_scoped_grant_converts_too(tmp_path):
    """An action kind has no verbs, so `shell:something` must become the plain capability —
    left behind it would read as a grant and switch nothing on."""
    server = _server(tmp_path)
    d = _routine(server, "scoped", {"utils": ["shell:read"]})
    migrate_shell_action(server)
    cfg, _ = load_routine(d)
    assert cfg.capabilities["utils"] == []
    assert cfg.capabilities["actions"] == ["shell"]


def test_it_is_idempotent_and_leaves_non_holders_alone(tmp_path):
    server = _server(tmp_path)
    holder = _routine(server, "operator", {"utils": ["shell"]})
    other = _routine(server, "plain", {"actions": ["memory_read"], "utils": ["discord"]})
    before = (other / "routine.yaml").read_text(encoding="utf-8")

    migrate_shell_action(server)
    assert (other / "routine.yaml").read_text(encoding="utf-8") == before
    after = (holder / "routine.yaml").read_text(encoding="utf-8")

    assert migrate_shell_action(server) is False             # nothing left to convert
    assert (holder / "routine.yaml").read_text(encoding="utf-8") == after


def _group(server, gid: str, caps: dict, members: list[str]) -> Path:
    from rsched.groups import groups_file

    path = groups_file(server.routines_home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "default_on_failure": "stop",
        "groups": [{"id": gid, "name": "FAU", "cron": "0 5 * * *",
                    "members": [{"slug": s} for s in members],
                    "config": {"permissions": ["shell"], "capabilities": caps}}],
    }), encoding="utf-8")
    return path


def test_a_group_config_block_is_converted_too(tmp_path):
    """A group's config is a live layer, so an unconverted block keeps handing every member the
    dead `utils:` entry — the permission then fails closed for all of them."""
    from rsched.groups import list_groups

    server = _server(tmp_path)
    path = _group(server, "grp-fau", {"actions": ["memory_read"], "utils": ["fau-mail", "shell"]},
                  ["ards", "nanogeofeld"])

    assert migrate_shell_action(server) is True

    caps = list_groups(server.routines_home)[0]["config"]["capabilities"]
    assert caps["utils"] == ["fau-mail"]
    assert "shell" in caps["actions"]
    # the rest of the store is untouched — the patch is raw, not a normalize round-trip
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["groups"][0]["cron"] == "0 5 * * *"
    assert raw["groups"][0]["config"]["permissions"] == ["shell"]
    assert [m["slug"] for m in raw["groups"][0]["members"]] == ["ards", "nanogeofeld"]
    assert migrate_shell_action(server) is False


def test_a_member_inheriting_the_group_block_ends_up_able_to_use_the_kind(tmp_path):
    """The effective contract, through the group merge a member actually loads."""
    server = _server(tmp_path)
    d = _routine(server, "ards", {"actions": ["memory_read"], "utils": []})
    _group(server, "grp-fau", {"utils": ["shell"]}, ["ards"])

    migrate_shell_action(server)

    from rsched.config.groupconfig import apply_group_config
    from rsched.groups import list_groups
    raw = yaml.safe_load((d / "routine.yaml").read_text(encoding="utf-8"))
    merged, _ = apply_group_config(raw, list_groups(server.routines_home)[0]["config"])
    policy = load_policy(server.libraries_home / "permissions", merged["permissions"],
                         merged["capabilities"])
    assert policy.allows_kind("shell") is True
