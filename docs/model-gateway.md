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
endpoints:
  otari:                       # the Messages wire: Claude ids, and any model Otari routes there
    kind: anthropic
    base_url: http://172.17.0.1:4000/api
    key_var: OTARI_API_KEY
  otari-openai:                # the chat-completions wire, for the models validated on it
    kind: openai
    base_url: http://172.17.0.1:4000/api/v1
    key_var: OTARI_API_KEY
  otari-openai-plain:          # the same, for a model that refuses response_format outright
    kind: openai
    base_url: http://172.17.0.1:4000/api/v1
    key_var: OTARI_API_KEY
    schema_mode: none
  cliproxy-mgmt:               # NO models: the subscription logins' management surface
    kind: anthropic
    base_url: http://172.17.0.1:8318
    quota_source: cliproxy
    quota_key_var: CLIPROXY_MANAGEMENT_KEY
```

Put `OTARI_API_KEY` and `CLIPROXY_MANAGEMENT_KEY` in Settings → Secrets. The management key
never reaches a routine: it is resolved on the server for the management routes alone.

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
up `<name>`'s list price and context window in the reference catalog — the fallback for a call
whose bill was not reported, and the window the run is sized by. Only the suffix counts: an id
with `metered` elsewhere in it is its own name.

## Signing in, and signing back in

The subscription logins belong to the standalone proxy, and the console still manages them —
through `cliproxy-mgmt`, the one endpoint bound to the proxy's management API. Its card lists the
proxy's accounts with the proxy's own status words: `token expired` when a refresh token was
rejected (`invalid_grant` — only a fresh sign-in fixes it), a usage-limit message with a
`retry after` time when a subscription window is spent (that one only waits). An endpoint with no
models bound is of no known family, so this card shows every account, offers every provider's
sign-in (Claude, Codex, Antigravity) and carries the Claude subscription's quota line
(`endpoints/cliproxy_mgmt.py`).

To sign one back in, press *re-authenticate Claude* (or *Codex*, *Antigravity*) on the card and
open the link it shows. The consent page then sends the browser to the PROXY'S callback address
(`http://localhost:54545/callback?code=…&state=…` for Claude, `localhost:1455/callback` for Codex,
`localhost:51121/oauth-callback` for Antigravity), which exists only on the server, so on your own
device the page fails to load while its address bar still carries the code. Copy the whole
address, paste it into the card's box and press *finish sign-in*; if the consent page shows a
code instead of redirecting, paste that. The console hands the code to the proxy's management
callback and polls until the token exchange settles. It never sees a token: its three routes
(`GET …/proxy-accounts`, `POST …/proxy-login`, `POST …/proxy-login/complete`) carry the consent
link, the state and the proxy's status words, and both POSTs are refused for the routine token
like every config-mutating route.

The terminal fallback — the proxy's own `--claude-login` with its callback port tunnelled over
SSH — belongs to the gateway repo now, since the container it runs in is that stack's.

`daemon/endpoint_watch.py` watches the same binding: every ten minutes it reads the account list
and records `ok`, `cooling`, `needs_reauth` or `unknown` per endpoint in
`.control/endpoint-health.json`, filing `endpoint_needs_reauth` once a day while a login is dead
([endpoints](endpoints.md#noticing-a-dead-credential-before-a-run-spends-a-turn)). It watches
`cliproxy-mgmt` alone — the Otari endpoints carry no management binding — so a dead login is named
on that card rather than on the endpoints whose calls it would fail.

## Decisions through the gateway

The `decide` action's catalog has a gateway route too. Otari serves TypeSafe's System One wire at
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
