# Model gateway setup

Every model call this instance makes leaves through ONE gateway that is not part of this
repository: [llm-interfaces](https://github.com/wehnsdaefflae/llm-interfaces), whose stack runs
**Otari** (the routing gateway, on the docker host at `:4000`) and, beside it, the ONE standalone
**CLIProxyAPI** that holds the subscription logins (`:8318`). The scheduler stays what it always
was — the one agent loop, speaking two chat wires — and the gateway owns everything about where a
call goes: which provider serves a model name, which subscription or key pays for it, which login
it is billed through. Until 0.483.0 this repository ran its own CLIProxyAPI as a compose service
(the `cliproxy` sidecar behind a `claude-proxy` profile); that service, its init and migration
scripts and its config example are gone, and nothing here starts a model transport any more.

The decision models' local server moved out the same way: predator's SemIf readout is
[local-model-server](https://github.com/wehnsdaefflae/local-model-server) now
([decision models](decision-models.md#predator-hosting-the-measured-model)).

## The endpoints

All four live in `config.yaml` (Settings → Endpoints) and reach the gateway on the docker host's
bridge address, `172.17.0.1`, which every container of this project can route to:

```yaml
gateways:
  otari:                       # where each name is routed — sizes every model by its ROUTE
    routes_file: /srv/llm-interfaces/config.yml
    plans:                     # the CLIProxyAPI logins, by their catalog plan groups
      cliproxy: [claude, antigravity]
      chatgpt: [codex-plus]
endpoints:
  otari:                       # the Messages wire: Claude ids, and any model Otari routes there
    kind: anthropic
    base_url: http://172.17.0.1:4000/api
    key_var: OTARI_API_KEY
    gateway: otari
  otari-openai:                # the chat-completions wire, for the models validated on it
    kind: openai
    base_url: http://172.17.0.1:4000/api/v1
    key_var: OTARI_API_KEY
    gateway: otari
  otari-openai-plain:          # the same, for a model that refuses response_format outright
    kind: openai
    base_url: http://172.17.0.1:4000/api/v1
    key_var: OTARI_API_KEY
    schema_mode: none
    gateway: otari
```

Put `OTARI_API_KEY` in Settings → Secrets. There is no management endpoint and no management
key here: the proxy's own panel manages its logins (see below).

`gateways:` is how the limits refresh sizes each model by the ROUTE its name takes rather than by
the model's public figure ([endpoints](endpoints.md#windows-and-output-caps-are-discovered--leave-them-blank)).
`routes_file` is Otari's own `config.yml` — the rsched container reaches it through its `/srv`
bind — read for its `aliases:` and `providers:`; Otari's alias API would need its master key.
`plans` names the CLIProxyAPI catalog plan groups each subscription login holds, which no
listing says: the Claude and Antigravity logins behind `cliproxy`, the ChatGPT Plus (Codex)
login behind `chatgpt`. A plan change on the proxy side is an edit here. These two blocks are
edited in `config.yaml` by hand; the Settings endpoint form keeps an endpoint's `gateway:`.

**Model ids are the gateway's NORMALISED names** — `claude-opus-5`, `claude-sonnet-5`,
`claude-haiku-4-5`, `gemini-4-argon`, `gpt-5.6-luna`, `claude-opus-5-metered`,
`glm-5.3-uncensored`, … — which Otari's aliases resolve to a provider and a credential. A
catalog entry therefore names WHAT model it wants, and moving a model from a subscription to a
metered key (or to another provider) is an alias change in the gateway, with no catalog edit
here (a `-metered` id is the paid route of the model it names — [what a call
costs](#what-a-call-costs)). The model form's id picker lists what the endpoint's own models route answers
([endpoints](endpoints.md#model-id-picker)); a listed name is still not proof the route behind it
works, so use the card's *test* before binding a routine to one.

Reasoning effort is not a catalog attribute: it is chosen per role and reaches every call
([endpoints](endpoints.md#effort-not-a-catalog-attribute)). A route validated only with the action
tool FORCED (the Codex models on the Messages wire) is an endpoint that sets
`tool_choice: forced`; the subscription proxy strips thinking and effort from a forced call, so
nothing is forced unless an endpoint says so.

### When a provider refuses `response_format`

Otari rewords a provider's refusal of the structured-output field as *"Model X does not support
the requested response format."* — no underscore. The `openai` adapter's one degraded retry
without `response_format` reads the refusal's wording, so "response format" is one of its hints
beside `response_format`, `json_schema` and `structured` (`endpoints/openai_compat.py`). A model
that refuses it on every call is still better bound to `otari-openai-plain` (`schema_mode: none`):
the retry costs a round trip per turn, the plain endpoint costs none.

## What a call costs

Otari prices every request itself and returns the figure in the response's usage block as
`cost_usd`, a six-decimal USD STRING (`"0.000005"`), beside `pricing_source` — on the Messages
wire and on chat-completions alike (where an OpenRouter upstream adds its own numeric `cost`).
That reported figure is what bills a call. Both adapters read it into the usage's `cost`
(`endpoints/usage.reported_cost`: OpenRouter's `cost` when it is there, else `cost_usd`; a
missing, malformed, negative or non-finite value is no figure at all, never a zero), and the
pricing seam takes a reported bill at its word as the call's `notional` — so a pay-per-use call
through the gateway is metered, and its cost budget bounded, by what the gateway says it cost.
The decision adapters read it the same way.

A subscription model is priced `"0.000000"` there, which is its real bill and is kept as its
`cost`. A zero is not a price, though, so its `notional` is still the model's LIST price from the
public reference catalog — what the same tokens cost bought, the figure every cost ranking and a
subscription route's cost budget read ([run analytics](run-analytics.md#what-a-run-costs-volume-and-notional_cost)).

**`-metered` names the paid route of the SAME model.** Where one model is reachable both on a
subscription and pay-per-use, the gateway names the paid route `<name>-metered`
(`claude-opus-5-metered`, served by OpenRouter's `anthropic/claude-opus-5`). The scheduler reads a
trailing `-metered` as that suffix and nothing else (`catalogs.reference_name`), so the id looks
up `<name>`'s list price in the reference catalog — the fallback for a call whose bill was not
reported. Only the suffix counts: an id with `metered` elsewhere in it is its own name. Its
WINDOW is its route's like any other name's: `claude-opus-5-metered` is read from OpenRouter's
`anthropic/claude-opus-5` row, `claude-opus-5-5-metered` (Anthropic's own API) from the built-in
Claude table.

## Signing in, signing back in, and removing a login

The subscription logins belong to the standalone proxy and are managed THERE, on its own panel at
`/management.html` (Auth Files): the signed-in accounts and their status, signing one back in when
a refresh token is rejected, removing a lapsed login, and what is left of each account's window.
This scheduler holds none of that and no management key — an endpoint here is a transport
(0.606.0, the operator's call). Two practical notes kept from when it did:

* reaching that panel over the LAN needs the reverse proxy to route the management API as well as
  the page: both `/v0/management*` AND `/v8/management*` must go to the proxy, or its newer
  web build reports "v8 Management API not found" and tells you to upgrade a server that is
  already new enough (diagnosed 2026-11-24 — the request was reaching the model gateway, which
  404s it);
* the management API IP-BANS a caller for ~30 minutes after a few failed authentications, keyed
  on the socket address — behind a reverse proxy that is ONE address for every browser, so a bad
  key locks everyone out until it lapses or the proxy restarts.

## Decisions through the gateway

The decision catalog has a gateway route too. Otari serves TypeSafe's System One wire at
`/api/v1/systemone`, so a decision endpoint there takes the gateway's key and a normalised id:

```yaml
decision_endpoints:
  otari-decisions:
    protocol: systemone
    base_url: http://172.17.0.1:4000/api/v1
    key_var: OTARI_API_KEY
decision_models:
  jev: {endpoint: otari-decisions, model: "openrouter:typesafe/jev-1.13"}
```

The protocol carries images as an optional `images` list of data URLs (at most 16), which the
gateway and llama-server read and Jev itself does not — so a model on this protocol is sent
images only when its catalog entry says `multimodal: true`
([decision models](decision-models.md#the-two-protocols)).

## Verification

Use each endpoint card's *test*, then the opt-in synthetic transport suite against the gateway,
with the gateway's key exported under the name the suite reads:

```bash
CLIPROXY_API_KEY="$OTARI_API_KEY" RSCHED_LIVE_TESTS=1 RSCHED_PROXY_URL=http://172.17.0.1:4000/api \
  RSCHED_PROXY_MODEL=claude-sonnet-5 \
  .venv/bin/python -m pytest -n0 -q tests/test_proxy_live.py::test_proxy_scheduler_contract
```

These are real subscription calls. They check actions, prompt preservation, history, images and
usage; the Claude case checks actual cache reads. For a Codex id set `RSCHED_CODEX_MODEL` and
select `-k "codex and anthropic"`.

The independent live utilities (`claude`, `claude-login`, `claude-usage`, `frame-fill`) still use
the `claude` CLI baked into the engine image and its own dedicated login mount; scheduler model
calls never do. Do not delete a human login or a utility credential as part of endpoint cleanup.
