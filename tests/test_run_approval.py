"""D137: confirmation approval is scoped to one run, never a capability grant."""
from types import SimpleNamespace

import pytest

from rsched.engine import authoring
from rsched.engine.childrun import inheritable_resources
from rsched.grantpolicy import GrantPolicy

BODY = '''"""sample — test.
usage: gu sample
tags: test
net: none
fs: roots
"""
'''
SCOPED = "approve this kind for the rest of this run"


@pytest.mark.parametrize(("answer", "expected"), [(SCOPED, 1), ("approve", 2),
    ("  APPROVE THIS KIND FOR THE REST OF THIS RUN  ", 1),
    (SCOPED + ", except other utils", 2)])
def test_approval_scope_and_fresh_run(tmp_path, monkeypatch, answer, expected):
    prompts = []
    tests = []
    ctx = SimpleNamespace(depth=0, server=SimpleNamespace(
        libraries_home=tmp_path, libraries_remote="", sandbox="off"),
        grants=GrantPolicy(confirm="always"), granted_now=set())
    loop = SimpleNamespace(ctx=ctx)
    monkeypatch.setattr(authoring.utils_lib, "ensure_library", lambda *a, **k: None)
    monkeypatch.setattr(authoring.utils_lib, "exists", lambda *a: False)
    monkeypatch.setattr(authoring.utils_lib, "write_util_file", lambda *a: None)
    monkeypatch.setattr(authoring.utils_lib, "git_commit", lambda *a, **k: None)
    monkeypatch.setattr(authoring, "_impact_note", lambda *a: "")

    def selftest(*args, **kwargs):
        tests.append(True)
        return True, "ok"

    def ask(*args, **kwargs):
        prompts.append(args[1])
        return {"answered": True, "answer": answer}

    monkeypatch.setattr(authoring.utils_run, "selftest", selftest)
    monkeypatch.setattr(authoring, "handle_ask", ask)
    action = {"kind": "write_util", "name": "sample", "content": BODY}
    assert authoring.handle_write_util(loop, action, 0.01)["selftest_ok"]
    assert authoring.handle_write_util(loop, action, 0.01)["selftest_ok"]
    assert len(prompts) == expected
    assert len(tests) == 2
    assert SCOPED in prompts[0]["options"]
    assert not GrantPolicy().with_overlay(ctx.granted_now, set()).actions
    assert inheritable_resources(ctx.granted_now, set()) == set()
    ctx.granted_now = set()
    assert authoring.handle_write_util(loop, action, 0.01)["selftest_ok"]
    assert len(prompts) == expected + 1
