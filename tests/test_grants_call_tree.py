"""The reserved-util gate over a call's `calls:` TREE, not just its name.

`capabilities.utils` is advertised as the switch for a reserved channel — the messengers, the
darknet, the shared browser, the remote machines. It gated the util NAME the model put in the
action and nothing else, while `utils_run.util_needs` unions every `calls:` callee's
`secrets:`, `net:` and `fs:` into the caller's one jail and one env, and the library root is on
PATH for every util. So an UNGATED util naming a reserved sibling is a second, unaudited door
into that channel: it receives the sibling's credentials and can exec it.

Live in the library: `rephrase-as-human` and `voice-rewrite` name `remote`, exec `gu remote`,
and inherit its `RSCHED_MACHINE_KEYS` — the bound machines' private SSH keys — while holding
none of the `remote-machines` permission that reserves it.

The edge is refused for what it CONFERS, not for existing: the callee carries credentials, or
the caller really execs it. `captcha-fetch` names `browser-session` only to print "use `gu
browser-session start --stealth`" in an error message, and `browser-session` declares no
secrets — closing an ungated page fetcher to 24 routines over an unused line would be the gate
costing more than it protects. The denial names the EDGE, because a refusal reading "remote is
off" for a call the run made to `rephrase-as-human` is unactionable.
"""

from __future__ import annotations

from rsched import utils_lib
from rsched.grantpolicy import GrantPolicy

RESERVED = '''"""vault — the reserved channel.

usage: gu vault
net: outbound
fs: none
secrets: VAULT_KEY
calls: (none)
"""
'''

CALLER = '''"""helper — ungated, and it names the reserved one.

usage: gu helper
net: none
fs: none
calls: vault
"""
'''

# names the reserved util and EXECS it, but that util carries no credential of its own
EXECER = '''"""execer — ungated; runs the reserved one.

usage: gu execer
net: none
fs: none
calls: plainvault
"""
import subprocess
subprocess.run(["gu", "plainvault"], check=False)
'''

# names the reserved util and never runs it — the live `captcha-fetch` shape: the name appears
# only in an error message telling a human what to do
MENTIONER = '''"""mentioner — ungated; only ADVISES using the reserved one.

usage: gu mentioner
net: none
fs: none
calls: plainvault
"""
print("blocked — use `gu plainvault start` yourself")
'''

PLAINVAULT = '''"""plainvault — reserved, but declares no secret of its own.

usage: gu plainvault
net: outbound
fs: roots
calls: (none)
"""
'''

PLAIN = '''"""plain — ungated and self-contained.

usage: gu plain
net: none
fs: none
calls: (none)
"""
'''


def _library(tmp_path):
    home = tmp_path / "library"
    (home / "utils").mkdir(parents=True)
    utils_lib.write_util_file(home, "vault", RESERVED)
    utils_lib.write_util_file(home, "helper", CALLER)
    utils_lib.write_util_file(home, "plain", PLAIN)
    utils_lib.write_util_file(home, "plainvault", PLAINVAULT)
    utils_lib.write_util_file(home, "execer", EXECER)
    utils_lib.write_util_file(home, "mentioner", MENTIONER)
    return home


def _policy(home, **kw) -> GrantPolicy:
    return GrantPolicy(libraries_home=home,
                       gated_utils={"vault": ("vault-access",),
                                    "plainvault": ("vault-access",)}, **kw)


def test_the_call_tree_is_part_of_what_a_util_declares(tmp_path):
    from rsched.utils_run import util_needs

    home = _library(tmp_path)
    assert util_needs(home, "helper").tree == ("helper", "vault")
    assert util_needs(home, "plain").tree == ("plain",)
    # …and it is why the caller already inherits the callee's credentials
    assert "VAULT_KEY" in util_needs(home, "helper").secrets


def test_an_ungated_util_reaching_a_reserved_one_is_refused_by_the_edge(tmp_path):
    home = _library(tmp_path)
    denial = _policy(home).deny({"kind": "util", "name": "helper"})
    assert denial is not None
    assert "'helper' declares `calls: vault`" in denial
    assert "its credentials" in denial
    assert "vault-access" in denial              # the covering permission is named
    assert "util:vault" in denial                # …and the one-click request route


def test_holding_the_reserved_util_lets_the_caller_through(tmp_path):
    home = _library(tmp_path)
    policy = _policy(home, utils=frozenset({"vault"}))
    assert policy.deny({"kind": "util", "name": "helper"}) is None
    assert policy.deny({"kind": "util", "name": "vault"}) is None


def test_an_unrelated_util_is_unaffected(tmp_path):
    home = _library(tmp_path)
    assert _policy(home).deny({"kind": "util", "name": "plain"}) is None


def test_an_edge_that_is_actually_exec_d_is_refused_even_without_credentials(tmp_path):
    denial = _policy(_library(tmp_path)).deny({"kind": "util", "name": "execer"})
    assert denial is not None and "a way to run it" in denial


def test_an_edge_that_only_mentions_the_reserved_util_is_not_a_bypass(tmp_path):
    """The live `captcha-fetch` shape: `calls: browser-session` exists so an error message can
    advise a human, `browser-session` declares no secret, and nothing execs it. Refusing that
    would close an ungated page fetcher to 24 routines and protect nothing."""
    assert _policy(_library(tmp_path)).deny({"kind": "util", "name": "mentioner"}) is None


def test_the_direct_name_gate_still_answers_first(tmp_path):
    """Calling the reserved util itself keeps its own wording — the edge message would be
    nonsense for a call that names no edge."""
    denial = _policy(_library(tmp_path)).deny({"kind": "util", "name": "vault"})
    assert "is a reserved capability switched OFF" in denial
    assert "calls:" not in denial


def test_an_admin_leg_and_a_libraryless_policy_both_skip_the_walk(tmp_path):
    home = _library(tmp_path)
    assert _policy(home, admin=True).deny({"kind": "util", "name": "helper"}) is None
    bare = GrantPolicy(gated_utils={"vault": ("vault-access",)})
    assert bare.deny({"kind": "util", "name": "helper"}) is None
