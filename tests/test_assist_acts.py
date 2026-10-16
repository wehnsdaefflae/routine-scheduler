"""The assist predicates that read an ACT — a message going out as the user, a web search
coming back, code written, a tool built, a tracked file removed, feedback read.

Each keys on what the library DECLARES about the thing acted on (a permission doc's tags, a
util's header), never on a util's name, and each misses rather than guesses.
"""

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from helpers import run_context, server_config, util_library
from rsched.engine.assist_acts import ACTS
from rsched.engine.assist_predicates import PREDICATES, Situation
from rsched.grantpolicy import GrantPolicy

TS = "20261009-120000"


def _doc(home, slug: str, tags: str) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / f"{slug}.md").write_text(f"---\ntags: [{tags}]\n---\n# permission: {slug} — x\n",
                                     encoding="utf-8")


def _header(name: str, *, tags: str, net: str) -> str:
    return f'"""{name} — t.\n\nusage: gu {name}\ntags: {tags}\nnet: {net}\nfs: none\n"""\n'


@pytest.fixture
def ctx(make_routine, tmp_path):
    d = make_routine(slug="acts")
    lib = tmp_path / "lib"
    _doc(lib / "permissions", "outbound-mail", "communication, email, outbound, as-operator")
    _doc(lib / "permissions", "notifications", "notifications, attention, outbound")
    _doc(lib / "permissions", "messaging-discord", "communication, messaging, outbound")
    for name, tags, net in (("websearch", "web, research, search", "outbound"),
                            ("code-search", "code, search, grep", "none"),
                            ("llm-complete", "llm, research", "outbound"),
                            ("feedback-pending", "feedback, steward", "none")):
        util_library(lib, name, _header(name, tags=tags, net=net))
    grants = GrantPolicy(gated_utils={"ntfy": ("notifications",)},
                         gated_verbs={"gmail": {"send": ("outbound-mail",)},
                                      "discord": {"send": ("messaging-discord",)}})
    return run_context(d, TS, grants=grants, server=server_config(libraries_home=lib))


def _check(ctx, name: str, action: dict, obs: dict | None = None) -> bool:
    loop = SimpleNamespace(ctx=ctx)
    return PREDICATES[name].check(Situation(loop=loop, action=action, obs=obs))


def _util(name: str, *args: str, **extra) -> dict:
    return {"kind": "util", "name": name, "args": list(args), **extra}


OK = {"kind": "util", "exit": 0, "stdout": "done"}


def test_every_act_joins_the_one_registry_at_its_moment():
    for name, (moment, _check_fn, describes) in ACTS.items():
        assert PREDICATES[name].moment == moment and PREDICATES[name].describes == describes


def test_a_send_as_the_user_is_the_reserved_verb_of_an_as_operator_channel(ctx):
    assert _check(ctx, "outbound-message", _util("gmail", "send", "--to", "a@b.c"))
    # reading the same mailbox is no send; a push to the operator's own device and a bot's
    # notice to him are outbound, but not his words going out to someone — no `as-operator`
    assert not _check(ctx, "outbound-message", _util("gmail", "read"))
    assert not _check(ctx, "outbound-message", _util("ntfy", "hello"))
    assert not _check(ctx, "outbound-message", _util("discord", "send", "done"))
    assert not _check(ctx, "outbound-message", {"kind": "write_file", "path": "a"})


def test_a_send_counts_as_sent_only_once_it_went_out(ctx):
    send = _util("gmail", "send")
    assert _check(ctx, "outbound-message-sent", send, OK)
    assert not _check(ctx, "outbound-message-sent", send, {"kind": "util", "exit": 1})
    held = {"kind": "assist_hold", "action": "util:gmail send", "lines": ["x"]}
    assert not _check(ctx, "outbound-message-sent", send, held)


def test_a_web_search_is_a_networked_util_tagged_web_and_search(ctx):
    assert _check(ctx, "web-lookup-returned", _util("websearch", "query"), OK)
    # local code search and a model call carry one of the tags, never both halves
    assert not _check(ctx, "web-lookup-returned", _util("code-search", "x"), OK)
    assert not _check(ctx, "web-lookup-returned", _util("llm-complete", "x"), OK)
    # a search that failed, or one sent to the background, has not come back
    assert not _check(ctx, "web-lookup-returned", _util("websearch", "q"), {"exit": 2})
    assert not _check(ctx, "web-lookup-returned", _util("websearch", "q", background=True), OK)


def test_feedback_read_is_a_util_tagged_feedback(ctx):
    assert _check(ctx, "feedback-read", _util("feedback-pending", "store.jsonl"), OK)
    assert not _check(ctx, "feedback-read", _util("websearch", "q"), OK)


def test_code_written_is_a_util_or_a_source_file_outside_scratch(ctx):
    ok_write = {"kind": "write_file", "bytes": 10}
    assert _check(ctx, "code-written", {"kind": "write_file", "path": "scripts/poll.py"},
                  ok_write)
    assert _check(ctx, "code-written", {"kind": "write_util", "name": "x"},
                  {"kind": "write_util", "selftest_ok": True})
    assert not _check(ctx, "code-written", {"kind": "write_util", "name": "x"},
                      {"kind": "write_util", "selftest_ok": False})
    assert not _check(ctx, "code-written", {"kind": "write_file", "path": "state/tmp.py"},
                      ok_write)
    assert not _check(ctx, "code-written", {"kind": "edit_file", "path": "README.md"},
                      {"kind": "edit_file"})


def test_util_created_is_a_new_tool_not_a_patch(ctx):
    action = {"kind": "write_util", "name": "x"}
    assert _check(ctx, "util-created", action, {"created": True, "selftest_ok": True})
    assert not _check(ctx, "util-created", action, {"created": False, "selftest_ok": True})


def _granted_repo(ctx, tmp_path):
    repo = tmp_path / "project"
    (repo / "docs").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    ctx.routine.fs_write_roots = [repo]
    return repo


def test_a_removal_counts_only_inside_a_work_tree_and_outside_scratch(ctx, tmp_path):
    repo = _granted_repo(ctx, tmp_path)
    gone = {"kind": "delete", "removed": True}
    assert _check(ctx, "tracked-path-removed",
                  {"kind": "delete", "path": str(repo / "docs" / "old.md")}, gone)
    assert _check(ctx, "tracked-path-removed",
                  {"kind": "move", "src": str(repo / "docs" / "a.md"),
                   "dst": str(repo / "docs" / "b.md")}, {"kind": "move"})
    # the routine's own scratch is nobody's reference, a failed delete removed nothing, and a
    # path no git work tree holds has no history to say who relied on it
    (ctx.routine.dir / ".git").mkdir(exist_ok=True)
    assert not _check(ctx, "tracked-path-removed",
                      {"kind": "delete", "path": "state/old.txt"}, gone)
    assert _check(ctx, "tracked-path-removed", {"kind": "delete", "path": "stages/old.md"}, gone)
    assert not _check(ctx, "tracked-path-removed",
                      {"kind": "delete", "path": str(repo / "docs" / "old.md")},
                      {"kind": "delete", "error": "no such path"})
    outside = tmp_path / "loose"
    outside.mkdir()
    ctx.routine.fs_write_roots = [repo, outside]
    assert not _check(ctx, "tracked-path-removed",
                      {"kind": "delete", "path": str(outside / "x.txt")}, gone)


SEED_PERMISSIONS = Path(__file__).resolve().parents[1] / "library-seed" / "permissions"


def test_exactly_the_channels_that_speak_as_the_operator_carry_the_tag_and_the_check():
    """The tag is what the hold keys on; the conduct line is what every send owes — the
    operator may have sent the message himself. They go together, on the same six docs."""
    from rsched.library_docs import parse_lenient

    tagged = set()
    for path in sorted(SEED_PERMISSIONS.glob("*.md")):
        meta, body = parse_lenient(path.read_text(encoding="utf-8"))
        if "as-operator" in (meta.get("tags") or []):
            tagged.add(path.stem)
            assert "Before EVERY " in body and "himself" in body, path.stem
    assert tagged == {"outbound-mail", "messaging-signal", "messaging-telegram",
                      "messaging-whatsapp", "messaging-zulip", "usenet"}
