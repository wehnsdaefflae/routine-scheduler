"""WHICH util calls deserve more isolation than the Landlock jail — the profile, derived.

D170 phase 2. The operator's ruling: *"Landlock stays the default, a VM is used
for calls with an untrusted-input or model-authored-code profile. The measured cost is
affordable for a few calls per run and is not for all of them."* Phase 1 measured what "a few"
has to mean: a micro-VM boot costs **+392 ms** over the jail's 61.8 ms (453.9 ms vs 61.8 ms
median) and holds **179 MB** per concurrent call, so the selected set has to be small or the
cost lands on every run.

This module is the SELECTION, and nothing else: no VM, no launch, no change to how an
unselected call is composed. It exists ahead of the isolation path deliberately — the set it
picks is measurable from the live call stream today (readmodels/util_stats renders it per util
beside that util's real call counts), so the cost of the escalation is arguable before anything
is built on it.

THE CRITERION, and why it is not "the call touches the network". Measured over the live
library and 4,107 `util` calls in the 120 most recently written transcripts:

- `net: outbound` alone is declared by 42 of 56 utils and carries ~60% of all calls — at
  +392 ms that is ~+7.8 s on a median run, which is the "not for all of them" the ruling
  excludes;
- **net AND no declared credential** is 8 utils and 204 calls — **1.7 calls per run**, ~0.7 s
  of added latency per run, 179 MB held one call at a time. That is "a few calls per run"
  literally.

The narrow bucket is also the bucket a VM can actually SERVE: a call declaring no secret needs
no secret injected into the guest (phase 1's gap 2 is void by construction), and an anonymous
fetch-and-parse acts on bytes it just downloaded rather than on the run's granted roots. The
expensive-to-serve calls and the trusted-counterparty calls are the same calls — a credentialed
util speaks to the operator's own account over TLS, which is a named counterparty, not
untrusted input.

A PROPERTY OF THE CALL, NEVER A FLAG. Nothing here is declared twice and nothing can be
forgotten: `utils_run.util_needs` already resolves `net` and `secrets` TRANSITIVELY across the
`calls:` graph, because the whole call tree runs in one jail and one env. So a util that
declares nothing itself but reaches `web-request` is already resolved as net-opening, and one
that reaches a credentialed sibling is already resolved as credentialed. A new util gets its
profile the moment it declares what it does — which the library already requires of it.

THE OTHER PROFILE IS A CALL SITE, NOT A UTIL. `model-authored-code` is the write_util
selftest: code the model wrote seconds earlier, run for the first time, with no run roots
mounted. It is not derivable from any util's header — the same util source is ordinary library
code one minute later — so it belongs with the dispatch that knows it is a selftest
(`engine/authoring.py`, `web/api_library_utils.py`, both already distinguished by passing
`sandbox.base_policy`). It is named here so the vocabulary is in one place, and asserted
nowhere here.

NOT SELECTED, each with its reason, because an unstated exclusion is one nobody can argue
with: a credentialed net call (named counterparty, and the call the VM cannot serve without
solving secret injection); a local call with no network (31% of the stream — the bytes are the
operator's own files and the jail already bounds which are visible); a `script` action (a
routine's own committed helper is not model-authored *in the call*); `shell` (handed no store
secret, runs the operator's own box — wrapping it would select the escape hatch and nothing
else).
"""

from __future__ import annotations

#: The profile of a call whose INPUT comes from an unnamed counterparty: it opens the network
#: and carries no credential, so there is nobody on the other end the operator chose.
UNTRUSTED_INPUT = "untrusted-input"

#: The profile of a call RUNNING CODE THE MODEL JUST WROTE (the write_util selftest). Not
#: derivable from a util header — see the module docstring; the dispatch that knows a call is
#: a selftest owns it.
MODEL_AUTHORED_CODE = "model-authored-code"


def profile_for_needs(needs) -> str | None:
    """The isolation profile of a call to a util declaring `needs`, or None for the default.

    `needs` is a `utils_run.UtilNeeds` — already resolved transitively over `calls:`. None
    means "the Landlock jail, exactly as today": that is the answer for 95% of the live call
    stream and the only answer this module gives about an unselected call.
    """
    if getattr(needs, "net", False) and not getattr(needs, "secrets", None):
        return UNTRUSTED_INPUT
    return None
