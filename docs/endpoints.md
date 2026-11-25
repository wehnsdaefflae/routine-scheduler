# LLM endpoint setup

An **endpoint** is a model *transport*: one place the scheduler can send a chat completion
and get an answer back. Endpoints never act on their own — the scheduler's engine is the
only agent loop. A **model** is a named entry in the *catalog* that binds a provider model id
to an endpoint and carries the per-model attributes — multimodality, context window,
temperature. One endpoint serves many models, so those attributes live on the model, not the
endpoint. Reasoning **effort** is deliberately NOT one of them: it is a property of the job a
model is doing, so it is chosen per role (a routine's `efforts:`, a conversation's own, and the
server's `system_effort` — see *Effort* below), never on the catalog entry. You configure
endpoints and models once (Settings → Endpoints, or
`~/.config/routine-scheduler/config.yaml`), then every routine and the system model **picks a
model by name** — except a model call, which may also name an endpoint plus a raw model id
(see *Reaching a model the catalog does not carry*, below).

This instance sends every call through a separate gateway (Otari, with CLIProxyAPI holding the
subscription logins beside it) — see the [model gateway guide](model-gateway.md). **Decision
models** — Jev, local-model-server on predator — are not chat transports
and have their own catalog and Settings section: see [decision models](decision-models.md). The
`anthropic` kind also accepts an Anthropic-compatible gateway or proxy URL and its client key;
billing belongs to the upstream account, not the wire protocol.

## The two kinds

| kind | what it talks to | credential | billing |
|---|---|---|---|
| `openai` | any OpenAI-compatible chat API: OpenRouter, Featherless, vLLM, Together, … | API key (or none for a local server) | per provider (metered or subscription) |
| `anthropic` | Anthropic-compatible Messages API | provider API key, or a gateway's / proxy's client key | upstream plan (direct Anthropic is metered) |

Nine times out of ten you want `openai`: one kind covers every provider that speaks the
OpenAI chat-completions dialect, cloud or local.

## Adding an endpoint (web UI)

1. **Settings → Endpoints → + add endpoint.** Name it (the name is its identity — routines
   reference it), pick the kind, set the base URL (e.g. `https://openrouter.ai/api/v1`).
2. **Give it a credential.** Either paste an API key on the endpoint's card (stored inline
   in the server config), or set `key_var` to a name like `OPENROUTER_API_KEY` and put the
   value in **Settings → Secrets** — the central store. Secrets win for anything you might
   rotate; inline wins for quick starts. A gateway or subscription proxy takes its own client key. The card's **credential in use** line shows
   which rung of the ladder is actually live (inline / secret / env file / none — labels
   only, values are never returned) and warns when an inline key **shadows** a set secret:
   the inline key wins, so editing the secret changes nothing until the inline key is
   removed. To remove it, send an **explicit null**:
   `PUT /api/settings/endpoints/<name>` with `{"api_key": null}` deletes the inline key and
   the Secrets rung takes over. A **blank or absent** `api_key` is deliberately
   non-destructive — the editor submits that field empty on every unrelated edit, so only a
   null means "delete it". A save that would leave the endpoint with **no** resolvable
   credential is refused (400) naming the `key_var` to provision: you cannot clear the last
   key, nor point a working endpoint at a secret nobody holds. An endpoint that already
   resolves nothing keeps saving normally — a keyless local backend (vLLM), or one
   added with no credential field at all, which is why step 1 above does not ask for one.
   Filling `key_var` in on the **add** form does mean the secret has to be in the store
   already: add the secret first, or add the endpoint bare and give it a key on its card.
3. **Test it.** Pick a model id on the card (the dropdown lists what the endpoint itself says
   it serves — see *Model-id picker* below) and hit *test* — you get latency, whether the
   model respected a JSON schema, and the raw error (with an auth hint) if the call failed.
   Fix problems here, not mid-run.
4. **Add the models it serves.** In the endpoint list's **Models** section → *+ add model*.
   Name it (the name is what routines reference — e.g. `gpt-4o`, `glm`, `opus`), pick the
   endpoint, pick the provider's model id from the list the endpoint serves, and set its
   attributes: **multimodal** (default by the endpoint kind), **context window**,
   **temperature**. One endpoint can serve
   many models with different windows and vision support — add one catalog entry per model.
5. **Point roles at models.** Set the server-wide **system model** (used only for setup-time
   work: routine creation and workflow generation) by picking a catalog model (plus an optional **effort** for it — blank is the provider's
   default), and per
   routine the model roles — **main** (the orchestrator loop; spawned children run it by
   default, and a call may override per child), **tool_call** (a scoped model call), and the
   optional **uncensored** (the refusal-clarification harness — see below — and, in a
   conversation, the model a reply flagged ⚑ as a refusal is handed to) — on the
   routine's page, each a catalog model name.
   An unset **main** falls back to the system model. An unset **tool_call** falls back to
   `system_tool_call_model` — the fleet-wide default for the cheap half of the work (the
   scoped model calls, a turn a run swaps down with `role_swap: cheap`, the claim verifier's second
   opinion) — and, while THAT is unset, to the system model exactly as main does. Setting it
   is what makes a cheap turn actually cheaper: with no default for the role, every routine
   that named no `tool_call` of its own resolved both roles to one model and saved nothing.
   **uncensored has no fallback** — leave it unset and refusals are still flagged +
   isolated, but no fragment is referred. See *Refusal clarification* below.

## Adding endpoints + models (config file)

Endpoints go under `endpoints:`; the model catalog under `models:` (name → a model bound to an
endpoint); the system model is a catalog name. In `~/.config/routine-scheduler/config.yaml`:

```yaml
endpoints:
  OpenRouter:
    kind: openai
    base_url: https://openrouter.ai/api/v1
    key_var: OPENROUTER_API_KEY   # name in the Secrets store (or use api_key: inline)
    schema_mode: json_schema
    context_tokens: 100000         # a DEFAULT models on this endpoint inherit

models:                           # the catalog: each entry binds a model id to an endpoint
  glm:
    endpoint: OpenRouter
    model: z-ai/glm-5.2           # text-only → inherits openai's multimodal default (off)
  gpt-4o:
    endpoint: OpenRouter
    model: openai/gpt-4o
    multimodal: true              # this model sees images/PDFs; glm above doesn't
    context_tokens: 128000         # overrides the endpoint default for this model

system_model: glm                 # the fallback model for setup-time work — a catalog NAME
system_tool_call_model: ""        # fleet default for the tool_call role; blank = system_model
```

### Endpoint fields (the transport)

- `base_url` — everything before `/chat/completions`. Self-hosted vLLM: `http://host:8000/v1`.
- `api_key` / `key_var` / `key_env_file` — credential lookup order: inline `api_key`
  first, then `key_var` in the Secrets store, then `key_var` inside `key_env_file`
  (a `~/.credentials/*.env` style file). `key_var` defaults per kind — `OPENAI_API_KEY`
  for `openai`, `ANTHROPIC_API_KEY` for `anthropic` — set it explicitly for an aggregator
  (e.g. `OPENROUTER_API_KEY`). Over the API the inline `api_key` has three states, not two:
  absent and `""` keep whatever is stored, `null` clears it
  (`web/settings/endpoint_keys.py`).
- `schema_mode` — how the endpoint enforces the one-JSON-action-per-turn contract:
  - `json_schema` (default): strict `response_format` — OpenRouter, OpenAI, vLLM.
    Providers that reject it — with a 400, or a generic 503 that hides a schema-incapable
    backend — get one degraded retry without it, so it is safe to leave on.
- `tool_choice` — `anthropic` kind only: how the action tool is offered. `auto` (the default)
  lets the role's reasoning effort and its thinking reach it; `forced` keeps a route validated
  only forced (the Codex models the subscription proxy serves on this wire), at the cost of the
  effort — a forced call is answered without thinking. Config-only: a Settings save that does
  not send it keeps what config.yaml says.
  - `none`: nothing requested; the code-level validate-and-retry loop does all the work.
- `context_tokens` — the full input + output token window used as a fallback when model
  metadata is unavailable. Default `25_000`. Provider discovery takes precedence over this
  endpoint fallback; an explicit per-model override takes precedence over discovery.
- `temperature` — optional **default** temperature catalog models inherit when unset. Current
  Claude models removed the sampling parameters and answer 400 to one; that 400 is absorbed
  (the field is dropped for one degraded retry), so setting it costs a wasted round trip per
  turn on those models rather than failing them — steer them with the role's effort instead. It still
  works on everything else the wire serves, Haiku and a proxy's `gpt-*` ids included.
- `extra_body` — merged into every request body (`openai` kind only). This is where
  aggregator routing lives, e.g. OpenRouter provider pinning:

  ```yaml
  extra_body:
    provider:
      order: [Fireworks, DeepInfra]
      allow_fallbacks: true
      ignore: [SomeProvider]   # e.g. providers whose constrained decoding corrupts output
  ```

All of the above are editable on the endpoint's card in **Settings → Endpoints** (under *edit
fields*: `temperature`, `key_env_file`, and — per kind — the
`openai` `extra_body` as JSON). A save that omits a field the form doesn't show preserves the
stored value rather than clearing it.

### Model fields (the catalog)

A catalog model binds a provider `model` id to an `endpoint` and carries the attributes that
vary *per model*. Leave an attribute unset (or blank in the UI) to inherit the endpoint's
default. Routines and the system model reference a model by its catalog **name**.

- `endpoint` — the configured endpoint that transports this model (required).
- `model` — the provider's model id (required), e.g. `openai/gpt-4o`, `z-ai/glm-5.2`. The
  Settings form fills this from the endpoint's own listing (*Model-id picker* below) rather than
  a free-text box.
- `multimodal` — whether this model takes image/PDF input natively. **Default by the endpoint
  kind**: on for `anthropic` (images + PDFs), off for `openai`. Set
  it explicitly to turn native vision *on* for an `openai` vision model (GPT-4o, Gemini) or
  *off* for a text-only one. When off, images/PDFs a routine views route to the `vision` util
  instead — vision still works, just indirectly.
  **`multimodal` is not the only gate: a file must also be small enough to send.** One predicate
  decides it for both `view_image` and conversation auto-attach — `media_types.oversize_reason`
  — and it checks two bounds: `NATIVE_MEDIA_MAX_BYTES` (7 MiB raw; base64 inflates ~33% into most
  providers' ~10 MB request limit) and `NATIVE_MEDIA_MAX_PIXELS_PER_SIDE` (2000 px per side,
  F623). The pixel bound exists because bytes do not predict it: a 2 MB 12000x9000 screenshot
  clears the byte ceiling and then makes the provider refuse the **whole message** — every image
  in it and the text with them — on `At least one of the image dimensions exceed max allowed size
  for many-image requests: 2000 pixels`. An over-bound file routes to the `vision` util and the
  run is told the limit and the file's own measured size, so it can downscale and look properly.
  Dimensions are read from the file header (`image_dimensions`, PNG/JPEG/GIF/WEBP, no Pillow); an
  unmeasurable header means "unknown", never "within the limit". A PDF has no pixel size and is
  judged by bytes alone, and so are audio and video.
- `audio` / `video` — whether this model takes AUDIO or VIDEO input natively (D192). **No endpoint
  kind default**: unlike images, there is no kind whose models all have ears — on one
  OpenAI-compatible endpoint `gpt-4o-audio-preview` takes audio and the `glm` beside it does not —
  so each is a plain per-model opt-in, off until you turn it on. `multimodal` is still the master
  gate (a model with `multimodal: false` takes nothing), and **the wire has to carry it too**: the
  `openai` kind carries audio as an `input_audio` content part and carries no video; the
  `anthropic` kind (Messages API) carries neither, because it publishes no block for them. So a
  model bit and an endpoint that cannot carry the modality = not sent natively, by design.
  There is **no fallback** for these two the way the `vision` util backs up images — that util
  describes pictures — so a file the run cannot attach comes back as a refusal saying which half
  is missing, and naming `NATIVE_MEDIA_MAX_BYTES` (7 MiB RAW; base64 inflates it ~33%). Video
  meets that ceiling within seconds of footage: extract the audio or some frames first (the
  `video-extract` util) or transcribe it (`deepgram`), then attach that.
  The allowlist of types itself is one structure — `endpoints/media_types.py`'s
  `MEDIA_MODALITIES`, mime → `image` / `pdf` / `audio` / `video` — and everything (the engine's
  pre-flight, each adapter's `supports_media`, the provider block builders) asks it rather than
  comparing mimes.
- `context_tokens` — an optional override for this model's full input + output token window.
  Leave blank to use provider discovery, then the endpoint fallback. Compaction compares an
  explicitly estimated input token count against this window and reserves `max_tokens` for
  output. The estimate includes UTF-8 text, message framing, action schema and media; it is not
  an exact provider tokenizer count. Provider usage is authoritative — and it is USED: after
  each completion the run divides the prompt size the provider reported by its own estimate of
  the same messages and, when the estimate reads light, compacts against the window divided by
  that ratio (`engine/window.note_prompt_size`, clamped to 2x, measured only on prompts already
  past a fifth of the window). Without it the estimate was corrected only by a provider's
  oversize 400 — six of those in a fortnight, four in one run. Character counts are only used
  internally when estimating text or trimming a string.
- `temperature` — sampling temperature; inherits the endpoint's when unset. Leave it blank on a
  current Claude model — see the endpoint field above.
- `max_tokens` — the requested **output** cap per completion, sent on every engine call
  (turns and scoped model calls). Leave it blank: it resolves through the same chain as the window
  (*Windows and output caps are DISCOVERED* below) — the route's published maximum, then the
  endpoint's `max_tokens`, then a generous engine default (16,384). Settings flags a model still riding
  that default with a **⚠ max_tokens** chip — implausible hand values (below 4,096, or larger
  than the context window) are flagged too, so "every model set correctly" is auditable at a
  glance.
- `fallbacks` — the ordered **failover chain**: catalog model names tried in order when this
  model fails hard. See *Failover & cooldowns* below.
- `note` — **when to use this model**, one line, in your words (D279(a)). A run choosing a
  `model` for `child`, or a `role_swap` position, otherwise sees a catalog of
  NAMES: nothing told it that one model is cheap and blunt, that another is the only one here
  with vision, or that a third is rate-limited to uselessness before noon. The `list_models`
  action returns it as `when_to_use` beside the name — only when set, because an empty line
  would read as "no opinion recorded" where there is simply no field, on every model of every
  catalog. It is **advice, never a routing rule**: nothing in the engine reads it to decide
  anything (`tests/test_model_when_to_use.py` pins that the only readers are the surface that
  stores it and the surface that serves it), so a sentence here can mislead a run but can never
  silently change what runs where.

### A listed model is not necessarily an enabled one

A subscription proxy's `/v1/models` is the proxy's REGISTRY, not its upstream's answer: it can list
a model the upstream provider has published but not switched on for the account. The live case
(2026-10-14): CLIProxyAPI listed `gemini-4-argon-high` under the Antigravity sign-in while Google's
own `fetchAvailableModels` for the same account carried the model only as `gemini-4-argon-disabled`
with an empty quota — every enabled model there has a `remainingFraction` — and every call
answered `404 Requested entity was not found`. The *test* result therefore carries a `hint`
(`endpoints/failure_hints.py`, shown above the raw error): NOT FOUND from a proxy reads "listed,
not enabled for this account yet", `unknown provider for model` reads "no signed-in account
serves this id", and `auth_unavailable` reads "every account that could serve it is cooling
down or out of quota". The raw error stays beneath it, and an error nobody has explained gets no
invented hint. The proxy also cools the model after a 404, so a retry within minutes answers 503
with the same cause inside.

### Model-id picker

Typing a provider model id by hand was how a catalog entry ended up naming something the
endpoint never served. The model form's id field, and the endpoint card's *test* box, are
therefore a `<select>` filled from `GET /api/settings/endpoints/{name}/model-ids` —
`{ok, ids: [...sorted...], error?}`, read-only and refused for the routine token like every
`/api/settings` read. The server asks the endpoint the same way `limits` does (OpenRouter's
and Nano-GPT's own listings; for everything else `GET {models route}` — `{base}/models` on an
`openai` endpoint, `{base}/v1/models` on an `anthropic` one, so a subscription proxy answers
`{"data": [{"id": …}]}`), with the endpoint's own key, resolved on the server and never
returned. The route never raises: an unreachable endpoint, a refused key or a body that is no
listing comes back as `{ok: false, error}` with a fixed one-line reason (no upstream body). The
page then falls back to a plain text input and shows that reason. Two details: a value the
catalog already holds is kept as an option even when the listing lacks it, marked *not listed*
(opening a card never silently changes config); and a proxy lists no model for a credential it
cannot use, so a signed-out account makes its models vanish from the picker — sign in from the
card first (`model-gateway.md`).

### Effort (not a catalog attribute)

Reasoning effort — `low | medium | high | xhigh | max`, blank = the provider's own default —
is chosen where a model is USED, so one catalog entry serves every level instead of one row per
level: per role on a routine's page (`efforts: {role: level}`), per conversation, and for the
system model and the context-compaction model in Settings (`system_effort` / `compaction_effort`,
saved with `PUT /api/settings/system-model` and `PUT /api/settings/compaction-model`, both
`{name, effort}`; the page's list of levels is `effort_levels` in the endpoints payload). A role with no model of its own runs the system
model at the role's effort if the routine sets one, else at `system_effort`. Each kind maps the
level to its own reasoning knob (`openai` collapses `xhigh` / `max` → `high`); lower it if a
reasoning model spends its whole output budget thinking instead of answering.

### Failover & cooldowns (`fallbacks`)

A provider outage at cron-fire time used to kill the run after the transport's own retries
(3 tries, exponential backoff — or, when the host REFUSES the connection, up to two minutes of
tries, `base.UNREACHABLE_PATIENCE`: every route leaves through one gateway, so failing over to a
model behind the same refused port cannot help, and a 14 s gateway restart on 2026-11-19 ended a
run in ~6 s across two rungs). With `fallbacks: [other-model, …]` on a catalog model, the
engine instead **fails over**:

- **Mid-turn**: when the serving model fails hard (retries exhausted, or a non-retryable
  error such as a dead host or bad key), the turn is re-issued to the next chain member —
  with *its* endpoint, temperature, and max_tokens (the effort stays the role's). The switch is logged visibly as
  a transcript `error` event carrying a `failover` payload (`from`/`to`/`cooldown_s`), and
  every turn's usage records the model that actually served it, so spend attribution stays
  correct. Only when the whole chain is exhausted does the run fail as before.
- **Classifier refusals** engage the same chain, on the FIRST refusal. Claude Fable/Mythos-
  class safety classifiers decline a request as an HTTP **200** with `stop_reason:
  "refusal"`, empty content, and a `stop_details` naming the category (`cyber`, `bio`,
  `frontier_llm`, `reasoning_extraction`) — so error-rate monitoring never sees it, and
  re-sending the refused prompt to the same model usually just earns another refusal.
  The engine therefore never blind-retries a refusal: it logs a transcript `error` with a
  `refusal` payload (category + explanation), runs ONE refusal-clarification pass (below:
  isolate the trigger, deliver its essence to the uncensored harness), then advances
  the fallback chain — cooling the refused model for this
  run only. With no usable fallback the run fails HONESTLY, naming the category, instead
  of dying as "empty completion". OpenAI-compatible providers signal the same class of
  decline as `finish_reason: "content_filter"` (or the spec's `message.refusal` field) —
  treated identically. **If a catalog model runs a classifier-bearing provider model
  (Fable/Mythos-class), give it a `fallbacks:` entry** (e.g. an Opus-class model — the
  provider's own recommendation) so a refusal costs one switch, not the run.
- **Cooldown**: a hard-failed (endpoint, model) is marked *cooling* for 5 minutes — every
  later resolution in the same run process (main turns, scoped model calls, compaction, spawned
  children) skips it instead of hammering a flapping provider. When every remaining member
  is cooling, the one already serving is used anyway (a run never stalls on bookkeeping).
  Cooldowns are process-local: a fresh run probes the primary once and re-marks it if the
  outage persists. A provider that answered with a `Retry-After` longer than five minutes is
  cooled for exactly that long — it has said when it will serve again, and the default is
  only a guess.
- **A switch is forward-only within a run.** A cooldown expires; a weekly quota does not.
  Once a run has moved down the chain it starts every later turn from the member that is
  serving and never walks back up, so a quota-exhausted primary cannot flap back in five
  minutes later. Before this, a run on an exhausted primary oscillated for its whole life —
  123 switches across 28 runs in ten days, ~6.5 minutes apart — and each swing landed after
  the 5-minute cache TTL had expired, so the whole prefix was re-written at 1.25× on both
  models. A fresh run still probes the head once, which is what brings a recovered provider
  back with nothing to reset.
- **The chain prefers a member that can take the request.** Images and prompt size travel
  WITH the request, so a text-only or too-small rung is passed over while a capable one
  remains (a `multimodal: false` model answers `400 … does not support image inputs`; a 32k
  one answers `maximum context length is 32768`) — each of those costs a round trip and
  marks a healthy model cooling for five minutes. It is a preference, not a veto: when no
  remaining member fits, the next one is taken anyway and the engine repairs what it can
  (images become `vision`-util text, an oversize prompt is compacted into the new member's
  window).
- **A substituted run is told what it lost** (`engine/substitution.py`). Two messages, with
  deliberately different lifetimes. WHO is serving goes to the turn's debris — it exists to
  steer the inherited reply (`[MODEL FAILOVER — you are no longer the model this routine was
  configured with] …`, D146-B) and is dropped with the rest of the debris once that reply is
  accepted. WHAT THE RUN MUST NOW BUDGET AGAINST is durable, an appended `ENGINE NOTE:
  CONTEXT WINDOW REDUCED …` naming both models and both windows as numbers: because the
  preference above is not a veto, a chain may step DOWN in window, and a run's only standing
  statement about its own window is the `CAPABILITIES` line composed at boot under the BOUND
  model's figure — which the prompt's append-only caching contract forbids rewriting. Emitted
  only when the window actually shrank against the chain HEAD (so a two-rung descent
  200k → 200k → 32k is announced, which measuring each step against the previous rung would
  miss), and latched at the smallest window already announced, so a note is never the layer
  talking about itself. `llmsectest-weekday:20260922-060001` spent $1.03 over 106 turns on a
  run that was unrunnable from turn 0 — a 32,768-token substitute under a 200k-class prompt —
  and `weightloss:20260923-220004` $1.01 over 193 turns diagnosing its own field-shifted
  output as "the model cannot reliably hold the action schema", two rungs down and never told.
- **A floor already over the window is HARD-COMPACTED at boot, and the run starts** — the
  engine's first act, before turn one, is to drop the middle to whatever fits
  (`compaction.maybe_compact`, then `clamp_to_cap`) and to STATE the loss: an engine note in the
  run's own prompt, rendered on the conversation and run pages, plus a `prompt_floor_over_window`
  health event carrying `floor_tokens`, `window_tokens`, `after_tokens`, `elided_messages`,
  `clamped_messages` and `fits` (`engine/promptfloor.py`, fired from `boot` once the prompt is
  composed). The recording-only first version (D223, 2026-10-30) earned the evidence for this
  one: it fired nine times for a single conversation, `c-20261014-134208`, every attempt bigger
  than the last (1,063,175 → 1,138,237 estimated tokens against a 1,000,000-token window), after
  a week of `prompt_oversize_shrunk` passes that had stopped being able to help. The operator
  chose compaction over refusing the start (D223, 2026-11-10): *"start it, but have the engine's
  FIRST act be a hard compaction that drops the middle to whatever fits, so the thread continues
  with a stated loss"*. Compacting may act on the catalog figure where refusing could not — an
  F278 run-local correction can make the real clamp smaller, and a veto resting on an
  approximation would turn a degraded run into no run, while compacting early only costs context
  the run can still read out of its transcript. No archival model call is made here: that is a
  model call, and this is the engine's first act. A floor that still does not fit afterwards
  (head, tail and system prompt are incompressible) is recorded with `fits: false` and said in
  the note; the run starts regardless and its first completion fails for length as it would have.
- **Every switch reaches the health stream** as a `model_failover` event carrying the chain
  head, both model names and a reason (`rate_limit` · `auth` · `server` · `refusal` ·
  `empty` · `other`) — a run that fails over finishes `ok`, so without it a fleet running
  all day on metered fallbacks looks healthy.
- **Retry, failover and overflow read what a failed call DECLARED** (`endpoints/errorsignal.py`,
  R3108): the provider's error enum and HTTP status, never English sentences in its body.
- Chains are **transitive, breadth-first**: the named model's own `fallbacks` first, then
  each of theirs, in declaration order. `Fable Max: [Fable]` therefore reaches everything
  `Fable` falls back to, with no duplicated tails to keep in sync — write each model's own
  alternatives and the ladder follows. Self-references, duplicates, and unknown names are
  reported as config problems and skipped, and a broken rung never truncates the ladder
  below it. Roles without fallbacks behave exactly as before — the feature is opt-in per
  catalog model, and `routine.yaml` still maps each role to ONE catalog name.

```yaml
models:
  glm:
    endpoint: OpenRouter
    model: z-ai/glm-5.2
    max_tokens: 32768             # the model's real output limit
    fallbacks: [glm-featherless]  # tried when OpenRouter fails hard
  glm-featherless:
    endpoint: Featherless
    model: zai-org/GLM-5.2
    max_tokens: 16384
```

### Prompt caching (automatic — no config)

Prompt caching needs no setup. Cache traffic is reported separately in usage — `cached_in`
(the ~0.1× re-reads) and `cache_write` (the ~1.25× writes) — and kept out of the `in` count, so
token budgets keep their meaning.

Who caches how:

- **`anthropic` kind** — explicit breakpoints on the tools block, the system prompt and a moving
  one on the last message, every turn, subscription proxies included.
- **OpenAI-, DeepSeek- and GLM-style backends** — implicit: they hit on a byte-stable prefix by
  themselves, which the engine's append-only message list gives them, and report the hit as
  `cached_tokens`.
- **Anthropic models through an aggregator** (`anthropic/…` on OpenRouter) — the exception that
  needs saying: they cache ONLY on an explicit breakpoint, so routing one through an aggregator
  does not inherit implicit caching. A conversation turn on such a model therefore carries a
  top-level `cache_control` marker. Without it the metered Opus fallback re-sent its whole
  prefix at full price every turn — twelve turns of one live run, ~100–117k input each,
  `cached_in` 0, **$6.63**.
- **One-shot calls** (a scoped model call, an archival digest, the refusal classifier) are not
  written to the cache: their prefix is never sent again, so the write would buy a read that never
  comes. On the OpenAI-compatible wire that means no marker. On the `anthropic` wire it means ONE
  marker on the smallest prefix the request has — the tool definition, else a leading slice of at
  most 1,000 chars cut from the system prompt or the first message, at a line break where one
  falls (`anthropic_messages.claim_placement`) — because the subscription proxy behind this
  instance's gateway (CLIProxyAPI, v7.2.156 when measured) places its OWN breakpoints on the
  tools, the system prompt and the last message of any request that carries none, with no setting
  to stop it. Before the claim, 100% of the archival calls' input (2.94M tokens over four calls)
  and 95% of 190 subcalls' was billed as cache writes in the week to 2026-10-08. A ~150-token
  tool definition, like a 1,000-char slice, is below the minimum prefix the API caches, so the
  claim itself writes nothing
  there.

**The 5-minute TTL is the right default — measured twice, do not raise it.** A cache entry
expires five minutes after its last use, so a turn following a long tool call re-writes the whole
prefix. That is real: over one fortnight on the subscription endpoint, 15,269 back-to-back turns
wrote 40.8 M tokens while 109 turns after a gap wrote 25.5 M — **38% of all cache writes**,
nearly all full rewrites, concentrated in a handful of long-tool routines. A blanket 1-hour TTL
is still not the answer, and the arithmetic says so: writing at 2× instead of 1.25× costs about
+30.6 M on the turns that never needed it to buy back about 29.2 M. Slightly WORSE than
break-even, and it raises the fleet's steady-state weighting. Nor is a per-model `cache_ttl`
knob worth it for the two or three routines involved. What DOES pay is fewer long gaps inside a
turn loop.

### Every retry attempt is visible (F655)

The retry loop reports each boundary from INSIDE itself, and it has to be inside: instrumentation
wraps an adapter from the OUTSIDE (`endpoints/instrument.InstrumentedEndpoint`) while
`base.with_retries` sits within the adapter's own `complete()`. So one `started` record covered
however many attempts a call took — worst case three attempts of `DEFAULT_TIMEOUT` (600 s) behind
a single row, which is how a retrying run became indistinguishable from a frozen one. The
operator hit exactly that: a resumed run's first call never got an answer, the 600 s read timeout
cut it, the retry answered in 1m10s, and for 11 minutes nothing anywhere said so.

- `endpoints/attempts.py` is the one-way channel: `with_retries` calls `attempts.boundary(...)`
  before each sleep and imports nothing from `instrument`; `instrument` installs a reporter for
  the duration of one call and knows nothing about tenacity. The reporter is a **ContextVar**, so
  parallel subrun threads each report to their own call's record.
- The record is an **`attempt` phase on the same call id** (a retry is the same call), carrying
  `attempt` (the one now starting), `tries`, `after` (what ended the previous attempt) and
  `retry_in` (the wait). It lands in `runs/<ts>/llm-tasks.jsonl` and on the `llm_task` bus event
  like every other phase.
- `llm_tasks.TaskCenter` folds it to the status **`retrying`** — a third state, neither thinking
  nor terminated. `llm_tasks.IN_FLIGHT` is the one tuple every "has this call ended" check reads
  (orphan sweep, age backstop, process pruning), because F572's leak was precisely a predicate
  that named one in-flight status and missed another. The dock mirrors that set.
- The terminal failure is NOT reported by the loop — the wrapper records it as the call's own
  `failed` — so an exhausted call books each boundary once and the failure once.
- **What ended an attempt has to be readable.** httpx's timeout exceptions carry an EMPTY
  message, so `post_json` once produced `claude-proxy: ` for a 600 s cut. `base._transport_cause`
  names the clock that ran out and the bound it was set to; an error with words of its own keeps
  them.

### Why raw httpx, and not the provider SDKs

The transports are `httpx` + `tenacity` on purpose. An SDK would replace ~120 lines (`post_json`,
the status classifier, the Retry-After read, the retry wrapper) and keep every line that actually
earns its place, because each provider-specific degrade needs the raw status and body: the
`output_config`/`cache_control` strip for gateways, the `temperature` and effort drops for the
Claude models that refuse them, the `response_format` 400 **and** the generic
503 that hides a schema-incapable backend, the 402 "can only afford N" retry, the three catalog
readers, the proxy-management routes. On top of that the Anthropic
SDK is built on `httpx2` — a second HTTP stack beside the `httpx` the daemon, web layer and utils
already use — its structured-output helpers duplicate an engine-side schema guard that must stay
provider-agnostic, and its retry policy is the one `tenacity` already implements here with a
Retry-After hint. Measured call durations (p99 45 s, max 414 s against a 600 s ceiling) show no
need for its streaming helper either. Net: one more dependency stack, zero special cases removed.

## Reaching a model the catalog does not carry (F868)

A model-call capability's model takes a role, a catalog name — or **`<endpoint>/<model id>`**.
The **endpoint** half must be a name from `endpoints:` (`list_models` lists them with their
base URLs); the **model id** half is sent verbatim and needs no catalog entry. Per-model
attributes come from the endpoint's own defaults with the provider's published window and
output cap in between — the same precedence a catalog model gets, minus its per-model tier —
and the resolved ref carries **no catalog name**, so an unregistered model is never reported as
a registered one.

**Why not a URL.** `EndpointRegistry.for_endpoint` resolves the spec against the configured
set, so an endpoint named by a raw URL would be a network destination nothing reviewed. The
endpoint set stays the operator's; what opens is the model id, which is the half the catalog
actually made impossible. A URL fails as the unconfigured endpoint name it is, and the error
names the configured ones.

**Not for a child run.** A child (`child`) is configured with `models["main"] = <catalog
name>` and resolves everything through the catalog, so an endpoint spec is refused there with a
reason naming where one is taken. A DECISION endpoint named here is refused too: it is a
different wire (one typed answer, no free text), reached as its own capability.

Since D288 this route is the UTIL's, not an action's — see *What a util gets* below, which is
how the endpoint set and its keys reach the call.

**What a util gets.** The same knowledge reaches a util as `RSCHED_LLM` (the configured
endpoints plus which one each model role resolves to) and `RSCHED_LLM_KEYS` ({endpoint: api
key}, for the ROLE endpoints only) — declared-only, like every injected name. That is what lets
an LLM-calling util send work to the endpoint its own routine runs on instead of hardcoding a
provider. See `endpoints/routine_env.py` and [sandboxing](sandboxing.md).

**Local models are the case this exists for.** A routine whose files must not reach a cloud
model (private scans, anything the operator has not cleared to leave the box) could not say so
through the engine's own channel while the catalog was cloud-only. Adding a local box is an
ordinary endpoint entry — no catalog model needed:

```yaml
endpoints:
  omen:
    kind: openai
    base_url: http://192.168.0.201:8790/v1
    # key_var: OMEN_AGENT_KEY   # only if the server wants one; local vLLM needs none
```

## Provider recipes

**OpenRouter** (one key, hundreds of models) — `kind: openai`,
`base_url: https://openrouter.ai/api/v1`, key from [openrouter.ai/keys](https://openrouter.ai/keys).
Model ids look like `z-ai/glm-5.2`, `qwen/qwen3.6-35b-a3b`.

**Featherless** (serverless host for *any* public Hugging Face model — community
fine-tunes and abliterated/uncensored variants included) — `kind: openai`,
`base_url: https://api.featherless.ai/v1`, key from
[featherless.ai](https://featherless.ai) (flat-rate subscription, not per token). Model id
= the HF repo id, e.g. `huihui-ai/GLM-4-32B-0414-abliterated`. Any public safetensors
model with 100+ downloads and a supported architecture is served automatically; larger
models need the bigger plan (72B-class on the base tier, 700B-class like GLM 5.2 on the
top tier).

**Self-hosted vLLM** (any HF model on your own GPUs, incl. rented ones — Runpod
serverless exposes `https://api.runpod.ai/v2/<endpoint-id>/openai/v1`) — `kind: openai`,
base URL of the server, whatever key you configured it with. This is the guaranteed path
for a model no provider lists.

**Anthropic API** — `kind: anthropic`, no base_url needed, `sk-ant-…` key. Metered: know
your budget caps. The action rides one tool offered on plain `auto` (`_AUTO`), which lets a
reply chain several CALLS — because a
FORCED tool choice makes the configured effort meaningless: the Messages API allows thinking
only on `auto`, the newest Claude models on the direct API (Fable 5.1, Opus 5.5, Sonnet 5.5)
refuse forcing with a 400, and the [subscription proxy](model-gateway.md) strips thinking
and effort from a forced call. Measured through the proxy on 2026-10-08: forced, Opus 5 at
effort `low` and `max` answered alike (467 and 551 output tokens, no thinking); on `auto` it
thought, and Opus 5, Sonnet 5 and Fable 5 all answered with the action, on a first turn and a
third, with the real composed prompt and the full schema. From 0.370.2 to 0.395.0 every call was
forced, so no configured effort reached a Claude model on a structured turn. 0.372.0 had tried
`auto` once and Opus then answered `tool_use` with nothing readable — a shape never reproduced
since; it can no longer cost a run, because an `auto` reply no action can be read from is
re-asked ONCE with the tool forced inside the same call (`stop_details["forced_reask"]` marks
that answer, which ran at the proxy's default effort, and the warning log names what the unread
reply carried). An endpoint set `tool_choice: forced` sends the forced choice, and on a model
that refuses it gets plain `auto`. A model that answers in text instead has its action
read from the text — and a THINKING model often does both in one reply: its first action as JSON
text, the next as the call, narrating the first as done. The engine runs the reply's actions in that
order (`engine/replyactions.py` for the order, `engine/chain.py` for the run), stopping at the
first failure and handing the rest back NOT EXECUTED by name in that observation; before 0.406.0
the adapter's call was all it read, so the first action vanished and the call that claimed it ran
(R2443-R2445). The cause is removed at the wire since 0.407.0: on `auto`
every past action is SENT as a call of the action tool and its observation as that call's result
(`anthropic_messages.native_turns`), so the model reads its history as calls rather than JSON text
and a reply ends at its one call — in live replays of the same turn, two actions in 5 of 5 replies
before, one call in 5 of 5 after, on Opus 5 and Sonnet 5. **That measurement is also why the
one-call rendering teaches by example**: until 0.550.0 the body additionally asked the provider
for at most one `tool_use` block, so a model forming a chaining intent could express it only as
JSON text — the channel this rendering teaches it not to use. The ask is gone; the history
rendering is unchanged, and whether a chained reply should be rendered BACK as its several calls
is a separate question (the engine's list keeps one action per assistant message). The rendering is positional and
deterministic, so the cached prefix holds; no thinking block is stored or passed back (the API
accepts a continuation without one, measured 2026-10-10); a forced route keeps the text history
it was validated on. A model that refuses several
optional fields (a configured `temperature`, the effort knob) is degraded one 400 at a time.
A reply no action can be read from names what it carried — block types, a tool call's name,
its input's type — in the run's empty-completion error.

## Prices are DISCOVERED too — and never guessed

The same daily refresh re-reads every configured model's PRICE into
`<routines>/.control/model-prices.json` (`endpoints/prices.py`), so each completion carries a
`notional` dollar figure for its whole context volume — fresh input, output, cache reads and
writes — even on a subscription transport whose own `cost` is always 0.0. One source per model,
chosen by its route: a metered call is priced by its provider — the bill it REPORTS on the call
(OpenRouter's `cost`; an Otari gateway's `cost_usd` on either wire, docs/model-gateway.md), else
its own published rates for that exact id (OpenRouter, Nano-GPT); a subscription proxy or gateway
(every endpoint here that is neither OpenRouter nor Nano-GPT) at the model's list price in the
public reference catalog — a gateway's `<name>-metered` id under `<name>`'s, since it is the paid
route of the same model. A reported zero (the gateway's figure for a subscription call) is that
call's true bill and stays its `cost`, but it is no price, so the list price is its notional. There is no price to configure and no fallback table: a
model no source prices is listed as `unpriced` and its calls carry no figure. The cache records
which models it CHECKED, and a configured model missing from that list makes the limits cache
stale (`prices.missing`), so a new model — or a deploy that brings the price cache — is priced at
the next tick rather than at the next daily expiry. docs/run-analytics.md ("What a run costs") is
where the figure is read.

That figure is also what a **cost BUDGET** meters: `budgets.max_cost` is checked against
`endpoints/usage.dollars_spent` — `notional` first, a provider's reported `cost` only as the
fallback (F758). This matters when you point `system_model` at a metered `anthropic` endpoint:
until F758 the ceiling read `usage["cost"]`, which NO `anthropic` adapter then wrote, so a cap set on
such a route bounded nothing while reading as a real one. It now binds — in LIST-PRICE dollars on
a subscription route (not an invoice: a run there can trip a dollar cap having been billed
nothing) and in real spend on a metered one, where the provider's own reported figure IS the
notional one. A model the refresh lists as `unpriced` still carries no figure, so a cap on it
meters 0.0 and cannot trip: a ceiling cannot be enforced against a price nobody publishes.

## Windows and output caps are DISCOVERED — leave them blank

You do not size a model's context window by hand. At boot and every 24 hours the daemon asks each
configured provider what its models' real limits are and caches the answer under
`<routines>/.control/model-limits.json` (derived state, never config — `endpoints/limits.py`).
Resolution walks one chain:

    per-MODEL config  →  what the PROVIDER reports  →  the endpoint default  →  the engine floor

A per-model value is you sizing THIS model down on purpose — a cost budget, a slow provider — and
it still wins. An ENDPOINT value sits below discovery because that is all it has ever been: a
default a model inherits when it says nothing. Blank is the right state for both, and the Settings
card says where each effective figure came from ("from openrouter", "from the built-in table")
rather than showing an empty box.

What can be discovered, per endpoint:

| endpoint | window | output cap |
|---|---|---|
| behind a **gateway** (`gateway:` set) | the catalog of the ROUTE the name resolves to (below) | the same |
| `openai` @ OpenRouter | `GET /models` → `top_provider.context_length` (the row's own when absent) | `top_provider.max_completion_tokens` |
| `openai` @ Nano-GPT | its detailed listing, `/api/v1/models?detailed=true` | `max_output_tokens` there |
| `openai`, other | `max_model_len` / `context_length` if the gateway emits one (vLLM does) | none published |
| `anthropic` @ Anthropic | a built-in Claude table (see below) | none published |
| `anthropic` @ a proxy, no gateway | whatever its `/v1/models` carries | the same |

**Both knobs are the route's published maximum** (operator, 2026-11-20: *"not below"*). The window
is adopted verbatim and so is the output maximum the route publishes — 128,000 on a Claude or
Codex subscription route, 65,536 on Gemini's. The engine subtracts `max_tokens` from the input
budget (`compaction.window_ceiling_tokens`), so a large reservation means a run compacts earlier,
never that a request overflows. A row that publishes a window and no maximum claims no output
figure at all: the model takes the endpoint's `max_tokens`, then the engine default, and the card
flags it.

**One guard: a published figure that is not a maximum.** OpenRouter fills
`max_completion_tokens` with ⌊0.9 × window⌋ for models whose real output limit it does not carry
(`z-ai/glm-5.3` and `google/gemini-4-argon` both read 943,718 on a 1,048,576 window, where
Nano-GPT publishes 131,072 and Google 65,536), and a few rows restate the window itself. Providers
validate `input + requested_output <= window`, so asking for either leaves the prompt nothing.
Such a figure (`routecatalogs.is_placeholder`) is replaced by the real maximum the other catalogs
agree on under the name vendors share — Nano-GPT's listing and every CLIProxyAPI plan — and is
left unknown when they disagree or say nothing. The card keeps what was published beside it.

**A gateway name is sized by its ROUTE, never by the model's public figure.** An Otari gateway
serves normalised names and its `aliases:` send each to a provider; the figures belong to that
route. The Codex Plus login serves `gpt-5.6-luna` at 372,000 tokens where the API (and the public
OpenRouter catalog, which sized it until 0.564.0) says 1,050,000; the Claude subscription serves
`claude-sonnet-4-6` at 200,000 where the API serves 1,000,000. So an endpoint names its gateway,
and the gateway says where its routing lives and which subscription plan each login holds:

```yaml
gateways:
  otari:
    routes_file: /srv/llm-interfaces/config.yml   # Otari's own config: aliases + providers
    plans:                                        # CLIProxyAPI logins → their catalog plan groups
      cliproxy: [claude, antigravity]
      chatgpt: [codex-plus]
endpoints:
  otari:
    kind: anthropic
    base_url: http://172.17.0.1:4000/api
    gateway: otari
```

At each refresh the routing file is read (`endpoints/routes.py`) and every name resolves to
`<provider>:<upstream id>` — an alias, or a name that is already a selector
(`openrouter:z-ai/glm-5.3`). A routing POLICY name resolves to no single route and stays unknown.
The provider then names the catalog (`endpoints/routecatalogs.py`), each read once per refresh:

- a provider with `plans` is a CLIProxyAPI login → **CLIProxyAPI's model catalog**
  (`router-for-me/models`), the plan groups listed. The plan is CONFIG because nothing on the
  wire says which plan a login holds; two plans listing the id with different figures leave it
  unknown. A level pin (`gpt-6.2-sol(max)`) is dropped before the lookup.
- `openrouter` (Otari's built-in, or an `api_base` on openrouter.ai) → OpenRouter's public
  `/models` by EXACT id, sized by `top_provider` — the same listing the prices are read from.
- an `api_base` on nano-gpt.com → Nano-GPT's detailed listing by exact id.
- `anthropic` (Otari's built-in) → Anthropic's own API: the built-in table's window, no output
  figure (its listing needs the key only the gateway holds).
- anything else (a local model server) publishes nothing readable: unknown.

The file is read rather than Otari's alias API because that API needs Otari's MASTER key, which
no credential on this side should hold; the file is the routing as written (an alias edited on
Otari's dashboard alone is not seen — keep the file the source). Editing it makes the cache
stale at once, so a moved alias is re-sized at the next tick rather than a day later. The card
says `from the subscription plan` / `from openrouter` / `from nano-gpt`, and every row records
its `route` (`chatgpt:gpt-6.2-sol(max) [codex-plus]`).

The built-in Claude table answers for **Anthropic's own API** alone — a direct `kind: anthropic`
endpoint, or a gateway's `anthropic` route. Never for a proxy: a subscription may serve the same
id on a smaller window. Anthropic's own listing has carried `max_input_tokens`, `max_tokens` and
a capability tree since March 2026; **read it the day a direct endpoint is configured** — until
then a Claude id the table does not name stays unknown. A table key covers the ids that start
with it: a dated snapshot, and a later point revision of a 5-series family (`claude-opus-5-5`
reads `claude-opus-5`'s window, which every such revision has kept). A brand-new family, or a
4-series revision, needs its own row. Retired models carry none.

A miss is not a failure: the model drops to the next tier, and the Settings card says WHICH miss
it is. Those are two different states and only one is fixable by discovery:

- **the provider does not list that id** — usually a stale catalog entry or a typo, worth fixing.
- **the provider lists the id but publishes no figures for it** — the state of an endpoint whose
  catalog is ids only (a proxy with no `gateway:`, or a gateway route its catalog does not list).
  Only a route the scheduler can read, or a hand-set value, settles it, and the card says so
  instead of sending you after a typo that is not there.

When the endpoint publishes no catalog at all, the question is unanswered and the card keeps the
undecided wording — an unreachable provider is never reported as "does not serve this model".

**Claude subscription** — use `kind: anthropic`, the gateway's (or proxy's) root base URL, and
its client key. CLIProxyAPI owns OAuth login, token refresh, and account routing; here it sits
behind an Otari gateway that routes normalised model names to it. See the
[model gateway guide](model-gateway.md).

### Subscription logins, quota and sign-in are NOT here

A subscription's accounts, how much of its window is left, and signing one back in belong to the
gateway that owns the logins, and are managed on CLIProxyAPI's own panel
(`/management.html`) — this product configures the TRANSPORT and nothing else (0.606.0, the
operator's call: "we don't need any mention of subscriptions in routine-scheduler"). The
scheduler once mirrored all three, plus a 10-minute watch that filed a blocked-work row when a
login died; the cost of dropping it is named in [contracts](contracts.md) and
[architecture](architecture.md). For a pay-per-use key the Settings card still reads the
provider's own BALANCE (OpenRouter, Nano-GPT), which is a credits read, not a subscription.

## Abliterated GLM 5.2 (uncensored community variants)

Status as of 2026-07: the abliterations of `zai-org/GLM-5.2` exist as Hugging Face
weights — `huihui-ai/Huihui-GLM-5.2-abliterated-GGUF` (GGUF/llama.cpp quants, the
"IQ2-class" files people quote), `zandenAI/GLM-5.2-FP8-Uncensored` and
`Bahushruth/GLM-5.2-FP8-abliterated` (safetensors, gated) — but **no inference provider
serves any of them turnkey yet** (GGUF isn't servable by safetensors providers; the
safetensors variants are gated, which blocks auto-onboarding).

The configured **Featherless** endpoint is the closest cloud path:

- Works **today**: `huihui-ai/GLM-4-32B-0414-abliterated` — a genuine abliteration of the
  previous GLM generation, confirmed live on Featherless, fits the base plan.
- GLM **5.2** abliterated: needs the top Featherless tier (750B-class) *and* the variant
  onboarded — request it via their Discord `#model-suggestions`, or connect an HF account
  that has accepted the repo's gate. Once listed, just use its repo id as the model id —
  the endpoint config doesn't change.
- Guaranteed alternative: rent GPUs and self-host `zandenAI/GLM-5.2-FP8-Uncensored` on
  vLLM (~4 large GPUs for the FP8), then add it as a vLLM recipe above.

Add the finished model to the catalog (its `endpoint` + provider `model` id) and set it as a
routine's `main` model (or the system model) like any other — abliterated models are ordinary
models to the scheduler.

**Nano-GPT** (`kind: openai`) is the turnkey cloud path today: it serves abliterated models
directly (e.g. `huihui-ai/DeepSeek-R1-Distill-Llama-70B-abliterated`), so no self-hosting.
Use **`schema_mode: none`**, not `json_schema`: these run on community GPU backends
that can't do strict schema-constrained decoding and reject a `json_schema` `response_format`
with a generic `503 service_unavailable` (which looks like an outage, not a schema problem).
The adapter self-heals a stray 503 by retrying once without the schema, but `none` avoids
that wasted probe every turn.

**A 402 that prices out `max_tokens` degrades instead of killing the turn** (F362). A
credit-metered provider (OpenRouter) answers `402` when the REQUESTED output ceiling costs
more than the balance left, and the message names the number that fits — *"You requested up
to 16384 tokens, but can only afford 9590"*. That is NOT an empty account: it recurs as any
balance drains, so topping up only postpones it. The adapter retries once at the stated
ceiling, which turns a run-killing failure into a shorter completion (the squeeze is logged).
Two deliberate exceptions: an affordable ceiling too small to carry an action (below ~600
tokens) is not retried, so failover takes the turn instead of a stub reply; and a 402 naming
no number is left alone, because there is nothing to degrade to and inventing one would mask
a genuinely empty balance. The durable fix for a routine that keeps hitting it is a lower
per-model `max_tokens` in the catalog.

```yaml
endpoints:
  NanoGPT:
    kind: openai
    base_url: https://nano-gpt.com/api/v1
    key_var: NANO_GPT_API_KEY     # value goes in Settings → Secrets
    schema_mode: none
    context_tokens: 100000
```

## Refusal takeover (the `uncensored` role) - with exponential back-off

A routine may configure a third model role, **`uncensored`**, alongside main / tool_call.
It is a **working model, not a honeypot** (operator, 2026-10-16 - supersedes the
2026-08-22 honeypot premise): when a model refuses a request, the uncensored model
TAKES THE REQUEST OVER - the whole prompt/turn, its context intact - and its reply IS
the answer / the turn's action. Nothing is excised, nothing is discarded: the point of
the role is that all kinds of requests can be processed.

**The back-off** (operator, 2026-10-16): the uncensored model serves an episode of
consecutive refusals with a window that doubles each time the regular model refuses
again - 1 -> 2 -> 4 -> 8 -> 16 turns (engine/refusal.py TAKEOVER_CAP; deeper episodes
hold the window at 16). The refused request itself is the window's first turn. When the
window closes, the next request is handed BACK to the routine's regular model; a turn
the regular model completes cleanly RESETS the episode (the next refusal starts at
window 1 again). A refusal BY the uncensored model is an honest dead end - recorded,
never a back-off rung - so the two models never ping-pong a refusal between them.

When a model refuses, the engine runs the **refusal-takeover process**
(engine/refusal.py + engine/turntakeover.py) at the agent turn loop (children run the same
loop and are covered identically). Until D288 there was a second seam — the `llm` action
re-issued a refused SUB-CALL — and it went with that kind: the back-off episode below is
RunContext state the turn loop owns, so a util buying inference cannot join it, and a
util-side retry would record no `refusal` event, tick no window and count no referral. A
routine that wants refused work done on a different model names that model in the call
itself, which is a choice rather than an escalation. The one surviving seam:

1. **Detect - reliably, not by a marker list.** A provider classifier stop
   (`stop_reason: "refusal"` / `"content_filter"`, see *Failover & cooldowns* above) is
   authoritative. A free-text decline is judged by an LLM classification subcall on the
   tool_call model (schema'd verdict); the legacy marker list survives only as a
   zero-cost fast path that can CONFIRM an obvious opener - it never decides "not a
   refusal", and a marker counts only inside the reply's OPENING SENTENCE (operator,
   2026-10-01). "Here's the summary; I can't provide the 2025 figures" opens with an
   answer, so the marker decides nothing there: a free-text reply goes on to the
   classifier, and an `ok`/`partial` finish - which the fast path judges alone - stands
   as the reply it is.
2. **Flag.** A first-class `refusal` transcript event records the seam, the refusing
   model, the refusal message, and the takeover verdict (takeover true/false, the
   episode level, the window, the takeover model) - what the transcript UI renders.
3. **Take over.** The refused TURN is re-driven WHOLE on the uncensored model, and its
   output IS the turn's action. A takeover window opens (the back-off above); when it
   closes, the regular model is handed the next request. When NO takeover can serve - no
   `models.uncensored` configured, or the uncensored model itself refused - the refusal
   stands and the normal path applies: the turn takes the ordinary fallback-chain path (a
   classifier refusal still cools the refusing model and advances `fallbacks:`; with no
   usable fallback the run fails honestly, naming the category).

- **Opt-in takeover, always-on flagging.** Without `models.uncensored` the engine still
  detects and flags - only the takeover is skipped (the event says so). The role has no
  system-model fallback. Since R3680 a routine CREATED through `workflows.scaffold` is born
  with the role filled from this instance's own standard (`workflows/uncensored_default.py`),
  so "unset" now means someone cleared it or the instance names no standard - not that nobody
  got round to it. The resolution rule itself is unchanged: unset is still referral off.
- **Only free-text replies are considered** - a schema-constrained (`response_schema`)
  reply is an answer by construction. An explicit `model: uncensored` call is the
  caller's own probe and is never taken over.
- **Audit.** `ctx.referrals` (status.json + the durable spend stream) counts requests
  actually served by the uncensored model; the episode state (`refusal_level`,
  `takeover_left`) rides in status.json and survives a resumed leg. Each incident's
  full record is its `refusal` transcript event.

The operator seam stays the person's own: a conversation reply flagged as a refusal
(discarded, everything after it archived, and the conversation's uncensored model made
its MAIN model from then on) - see the **Conversations** guide.

## Troubleshooting

- **✗ auth problem** on test → wrong/missing key. Check the endpoint's card: inline key
  set? `key_var` present in Secrets (Settings → Secrets)?
- **schema VIOLATED** on test → the model can't hold the JSON contract in that mode. Try
  `json_schema` or a stronger model. Weak models + `none` still work — the engine repairs and
  retries — but burn turns.
- **✗ 503 service_unavailable** on test, yet the model *is* in the provider's model list →
  its backend can't honor the requested `response_format`. Set `schema_mode: none`
  (common for NanoGPT abliterated/community models). The adapter also retries once without
  the schema on such a 503, but `none` avoids that extra probe every turn.
- **Truncated / empty answers from reasoning models** → the model spent its output budget
  thinking. The engine already maps effort to the provider's reasoning knob; pick a lower
  effort for that model role (the routine page's effort control), or a non-reasoning model.
- **A run failed with "refused the turn (… category=…)"** → the provider's safety
  classifier declined the prompt (an HTTP 200, not an outage — see *Failover & cooldowns*).
  Give that catalog model a `fallbacks:` entry pointing at a model without that classifier
  (or configure the routine's `uncensored` role); the transcript's `error` event carries the
  category and explanation.
- **A provider mangles structured output** (dropped fields, foreign keys) → exclude it via
  `extra_body.provider.ignore` (OpenRouter) and keep `allow_fallbacks: true`.
