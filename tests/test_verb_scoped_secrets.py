"""VERB-SCOPED secrets (`secrets: NAME@verb`, R2622) — the acceptance the operator's
decision named: a verb-dispatched util's credential surface stops being the union of all its
verbs' needs.

The demonstration R2621 asked for, in the shape it asked for it: `gu remote credentials` can
see `DEEPL_API` while `gu remote exec` cannot. Both of the catalog shapes it named as traps for
a naive implementation are here too — a util whose default call leads with a URL
(`web-request`) and one whose default call leads with a regex (`code-search`) — because a verb
the engine guesses wrongly turns a sandbox decision into a missing-credential mystery, after
which the reasonable move is provisioning a credential that already exists.
"""

from rsched import utils_lib, utils_run, utils_verbs
from rsched.utils_header import header_problems, parse_header, parse_secret_entries, widening

REMOTEISH = (
    "# /// script\n# dependencies = []\n# ///\n"
    '"""remoteish — fixture of a verb-dispatched util holding one verb\'s credential.\n\n'
    "usage:\n"
    "  gu remoteish list [--json]\n"
    "  gu remoteish exec MACHINE --command CMD [--cwd DIR]\n"
    "  gu remoteish credentials MACHINE --service claude|deepl\n"
    "calls: (none)\n"
    "secrets: MACHINES_JSON, DEEPL_API@credentials\n"
    "tags: t\nnet: none\nfs: none\n"
    '"""\nimport os\nos.environ.get("DEEPL_API")\nos.environ.get("MACHINES_JSON")\n'
)
STORE = {"MACHINES_JSON": "m-1", "DEEPL_API": "d-1"}


def _library(tmp_path, monkeypatch, src=REMOTEISH, name="remoteish"):
    utils_lib.ensure_library(tmp_path)
    utils_lib.write_util_file(tmp_path, name, src)
    monkeypatch.setattr("rsched.secrets.load_secrets", lambda: dict(STORE))
    return tmp_path


def test_the_entry_grammar_keeps_the_bare_name_and_records_the_scope():
    """Both markers are part of the DECLARATION, never of the variable name: `secrets` carries
    bare names in every case, so injection and the undeclared-read gate are untouched. A name
    repeated with two verbs serves both; `NAME?@verb` and `NAME@verb?` mean the same thing."""
    names, optional, verbs = parse_secret_entries(
        ["A_KEY", "B_KEY?", "C_KEY@one", "D_KEY@two?", "E_KEY?@two", "F_KEY@a", "F_KEY@b"])
    assert names == ["A_KEY", "B_KEY", "C_KEY", "D_KEY", "E_KEY", "F_KEY"]
    assert optional == ["B_KEY", "D_KEY", "E_KEY"]
    assert verbs == {"C_KEY": {"one"}, "D_KEY": {"two"}, "E_KEY": {"two"},
                     "F_KEY": {"a", "b"}}
    # the bare names still reach the declared-secrets set the sandbox injects from
    assert parse_header(REMOTEISH)["secrets"] == ["MACHINES_JSON", "DEEPL_API"]
    assert parse_header(REMOTEISH)["verb_secrets"] == {"DEEPL_API": {"credentials"}}


def test_the_verb_vocabulary_is_the_utils_own_usage_block():
    """The engine never reimplements a dispatcher: the vocabulary is the invocation table the
    author wrote, so a verb nobody can call on purpose cannot be bound either."""
    assert utils_verbs.declared_verbs(REMOTEISH) == ("list", "exec", "credentials")
    # a util with no verbs declares no vocabulary — every bound name is then withheld always
    plain = REMOTEISH.replace("usage:\n  gu remoteish list [--json]\n"
                              "  gu remoteish exec MACHINE --command CMD [--cwd DIR]\n"
                              "  gu remoteish credentials MACHINE --service claude|deepl\n",
                              "usage: gu remoteish [--json]\n")
    assert utils_verbs.declared_verbs(plain) == ()


def test_argv_names_a_verb_only_at_its_first_token():
    """R2621's two traps, and the one that would widen the scope instead of narrowing it: a
    flag VALUE spelling a verb must never be read as that verb."""
    assert utils_verbs.running_verb(REMOTEISH, ["credentials", "m", "--service", "deepl"]) \
        == "credentials"
    assert utils_verbs.running_verb(REMOTEISH, ["exec", "m", "--cwd", "credentials"]) == "exec"
    assert utils_verbs.running_verb(REMOTEISH, ["--selftest"]) is None
    assert utils_verbs.running_verb(REMOTEISH, []) is None
    # web-request's default call is a URL; code-search's is a regex. Neither names a verb.
    assert utils_verbs.running_verb(REMOTEISH, ["https://example.org/x"]) is None
    assert utils_verbs.running_verb(REMOTEISH, ["--amber|--midnight"]) is None


def test_the_credentials_verb_sees_the_secret_and_exec_does_not(tmp_path, monkeypatch):
    """THE ACCEPTANCE TEST (R2621): the two calls that are the demonstration. The unsuffixed
    secret is injected for every verb, as before — the change is additive."""
    home = _library(tmp_path, monkeypatch)
    cred = utils_run._child_env(home, "remoteish", args=["credentials", "m", "--service", "deepl"])
    assert cred["DEEPL_API"] == "d-1" and cred["MACHINES_JSON"] == "m-1"
    execd = utils_run._child_env(home, "remoteish", args=["exec", "m", "--command", "ls"])
    assert "DEEPL_API" not in execd, "exec must not see the credentials verb's secret"
    assert execd["MACHINES_JSON"] == "m-1", "an unsuffixed secret is unchanged"
    # FAIL CLOSED: a call that names no verb withholds every bound name
    for argv in ([], ["--selftest"], ["https://example.org"]):
        assert "DEEPL_API" not in utils_run._child_env(home, "remoteish", args=argv), argv
    # and the pre-R2622 call shape (no args at all) still works — purely additive
    assert "MACHINES_JSON" in utils_run._child_env(home, "remoteish")


def test_an_unscoped_library_is_untouched(tmp_path, monkeypatch):
    """Every util in the library the day this shipped declares no scope: such a call tree
    must take the same path it always did, verb or no verb."""
    plain = REMOTEISH.replace("secrets: MACHINES_JSON, DEEPL_API@credentials",
                              "secrets: MACHINES_JSON, DEEPL_API")
    home = _library(tmp_path, monkeypatch, plain)
    assert utils_verbs.verb_scope(home, "remoteish") == {}
    assert utils_verbs.withheld_for_verb(home, "remoteish", ["exec"]) == ([], None)
    env = utils_run._child_env(home, "remoteish", args=["exec", "m"])
    assert env["DEEPL_API"] == "d-1" and env["MACHINES_JSON"] == "m-1"


def test_a_calls_edge_cannot_walk_around_the_scope(tmp_path, monkeypatch):
    """A `calls:` edge hands a callee's credentials to its caller (util_needs), so a
    verb-bound name reached that way obeys the ROOT call's verb — otherwise `calls:` would be
    a second door around the scope."""
    home = _library(tmp_path, monkeypatch)
    caller = (
        "# /// script\n# dependencies = []\n# ///\n"
        '"""callerish — fixture reaching remoteish.\n\n'
        "usage: gu callerish [--json]\n"
        "calls: remoteish\ntags: t\nnet: none\nfs: none\n"
        '"""\nprint("ok")\n'
    )
    utils_lib.write_util_file(home, "callerish", caller)
    assert utils_verbs.verb_scope(home, "callerish") == {"DEEPL_API": {"credentials"}}
    # callerish declares NO verbs of its own, so no argv of its can name `credentials`
    assert "DEEPL_API" not in utils_run._child_env(home, "callerish", args=["credentials"])
    assert utils_run._child_env(home, "callerish")["MACHINES_JSON"] == "m-1"


def test_binding_to_a_verb_the_usage_block_never_lists_is_a_header_problem():
    """Caught when the util is SAVED, for one edit — not as an empty env at call time, which
    reads exactly like a credential nobody provisioned (R2621)."""
    assert header_problems(REMOTEISH) == [], header_problems(REMOTEISH)
    typo = REMOTEISH.replace("DEEPL_API@credentials", "DEEPL_API@credential")
    problems = header_problems(typo)
    assert any("DEEPL_API@credential" in p and "usage" in p for p in problems), problems
    assert any("Known verbs: credentials, exec, list" in p for p in problems), problems


def test_unbinding_a_scope_reads_as_a_widening():
    """Dropping `@verb` hands the secret to every verb's callers — the widening R2620 caught
    by hand, when three service credentials would have reached ~3,900 `gu remote exec` calls
    to serve one verb. The approval dial must see it."""
    unscoped = REMOTEISH.replace("DEEPL_API@credentials", "DEEPL_API")
    assert any("DEEPL_API on every verb" in w for w in widening(REMOTEISH, unscoped))
    # widening to one MORE verb is reported as that verb; narrowing is not a widening
    wider = REMOTEISH.replace("DEEPL_API@credentials", "DEEPL_API@credentials, DEEPL_API@exec")
    assert any("DEEPL_API on the verb exec" in w for w in widening(REMOTEISH, wider))
    assert widening(unscoped, REMOTEISH) == []
    assert widening(REMOTEISH, REMOTEISH) == []


def test_the_withholding_is_said_out_loud_in_the_observation():
    """Never a silent withholding: from inside the util a withheld verb-bound secret arrives
    exactly as an unprovisioned one, so the observation names it and the verb that ran."""
    from rsched.engine.observations import format_observation

    text = format_observation({"kind": "util", "name": "remoteish", "args": ["exec", "m"],
                               "exit": 0, "stdout": "ok", "stderr": "",
                               "withheld_verb_scoped": {"verb": "exec",
                                                        "secrets": ["DEEPL_API"]}})
    assert "verb-scoped secret(s) not injected: DEEPL_API" in text
    assert "the verb 'exec'" in text
    default = format_observation({"kind": "util", "name": "remoteish", "args": [],
                                  "exit": 0, "stdout": "ok", "stderr": "",
                                  "withheld_verb_scoped": {"verb": None,
                                                           "secrets": ["DEEPL_API"]}})
    assert "no verb (the default path)" in default
