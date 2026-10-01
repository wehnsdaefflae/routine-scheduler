"""A util revision that reaches something new is asked about at every approval level: it is a
new grant for every routine calling the util, in the shape of a fix."""
from types import SimpleNamespace

from rsched import utils_header, utils_lib
from rsched.engine import authoring
from rsched.grantpolicy import GrantPolicy


def _util(secrets="(none)", net="none", fs="none", calls="(none)", body="print('hi')"):
    return (f'"""probe — a test util.\n\nusage: gu probe [--selftest]\ntags: a, b, c\n'
            f'secrets: {secrets}\ncalls: {calls}\nnet: {net}\nfs: {fs}\n"""\n{body}\n')


def test_widening_names_what_is_new():
    base = _util()
    assert utils_header.widening(base, _util(body="print('changed')")) == []
    assert utils_header.widening(base, _util(secrets="API_KEY?")) == ["the secret API_KEY"]
    assert utils_header.widening(base, _util(net="outbound")) == ["the network"]
    assert utils_header.widening(base, _util(fs="roots")) == ["the routine's folders"]
    assert utils_header.widening(base, _util(fs="rw ~/x")) == ["writing ~/x"]
    assert utils_header.widening(_util(fs="rw ~/x"), _util(fs="ro ~/x")) == []   # narrower
    assert utils_header.widening(_util(net="outbound"), base) == []


def _loop(home, confirm):
    ctx = SimpleNamespace(server=SimpleNamespace(libraries_home=home, libraries_remote="",
                                                 routines_home=None),
                          depth=0, routine=SimpleNamespace(dir=home, slug="r"),
                          grants=GrantPolicy(actions=frozenset({"write_util", "revise_util"}),
                                             confirm=confirm),
                          granted_now={"approval:write_util"}, aborted=lambda: False)
    return SimpleNamespace(ctx=ctx)


def _write(tmp_path, monkeypatch, confirm, content):
    asked: list[str] = []
    monkeypatch.setattr(authoring, "handle_ask",
                        lambda _loop, ask, _poll, **_: asked.append(ask["question"]) or
                        {"answered": False, "qid": "q-1"})
    monkeypatch.setattr(authoring.utils_run, "selftest", lambda *a, **k: (True, "ok"))
    monkeypatch.setattr(authoring.sandbox, "base_policy", lambda _server: None)
    monkeypatch.setattr(authoring.utils_lib, "git_commit", lambda *a, **k: True)
    monkeypatch.setattr(authoring, "_impact_note", lambda *a: "")
    out = authoring.handle_write_util(_loop(tmp_path, confirm),
                                      {"kind": "write_util", "name": "probe",
                                       "content": content}, 0.01)
    return out, asked


def test_a_widening_revision_asks_even_when_revisions_are_autonomous(tmp_path, monkeypatch):
    utils_lib.ensure_library(tmp_path)
    utils_lib.write_util_file(tmp_path, "probe", _util())
    out, asked = _write(tmp_path, monkeypatch, "never", _util(net="outbound"))
    assert out.get("pending_approval") and "reach the network" in asked[0]


def test_a_revision_reaching_nothing_new_follows_the_setting(tmp_path, monkeypatch):
    utils_lib.ensure_library(tmp_path)
    utils_lib.write_util_file(tmp_path, "probe", _util())
    out, asked = _write(tmp_path, monkeypatch, "creations", _util(body="print('fixed')"))
    assert asked == [] and out.get("selftest_ok")
