"""The `gu` dispatcher as tracked repo code (`util-seed/gu`) and the one-shot that carries it
to a live library
(MIGRATION(expires=2027-01-31)): `gu` is what every `util` action ultimately invokes, and what
reads a util's `net:`/`secrets:` header — the declarations that decide which network access and
which credentials fold into its sandbox. Until it became a repo file nothing versioned, tested
or gated that. `utils_lib.ensure_library` installs a MINIMAL dispatcher and never overwrites an
existing one, so on an instance carrying that stub only a migration can replace it.
"""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from rsched import libgit
from rsched import migrate_dispatcher_seed as mig
from rsched.paths import repo_root
from rsched.utils_lib import DISPATCHER

GU = repo_root() / mig.SEED
SEED_BYTES = GU.read_bytes()
STUB_BYTES = DISPATCHER.encode()


# ------------------------------------------------------------------ the tracked file itself

def test_the_tracked_dispatcher_parses_and_answers_as_an_executable():
    """It is the live executable's source, not a description of it: it parses, and running it
    the way PATH runs it answers."""
    source = GU.read_text(encoding="utf-8")
    ast.parse(source)
    assert source.startswith("#!/usr/bin/env python3")
    r = subprocess.run([sys.executable, str(GU), "--help"],
                       capture_output=True, text=True, check=False, timeout=60)
    assert r.returncode == 0, r.stderr
    assert "gu — dispatcher for the global utils library" in r.stdout


def test_the_tracked_dispatcher_is_the_rich_one_the_stub_is_not():
    """What the migration rests on: the seed carries the verbs `utils_lib.DISPATCHER` lacks."""
    source = GU.read_text(encoding="utf-8")
    assert source != DISPATCHER
    for verb in ("list", "help", "lint", "deps", "remove", "index"):
        assert f'cmd == "{verb}"' in source
    assert 'cmd == "lint"' not in DISPATCHER


# ------------------------------------------------- what it decides about secrets and network

def _util_source(*, net: str | None = "none", secrets: str = "(none)", body: str = "") -> str:
    """A fixture util that lints CLEAN but for whatever the caller changes."""
    lines = ["# /// script", '# requires-python = ">=3.12"', "# dependencies = []", "# ///",
             '"""probe — a fixture util for the dispatcher\'s header gate.', "",
             "usage: gu probe [--json] [--selftest]", "calls: (none)", "tags: fixture"]
    if net is not None:
        lines.append(f"net: {net}")
    lines += [f"secrets: {secrets}", '"""', body]
    return "\n".join(lines) + "\n"


def _lint(tmp_path, source: str) -> tuple[int, str]:
    """`gu lint probe` against a throwaway library holding exactly that util. No util is ever
    executed (lint only reads) and nothing touches the network."""
    home = tmp_path / "lib"
    (home / "utils" / "probe").mkdir(parents=True)
    (home / "utils" / "probe" / "main.py").write_text(source, encoding="utf-8")
    r = subprocess.run([sys.executable, str(GU), "lint", "probe"],
                       capture_output=True, text=True, check=False, timeout=60,
                       env={**os.environ, "GLOBAL_UTILS_HOME": str(home)})
    return r.returncode, r.stdout + r.stderr


def test_a_clean_header_lints_clean(tmp_path):
    """The baseline the other cases are measured against — otherwise a green/red could come
    from anything in the fixture."""
    code, out = _lint(tmp_path, _util_source())
    assert code == 0, out
    assert "probe: ok" in out


SECRET_READ = 'import os\nTOKEN = os.environ["PROBE_API_TOKEN"]\n'


def test_a_credential_read_that_is_not_declared_is_refused(tmp_path):
    """The sandbox injects only DECLARED secrets, so an undeclared read arrives empty — the
    dispatcher is where that disagreement is caught."""
    code, out = _lint(tmp_path, _util_source(secrets="(none)", body=SECRET_READ))
    assert code == 1
    assert "PROBE_API_TOKEN" in out
    assert "injects only DECLARED secrets" in out


@pytest.mark.parametrize("declared", ["PROBE_API_TOKEN", "PROBE_API_TOKEN?", "probe_api_token"])
def test_a_declared_credential_passes_however_it_is_spelled(tmp_path, declared):
    """A trailing `?` marks an OPTIONAL secret and is part of the DECLARATION, not of the
    variable name; the engine compares case-insensitively."""
    code, out = _lint(tmp_path, _util_source(secrets=declared, body=SECRET_READ))
    assert code == 0, out
    assert "probe: ok" in out


@pytest.mark.parametrize("net", [None, "maybe", ""])
def test_network_access_must_be_declared_as_one_of_the_two_values(tmp_path, net):
    """Undeclared is DENIED at run time (fail closed), so a util that declares nothing lints
    red here instead of failing mysteriously inside the jail."""
    code, out = _lint(tmp_path, _util_source(net=net))
    assert code == 1
    assert "denies ALL TCP" in out


@pytest.mark.parametrize("net", ["outbound", "none"])
def test_the_two_network_values_are_accepted(tmp_path, net):
    code, out = _lint(tmp_path, _util_source(net=net))
    assert code == 0, out


# ------------------------------------------------------------------ the one-shot migration

def _instance(tmp_path, monkeypatch, live: bytes | None):
    repo = tmp_path / "repo"
    (repo / mig.SEED).parent.mkdir(parents=True)
    (repo / mig.SEED).write_bytes(SEED_BYTES)
    monkeypatch.setattr("rsched.bootstrap.repo_root", lambda: repo)
    server = SimpleNamespace(routines_home=tmp_path / "routines",
                             libraries_home=tmp_path / "lib")
    server.routines_home.mkdir()
    server.libraries_home.mkdir()
    if live is not None:
        (server.libraries_home / "gu").write_bytes(live)
        (server.libraries_home / "gu").chmod(0o755)
    return server


@pytest.mark.parametrize(("live", "left"), [
    (b"#!/usr/bin/env python3\n# the operator's own dispatcher\n", "by hand"),
    (SEED_BYTES, "already current"),
    (None, "not read"),                         # a dispatcher deleted live stays deleted
], ids=["an-operators-own", "already-the-seed", "deleted-live"])   # the seed's 17 kB is not an id
def test_anything_but_the_stub_is_left_untouched_and_named(tmp_path, monkeypatch, live, left):
    server = _instance(tmp_path, monkeypatch, live)
    record = mig.run_migration(server)
    path = server.libraries_home / "gu"
    assert (path.read_bytes() if path.exists() else None) == live
    assert left in record["left"] and "replaced" not in record
    assert not (server.libraries_home / mig.BACKUP).exists()


def test_the_stub_is_replaced_backed_up_committed_and_carried_once(tmp_path, monkeypatch):
    server = _instance(tmp_path, monkeypatch, STUB_BYTES)
    lib = server.libraries_home
    libgit.init_repo(lib, first_commit="init")

    record = mig.run_migration(server)

    live = lib / "gu"
    assert live.read_bytes() == SEED_BYTES
    assert live.stat().st_mode & 0o111 == 0o111          # a dispatcher that loses +x is dead
    assert (lib / mig.BACKUP).read_bytes() == STUB_BYTES  # nothing that was there is lost
    assert record["replaced"] == "gu"
    assert record["backup"].endswith(mig.BACKUP)
    assert libgit.git(lib, "log", "-1", "--format=%s").stdout.startswith("gu: the tracked")
    assert libgit.git(lib, "status", "--porcelain").stdout.strip() == ""   # boot ends clean
    stored = json.loads((server.routines_home / mig.RECORD).read_text(encoding="utf-8"))
    assert stored["replaced"] == "gu" and stored["backup"] == record["backup"]

    live.write_bytes(STUB_BYTES)                 # a later boot finds the record and does nothing
    assert mig.run_migration(server) == {}
    assert live.read_bytes() == STUB_BYTES


def test_a_stand_in_server_without_the_homes_cannot_kill_the_boot():
    """R3132's shape: `cmd_daemon` is handed a four-attribute stand-in by the fresh-container
    boot test, so every attribute read here is a `getattr` — a hard read would fail the whole
    daemon boot for a question this migration was not asked."""
    assert mig.run_migration(SimpleNamespace()) == {}
    assert mig.run_migration(SimpleNamespace(routines_home="")) == {}
    assert mig.run_migration(SimpleNamespace(libraries_home="")) == {}
