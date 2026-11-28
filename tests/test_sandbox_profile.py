"""D170 phase 2 — WHICH util calls the micro-VM is selected for (rsched.sandboxprofile), and
the Stats-tab reading that makes the selected set and its measured cost visible. What was
authorized is a SELECTOR, not a replacement ("Landlock stays the default, a VM is used for
calls with an untrusted-input or model-authored-code profile"), so these guards are about the
boundary: what is selected, what is NOT, and that the profile is derived from what a util
already declares rather than from a flag someone has to remember to set.
"""

from helpers import stats_server, write_usage_stream
from rsched import sandboxprofile, utils_run
from rsched.readmodels.util_stats import util_stats


def _util(server, name, header):
    d = server.libraries_home / "utils" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "main.py").write_text(f'"""{name} — a util.\n\nusage: gu {name}\n{header}"""\n'
                               'print("ok")\n', encoding="utf-8")


def _profile(server, name):
    return sandboxprofile.profile_for_needs(
        utils_run.util_needs(server.libraries_home, name))


def test_net_without_a_credential_is_the_untrusted_input_profile(tmp_path):
    """The whole criterion: the call opens the network and declares no secret, so there is
    no counterparty the operator chose on the other end of it.
    """
    server = stats_server(tmp_path)
    _util(server, "scrape", "net: outbound\n")
    assert _profile(server, "scrape") == sandboxprofile.UNTRUSTED_INPUT


def test_a_credentialed_net_call_is_not_selected(tmp_path):
    """The exclusion that keeps the cost inside the ruling. 55% of the live call stream is
    net AND credentialed; selecting it would put +392 ms on ~19 calls a run. It is also the
    call a VM cannot serve without solving secret injection into the guest (phase 1, gap 2),
    and a credentialed util speaks to the operator's own account — a named counterparty.
    """
    server = stats_server(tmp_path)
    _util(server, "mailbox", "net: outbound\nsecrets: GMAIL_APP_PASSWORD\n")
    assert _profile(server, "mailbox") is None


def test_a_local_call_is_not_selected(tmp_path):
    """No network, no untrusted input: the bytes are the operator's own files and the
    Landlock jail already bounds which of them are visible.
    """
    server = stats_server(tmp_path)
    _util(server, "grepper", "net: none\nfs: roots\n")
    assert _profile(server, "grepper") is None
    _util(server, "undeclared", "tags: misc\n")
    assert _profile(server, "undeclared") is None


def test_the_profile_is_resolved_transitively_so_no_declaration_can_be_forgotten(tmp_path):
    """The plan's constraint was that the decision must be a property of the CALL, never a
    per-util flag someone forgets. It is: `util_needs` resolves `net` and `secrets` across
    the `calls:` graph, because the whole call tree runs in ONE jail and ONE env. A util
    declaring nothing itself but reaching a net sibling IS selected; one reaching a
    credentialed sibling is NOT, on its callee's credential.
    """
    server = stats_server(tmp_path)
    _util(server, "fetcher", "net: outbound\n")
    _util(server, "mailer", "net: outbound\nsecrets: GMAIL_APP_PASSWORD\n")
    _util(server, "quiet-caller", "calls: fetcher\n")
    _util(server, "credentialed-caller", "calls: mailer\n")
    assert _profile(server, "quiet-caller") == sandboxprofile.UNTRUSTED_INPUT
    assert _profile(server, "credentialed-caller") is None


def test_an_optional_secret_still_counts_as_a_credential(tmp_path):
    """`optional` is a subset of `secrets`, not a separate thing: a util that MAY be given a
    key can still be speaking to a chosen account on the call that has one. The criterion
    reads `secrets`, so it stays on the safe side of that ambiguity rather than selecting a
    call whose counterparty may well be named.
    """
    server = stats_server(tmp_path)
    _util(server, "maybe-keyed", "net: outbound\nsecrets: NANO_GPT_API_KEY?\n")
    needs = utils_run.util_needs(server.libraries_home, "maybe-keyed")
    assert needs.secrets, "an optional secret must still be a declared secret"
    assert _profile(server, "maybe-keyed") is None


def test_the_model_authored_profile_is_named_but_never_derived_from_a_header(tmp_path):
    """The second authorized profile is a CALL SITE — the write_util selftest, code the
    model wrote seconds earlier — and the same util source is ordinary library code a minute
    later. So no header may yield it: a util that did would be permanently VM-isolated for
    something true only of its first run.
    """
    server = stats_server(tmp_path)
    for header in ("net: outbound\n", "net: none\n", "calls: whatever\n", "tags: x\n"):
        _util(server, "authored", header)
        assert _profile(server, "authored") != sandboxprofile.MODEL_AUTHORED_CODE
    assert sandboxprofile.MODEL_AUTHORED_CODE == "model-authored-code"


def test_the_stats_reading_marks_the_selected_utils_and_leaves_the_rest_unmarked(tmp_path):
    """The read-only consumer: the Stats tab's per-util rows carry the profile beside each
    util's real call counts, which is what makes the selected set arguable before anything
    is built on it. Everything unselected reads None — composed exactly as it is today.
    """
    server = stats_server(tmp_path)
    _util(server, "scrape", "net: outbound\n")
    _util(server, "mailbox", "net: outbound\nsecrets: GMAIL_APP_PASSWORD\n")
    _util(server, "grepper", "net: none\n")
    rows = {r["name"]: r for r in util_stats(server)["utils"]}
    assert rows["scrape"]["isolation_profile"] == sandboxprofile.UNTRUSTED_INPUT
    assert rows["mailbox"]["isolation_profile"] is None
    assert rows["grepper"]["isolation_profile"] is None


def test_the_cost_of_the_selected_set_is_reported_from_its_own_call_counts(tmp_path):
    """The ruling bounds the selector by a RATE, so the reading reports the selected set's
    real calls together with phase 1's measured per-call figures. A share of all calls would
    not answer it: what a run waits for is its own calls.
    """
    server = stats_server(tmp_path)
    _util(server, "scrape", "net: outbound\n")
    _util(server, "mailbox", "net: outbound\nsecrets: GMAIL_APP_PASSWORD\n")
    write_usage_stream(server.routines_home, [
        {"run_id": "r:1", "ts": "2026-07-01T07:00:00+00:00",
         "utils": {"scrape": {"ok": 3}, "mailbox": {"ok": 40}}},
    ])
    out = util_stats(server)
    iso = out["isolation"]
    assert iso["profile"] == sandboxprofile.UNTRUSTED_INPUT
    assert iso["utils"] == 1, "only the uncredentialed net util is selected"
    assert iso["calls"] == 3, "the credentialed util's 40 calls are not in the budget"
    assert iso["added_ms_per_call"] == 392
    assert iso["held_mb_per_call"] == 179


def test_a_util_the_library_no_longer_holds_gets_no_profile(tmp_path):
    """A counted name with no header left cannot be derived from and has no call left to
    select — and asking `util_needs` for it would be a read of a util that is not there.
    """
    server = stats_server(tmp_path)
    write_usage_stream(server.routines_home, [
        {"run_id": "r:1", "ts": "2026-07-01T07:00:00+00:00",
         "utils": {"vanished": {"ok": 2}}},
    ])
    rows = {r["name"]: r for r in util_stats(server)["utils"]}
    assert rows["vanished"]["in_library"] is False
    assert rows["vanished"]["isolation_profile"] is None
    assert util_stats(server)["isolation"]["utils"] == 0
