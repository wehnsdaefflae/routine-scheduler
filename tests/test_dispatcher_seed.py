"""The `gu` dispatcher as tracked repo code (`util-seed/gu`): `gu` is what every `util` action
ultimately invokes, and what reads a util's `net:`/`secrets:` header — the declarations that
decide which network access and which credentials fold into its sandbox.
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys

import pytest

from rsched.paths import repo_root

GU = repo_root() / "util-seed" / "gu"


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

