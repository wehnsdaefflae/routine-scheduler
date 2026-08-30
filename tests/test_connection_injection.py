"""Engine-side OAuth token injection: `_child_env` gives a util an engine-provided access token
ONLY if the util declares the var (the declared-only invariant, extended to non-store secrets), and
`exec_env._connection_env` resolves a routine's `connections:` bindings to those env vars."""

from __future__ import annotations

from types import SimpleNamespace

from rsched import utils_run
from rsched.engine.exec_env import _connection_env
from rsched.oauth import store
from rsched.oauth.store import Connection

DECLARING = '''"""notionish — declares the OAuth token var.

usage: gu notionish
tags: test
secrets: NOTION_ACCESS_TOKEN
net: outbound
fs: roots
"""
print("hi")
'''

PLAIN = '''"""plainish — declares no secrets.

usage: gu plainish
tags: test
"""
print("hi")
'''


def _lib(tmp_path, name, body):
    d = tmp_path / "utils" / name
    d.mkdir(parents=True)
    (d / "main.py").write_text(body, encoding="utf-8")
    return tmp_path


def test_declared_extra_is_injected(tmp_path):
    home = _lib(tmp_path, "notionish", DECLARING)
    env = utils_run._child_env(home, "notionish", {"NOTION_ACCESS_TOKEN": "AT"})
    assert env["NOTION_ACCESS_TOKEN"] == "AT"


def test_undeclared_extra_is_absent(tmp_path):
    home = _lib(tmp_path, "plainish", PLAIN)
    env = utils_run._child_env(home, "plainish", {"NOTION_ACCESS_TOKEN": "AT"})
    assert "NOTION_ACCESS_TOKEN" not in env


def test_undeclared_extra_scrubbed_even_if_inherited(tmp_path, monkeypatch):
    # the invariant: an undeclared secret must not reach the child by ANY route, incl. inherited env
    monkeypatch.setenv("NOTION_ACCESS_TOKEN", "leaked-from-daemon-env")
    home = _lib(tmp_path, "plainish", PLAIN)
    env = utils_run._child_env(home, "plainish", {"NOTION_ACCESS_TOKEN": "AT"})
    assert "NOTION_ACCESS_TOKEN" not in env


def test_connection_env_resolves_bindings(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "connections_path", lambda: tmp_path / "connections.json")
    store.set_connection(Connection(provider="notion", account="acme", access_token="AT"))
    bound = SimpleNamespace(routine=SimpleNamespace(connections={"notion": "acme"}),
                            granted_now=set(), grant_args={})
    assert _connection_env(bound) == {"NOTION_ACCESS_TOKEN": "AT"}
    unbound = SimpleNamespace(routine=SimpleNamespace(connections={}),
                              granted_now=set(), grant_args={})
    assert _connection_env(unbound) == {}
