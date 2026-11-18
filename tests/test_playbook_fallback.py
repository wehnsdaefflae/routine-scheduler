"""F907 — saving a conversation as a playbook tries the configured fallback chain.

MEASURED FAILURE. The operator pressed save-as-playbook on `c-20261014-134208` and got an
HTTP 502. Behind it: an upstream credential-pool **HTTP 429**, after which
`playbook_distill._infer` let the exhausted `EndpointError` escape. It had resolved its model
with `for_system()`, which does resolve the WHOLE chain — but `failover.pick` is resolve-time
avoidance, and a failure that happens during the CALL had nowhere to go. So the `fallbacks:`
the catalog already declared went untried, and the operator's request died on a transient.

The engine's own turn loop has had call-time failover since `engine/degrade.py`; this button
simply never ran through it. The repair uses that same primitive (`failover.next_after`) rather
than a second policy, and these tests pin the four discriminating cases — one where the chain
must be advanced and three where it must not be:

1. a retryable failure on the head → the next rung is tried and its answer is returned;
2. an auth failure → no fallback (the same key fails on every rung, so trying the chain would
   turn one real error into several);
3. a malformed or empty reply → not a transport failure at all; it raises as before, and the
   chain is not walked (a model that answered badly must not be retried as though it had not
   answered);
4. the chain exhausted → the error propagates, naming what was tried, and nothing is written.

The real `complete` is never called: each rung is a recording stub, so the test is about the
control flow this fix adds and not about any provider.
"""

from __future__ import annotations

import json

import pytest

from rsched import playbook_distill
from rsched.endpoints.base import EndpointError

#: A reply in the shape `_normalize` consumes — the body key is `main`, not `main_body`
#: (`main_body` is what `_normalize` RETURNS, and using it here made the fixture look fine
#: while the refuse-to-degrade guard rejected it as an empty body).
GOOD = {"slug": "a-playbook", "title": "A playbook", "when": "when asked",
        "axis": "the axis", "tags": ["one"], "main": "## Instructions\n1. Do the thing.",
        "details": []}


class _Ref:
    """A chain member's attributes, as `resolve_chain` resolves them — each rung its own."""

    def __init__(self, name, effort="high", temperature=0.2, max_tokens=4096):
        self.name, self.model = name, f"provider/{name}"
        self.effort, self.temperature, self.max_tokens = effort, temperature, max_tokens
        self.endpoint = "stub"


class _Comp:
    def __init__(self, parsed=None, text=""):
        self.parsed, self.text = parsed, text


class _Endpoint:
    """One rung. `outcome` is what its `complete` does: an exception to raise, or a reply."""

    def __init__(self, ref, outcome):
        self.ref, self.outcome = ref, outcome
        self.calls: list[dict] = []

    def complete(self, messages, **kw):
        self.calls.append(kw)
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


class _Server:
    system_model = "Primary"
    system_effort = "high"


def _wire(monkeypatch, rungs):
    """Install a chain of `(name, outcome)` rungs and return the endpoint stubs, in order."""
    chain = []
    for name, outcome in rungs:
        ref = _Ref(name)
        chain.append((_Endpoint(ref, outcome), ref))

    class _Registry:
        def __init__(self, server):
            self.server = server

        def for_system(self):
            return chain[0]

        def resolve_chain(self, name, effort=None):
            return list(chain)

    import rsched.endpoints as endpoints_pkg
    monkeypatch.setattr(endpoints_pkg, "EndpointRegistry", _Registry)

    def _next_after(ch, failed, **kw):
        idx = next((i for i, (_ep, ref) in enumerate(ch) if ref.name == failed.name), None)
        if idx is None or idx + 1 >= len(ch):
            return None
        return ch[idx + 1]

    from rsched.endpoints import failover
    monkeypatch.setattr(failover, "next_after", _next_after)
    return [ep for ep, _ref in chain]


def test_a_retryable_failure_advances_to_the_configured_backup(monkeypatch):
    """The operator's own case: a 429 on the head must reach the backup, not an HTTP 502."""
    rungs = _wire(monkeypatch, [
        ("Primary", EndpointError("429 from the credential pool", retryable=True)),
        ("Backup", _Comp(parsed=GOOD)),
    ])

    out = playbook_distill._infer(_Server(), "prompt", purpose="p", kind="save-playbook")

    assert out["slug"] == "a-playbook"
    assert len(rungs[0].calls) == 1, "the head was not even tried"
    assert len(rungs[1].calls) == 1, "the configured backup was never tried (F907)"
    # each rung keeps its own parameters, and the schema and timeout are untouched
    assert rungs[1].calls[0]["effort"] == "high"
    assert rungs[1].calls[0]["timeout"] == 180
    assert rungs[1].calls[0]["schema"] is playbook_distill.PLAYBOOK_SCHEMA
    assert rungs[1].calls[0]["max_tokens"] == 4096


def test_an_auth_failure_does_not_walk_the_chain(monkeypatch):
    """The same credential fails on every rung, so a fallback would multiply one real error."""
    rungs = _wire(monkeypatch, [
        ("Primary", EndpointError("401 bad key", auth=True)),
        ("Backup", _Comp(parsed=GOOD)),
    ])

    with pytest.raises(EndpointError):
        playbook_distill._infer(_Server(), "prompt", purpose="p", kind="save-playbook")

    assert rungs[1].calls == [], "an auth failure was failed over — the key is the same there"


def test_a_malformed_reply_is_not_a_transport_failure(monkeypatch):
    """A model that answered badly must not be retried as though it had not answered."""
    rungs = _wire(monkeypatch, [
        ("Primary", _Comp(parsed=None, text="not json at all")),
        ("Backup", _Comp(parsed=GOOD)),
    ])

    with pytest.raises((json.JSONDecodeError, ValueError)):
        playbook_distill._infer(_Server(), "prompt", purpose="p", kind="save-playbook")

    assert rungs[1].calls == [], "a malformed reply walked the chain"


def test_an_exhausted_chain_propagates_and_names_what_it_tried(monkeypatch):
    """Nothing is written on failure — the route surfaces the error, as it always did."""
    _wire(monkeypatch, [
        ("Primary", EndpointError("429 again", retryable=True)),
        ("Backup", EndpointError("503 too", retryable=True)),
    ])

    with pytest.raises(EndpointError) as caught:
        playbook_distill._infer(_Server(), "prompt", purpose="p", kind="save-playbook")

    message = str(caught.value)
    assert "exhausted" in message
    assert "Primary" in message and "Backup" in message, message

def test_a_registry_without_a_chain_propagates_the_original_failure(monkeypatch):
    """The chain is resolved lazily, so a registry that cannot produce one (a test double, a
    server with no catalog) must surface the transport error itself — never a second error
    raised while looking for a fallback."""
    class _Registry:
        def __init__(self, server):
            pass

        def for_system(self):
            ref = _Ref("Only")
            return _Endpoint(ref, EndpointError("429 and nothing else", retryable=True)), ref

        def resolve_chain(self, name, effort=None):
            raise TypeError("resolve() takes 2 positional arguments but 3 were given")

    import rsched.endpoints as endpoints_pkg
    monkeypatch.setattr(endpoints_pkg, "EndpointRegistry", _Registry)

    with pytest.raises(EndpointError) as caught:
        playbook_distill._infer(_Server(), "prompt", purpose="p", kind="save-playbook")

    assert "429 and nothing else" in str(caught.value)
    assert "exhausted" not in str(caught.value)
