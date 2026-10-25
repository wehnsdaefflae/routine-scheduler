"""PUT /api/settings/endpoints/{name} — the three states of the `api_key` field, and the
refusal that stops a save leaving an endpoint with no credential at all (D208).

The distinction IS the item. The Settings editor submits the key field blank on every
unrelated edit, so a blank (or absent) `api_key` must never wipe a stored inline key — while
an operator who wants the inline key GONE, so the Secrets store takes over, needs a way to say
so through the API. An explicit JSON `null` is that way, and it is awkward precisely because
the obvious serialisations throw it away: `model_dump()` puts it in the same bucket as the
empty string, and `model_dump(exclude_none=True)` — the same trap one layer up, which made a
routine-settings patch proposing `null` impossible to fire — strips it before any writer sees
it. The decision is therefore taken from `model_fields_set` plus the attribute itself, and
`test_the_body_tells_an_absent_key_from_an_explicit_null` pins that mechanism, so a refactor
back to a plain `str = ""` default fails here instead of in production.

The refusal is bounded by a shape that already lives on this instance: an endpoint carrying an
inline key whose `key_var` is NOT in the central store (the `anthropic metered` endpoint, and
every routine's main loop rides it). A refusal keyed on "key_var names a secret that is not in
the store" would make that endpoint unsavable, so this one is keyed on the RESULT — after the
save, does any rung of the credential ladder resolve? — and only when this save is what
changed where the credential comes from. That exemption is what keeps keyless local backends
(Ollama, vLLM) and endpoints that already resolve nothing savable.
"""

from __future__ import annotations

import yaml

from rsched.web.settings.endpoints import EndpointBody

#: Obvious non-credentials. No real key value appears in this suite.
INLINE = "sk-d208-inline-not-a-real-key"
STORED = "sk-d208-stored-not-a-real-key"


def _stored(tmp_path, name: str = "dummy") -> dict:
    raw = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))
    return raw["endpoints"][name]


def _view(c, name: str = "dummy") -> dict:
    return next(e for e in c.get("/api/settings/endpoints").json()["endpoints"]
                if e["name"] == name)


def _put(c, body: dict, name: str = "dummy"):
    return c.put(f"/api/settings/endpoints/{name}", json=body)


def _body(**over) -> dict:
    """The editor's own save shape: identity, kind, base_url — plus whatever the case sets."""
    return {"name": "dummy", "kind": "openai", "base_url": "http://x/v1", **over}


def _seed_inline_key(c) -> None:
    assert _put(c, _body(api_key=INLINE)).status_code == 200


def _seed_store(c, key: str = "OPENAI_API_KEY") -> None:
    """A value under the endpoint's key_var, so the Secrets rung resolves once the inline key
    is gone — the `nano gpt` shape the clear exists for."""
    assert c.put("/api/settings/secrets", json={"key": key, "value": STORED}).status_code == 200


# ---- the three states of the key field ---------------------------------------------------

def test_an_absent_api_key_keeps_the_stored_inline_key(api_client):
    """Today's behaviour, and it stays: the editor's "edit fields" PUT sends no `api_key`."""
    c, tmp = api_client
    _seed_inline_key(c)
    assert _put(c, _body(base_url="http://y/v1")).status_code == 200
    assert _stored(tmp)["api_key"] == INLINE
    assert _view(c)["has_inline_key"] is True


def test_a_blank_api_key_keeps_the_stored_inline_key(api_client):
    """The non-destructive case the whole guard exists for: a blank submit must never wipe a
    key. The credential-save form posts the field, and an empty one means "I did not retype
    it", never "delete it"."""
    c, tmp = api_client
    _seed_inline_key(c)
    assert _put(c, _body(base_url="http://y/v1", api_key="")).status_code == 200
    assert _stored(tmp)["api_key"] == INLINE
    assert _view(c)["has_inline_key"] is True


def test_an_explicit_null_clears_the_stored_inline_key(api_client):
    """The item. `api_key: null`, present in the payload, removes the inline key — and the
    live ServerConfig is re-mirrored, so the card stops claiming one without a restart."""
    c, tmp = api_client
    _seed_store(c)
    _seed_inline_key(c)
    assert _view(c)["key_source"]["shadowed_secret"] is True      # inline hides the secret
    r = _put(c, _body(api_key=None))
    assert r.status_code == 200, r.text
    assert "api_key" not in _stored(tmp)
    view = _view(c)
    assert view["has_inline_key"] is False
    assert view["key_source"] == {"source": "secret", "var": "OPENAI_API_KEY"}


def test_the_null_survives_a_save_that_also_changes_another_field(api_client):
    """The null is not a special-cased lone payload: it clears while an ordinary edit rides
    along, which is how the editor would ever send it."""
    c, tmp = api_client
    _seed_store(c)
    _seed_inline_key(c)
    assert _put(c, _body(base_url="http://z/v1", api_key=None)).status_code == 200
    stored = _stored(tmp)
    assert "api_key" not in stored
    assert stored["base_url"] == "http://z/v1"


def test_the_body_tells_an_absent_key_from_an_explicit_null():
    """The MECHANISM, pinned. `model_dump()` cannot tell these three apart — every one of them
    is dropped or kept by value alone — so the handler must read `model_fields_set`. A
    refactor back to `api_key: str = ""`, or a serialisation through
    `model_dump(exclude_none=True)`, fails here.
    """
    absent = EndpointBody(name="e", kind="openai")
    blank = EndpointBody(name="e", kind="openai", api_key="")
    null = EndpointBody(name="e", kind="openai", api_key=None)
    assert "api_key" not in absent.model_fields_set
    assert "api_key" in blank.model_fields_set and blank.api_key == ""
    assert "api_key" in null.model_fields_set and null.api_key is None
    # what the value-filtering serialisations do with all three — identical, hence the above
    assert null.model_dump(exclude_none=True).get("api_key", "<gone>") == "<gone>"
    assert absent.model_dump()["api_key"] == blank.model_dump()["api_key"] == ""


# ---- the refusal, and the shape it must not strand ---------------------------------------

def test_clearing_the_only_credential_is_refused(api_client):
    """The dangerous save: an inline key is the ONLY thing resolving, its key_var is in nobody's
    store, and the null would leave the endpoint dead. Refused, and config.yaml is untouched —
    this is the live `anthropic metered` shape, whose key clearing would take the main loops of
    every routine riding it down.
    """
    c, tmp = api_client
    _seed_inline_key(c)                      # no OPENAI_API_KEY in the store this time
    before = (tmp / "config.yaml").read_text(encoding="utf-8")
    r = _put(c, _body(api_key=None))
    assert r.status_code == 400, r.text
    assert "no credential" in r.text
    assert "OPENAI_API_KEY" in r.text                     # names what to provision
    assert INLINE not in r.text                           # never the value
    assert (tmp / "config.yaml").read_text(encoding="utf-8") == before
    assert _stored(tmp)["api_key"] == INLINE


def test_an_endpoint_with_an_inline_key_and_no_stored_secret_still_saves(api_client):
    """THE caution. `anthropic metered` carries an inline key while ANTHROPIC_API_KEY is not in
    the central store. A refusal keyed on the key_var alone would make it unsavable; this one
    reads the resulting state, where the inline key resolves, so an ordinary edit goes through
    and the key is preserved.
    """
    c, tmp = api_client
    assert _put(c, {"name": "metered", "kind": "anthropic", "base_url": "http://a/v1",
                    "api_key": INLINE}, "metered").status_code == 200
    assert _view(c, "metered")["key_source"] == {"source": "inline", "var": "ANTHROPIC_API_KEY",
                                                 "shadowed_secret": False}
    r = _put(c, {"name": "metered", "kind": "anthropic", "base_url": "http://b/v1",
                 "key_var": "ANTHROPIC_API_KEY"}, "metered")
    assert r.status_code == 200, r.text
    assert _stored(tmp, "metered")["api_key"] == INLINE
    assert _stored(tmp, "metered")["base_url"] == "http://b/v1"


def test_pointing_an_endpoint_at_a_secret_nobody_holds_is_refused(api_client):
    """The operator's second ask: an endpoint cannot be SAVED pointing at a credential nobody
    holds. Naming a key_var that is in no store, with no inline key and no env file, is exactly
    that state — and it is the save that creates it, so it is the save that is refused.
    """
    c, tmp = api_client
    before = (tmp / "config.yaml").read_text(encoding="utf-8")
    r = _put(c, _body(key_var="D208_KEY_NOBODY_HOLDS"))
    assert r.status_code == 400, r.text
    assert "D208_KEY_NOBODY_HOLDS" in r.text
    assert (tmp / "config.yaml").read_text(encoding="utf-8") == before
    # provision it and the same save goes through
    _seed_store(c, "D208_KEY_NOBODY_HOLDS")
    assert _put(c, _body(key_var="D208_KEY_NOBODY_HOLDS")).status_code == 200
    assert _view(c)["key_source"] == {"source": "secret", "var": "D208_KEY_NOBODY_HOLDS"}


def test_switching_kind_onto_a_key_var_nobody_holds_is_refused(api_client):
    """A kind switch moves the key_var DEFAULT under the endpoint — openai's OPENAI_API_KEY
    becomes anthropic's ANTHROPIC_API_KEY — so an endpoint running on a stored secret can be
    stranded without any credential field being touched. That counts as changing where the
    credential comes from, which is why `prev` is compared at its own kind.
    """
    c, tmp = api_client
    _seed_store(c)                                    # OPENAI_API_KEY only
    assert _put(c, _body()).status_code == 200
    assert _view(c)["key_source"] == {"source": "secret", "var": "OPENAI_API_KEY"}
    r = _put(c, {"name": "dummy", "kind": "anthropic", "base_url": "http://x/v1"})
    assert r.status_code == 400, r.text
    assert "ANTHROPIC_API_KEY" in r.text
    assert _stored(tmp)["kind"] == "openai"
    # with that secret provisioned too, the same switch goes through
    _seed_store(c, "ANTHROPIC_API_KEY")
    assert _put(c, {"name": "dummy", "kind": "anthropic",
                    "base_url": "http://x/v1"}).status_code == 200
    assert _stored(tmp)["kind"] == "anthropic"


def test_a_keyless_local_backend_still_saves(api_client):
    """Ollama and vLLM run keyless on purpose — `key_source.keyless_ok` says so — and the
    editor resends the kind-default key_var it was handed. Nothing about where the credential
    comes from changed, so the save is not the one that created the state and goes through.
    """
    c, tmp = api_client
    assert _view(c)["key_source"]["keyless_ok"] is True       # the fixture's dummy endpoint
    r = _put(c, _body(base_url="http://127.0.0.1:11434/v1", key_var="OPENAI_API_KEY"))
    assert r.status_code == 200, r.text
    assert _stored(tmp)["base_url"] == "http://127.0.0.1:11434/v1"
    assert "api_key" not in _stored(tmp)


def test_a_new_endpoint_may_be_added_before_its_credential_exists(api_client):
    """docs/endpoints.md sequences it this way — add the endpoint, then give it a key — and the
    console's add form posts no `api_key` at all. The refusal must not close that door.
    """
    c, _tmp = api_client
    r = c.post("/api/settings/endpoints", json={"name": "vllm", "kind": "openai",
                                                "base_url": "http://10.0.0.5:8000/v1"})
    assert r.status_code == 200, r.text
    assert _view(c, "vllm")["key_source"]["source"] == "none"
