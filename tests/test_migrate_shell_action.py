"""MIGRATION(expires=2026-10-03) guard: a holder of the reserved `shell` util keeps its hatch.

`shell` is an ACTION KIND from 0.287.0. On the live instance 14 routines (plus a dozen
conversations) still name it under `capabilities.utils`, where it would gate nothing and switch
nothing on — the permission would say they hold the escape hatch and the engine would refuse
every call. The contract asserted here is therefore the EFFECTIVE one: after the migration the
routine's policy allows the kind, and the reserved-util entry is gone rather than left inert.

Which surfaces exist is answered by the RUNNING instance, never by reading the tree the migration
was written in. The first deploy converted every routine file while a live shared config layer
went on handing the dead entry to the four routines that inherited it — a permission failing
closed that only `rsched validate` against the real instance reports.

The TEMPLATE half is the worst-shaped one: it produces NEW broken routines rather than holding
old ones wrong. A settings template's `config` block is stamped into every routine created from
it. Nothing converges a live template — `seed_libraries` writes templates only when the repo is
created and `sync_seed_library_docs` is add-only by design.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import yaml

from rsched.config import load_routine
from rsched.migrate_shell_action import migrate_shell_action
from rsched.policyload import load_policy

SEED_DOC = Path(__file__).resolve().parents[1] / "library-seed" / "permissions" / "shell.md"
SEED_TEMPLATES = Path(__file__).resolve().parents[1] / "library-seed" / "templates"
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


STALE_TEMPLATE = """---
tags: [meta]
config:
  permissions:
  - shell
  capabilities:
    actions:
    - memory_read
    utils:
    - shell
---
# template: maintainer — maintains this instance

Body prose.
"""


def _server(tmp_path: Path):
    lib = tmp_path / "lib"
    (lib / "permissions").mkdir(parents=True)
    (lib / "templates").mkdir(parents=True)
    (lib / "templates" / "maintainer.md").write_text(STALE_TEMPLATE, encoding="utf-8")
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


def test_a_settings_template_stops_minting_broken_routines(tmp_path):
    """The TEMPLATE surface. A template grants through its `config` block, so an unconverted one
    keeps stamping `capabilities.utils: [shell]` into every routine created from it — the
    permission held, the action off, and a util named that no longer exists.
    """
    from rsched.templates import config_for

    server = _server(tmp_path)
    assert migrate_shell_action(server) is True

    caps = config_for(server.libraries_home, "maintainer")["capabilities"]
    assert "shell" not in caps["utils"]
    assert "shell" in caps["actions"]
    seed = SEED_TEMPLATES / "maintainer.md"
    assert (server.libraries_home / "templates" / "maintainer.md").read_text(encoding="utf-8") \
        == seed.read_text(encoding="utf-8")


def test_a_template_that_never_granted_the_util_is_untouched(tmp_path):
    server = _server(tmp_path)
    plain = server.libraries_home / "templates" / "watcher.md"
    plain.write_text("---\nconfig:\n  capabilities:\n    utils: []\n---\n# t\n",
                     encoding="utf-8")
    before = plain.read_text(encoding="utf-8")
    migrate_shell_action(server)
    assert plain.read_text(encoding="utf-8") == before
