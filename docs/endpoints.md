# LLM endpoint setup

An **endpoint** is a model *transport*: one place the scheduler can send a chat completion
and get an answer back. Endpoints never act on their own — the scheduler's engine is the
only agent loop. A **model** is a named entry in the *catalog* that binds a provider model id
to an endpoint and carries the per-model attributes — multimodality, context window, effort,
temperature. One endpoint serves many models, so those attributes live on the model, not the
endpoint. You configure endpoints and models once (Settings → Endpoints, or
`~/.config/routine-scheduler/config.yaml`), then every routine and the system model **picks a
model by name**.

## The three kinds

| kind | what it talks to | credential | billing |
|---|---|---|---|
| `openai` | any OpenAI-compatible chat API: OpenRouter, Featherless, vLLM, Ollama, Together, … | API key (or none for local Ollama) | per provider (metered or subscription) |
| `anthropic` | Anthropic's Messages API | `sk-ant-…` API key | **metered**, per token |
| `claude-cli` | the Claude Code CLI in fully stripped print mode | `CLAUDE_CODE_OAUTH_TOKEN` from `claude setup-token` | your Claude **subscription** — no per-token billing |

Nine times out of ten you want `openai`: one kind covers every provider that speaks the
OpenAI chat-completions dialect, cloud or local.

## Adding an endpoint (web UI)

1. **Settings → Endpoints → + add endpoint.** Name it (the name is its identity — routines
   reference it), pick the kind, set the base URL (e.g. `https://openrouter.ai/api/v1`).
2. **Give it a credential.** Either paste an API key on the endpoint's card (stored inline
   in the server config), or set `key_var` to a name like `OPENROUTER_API_KEY` and put the
   value in **Settings → Secrets** — the central store. Secrets win for anything you might
   rotate; inline wins for quick starts. `claude-cli` reads `CLAUDE_CODE_OAUTH_TOKEN` from
   Secrets — paste the token the card asks for. The card's **credential in use** line shows
   which rung of the ladder is actually live (inline / secret / env file / none — labels
   only, values are never returned) and warns when an inline key **shadows** a set secret:
   the inline key wins, so editing the secret changes nothing until the inline key is
   removed.
3. **Test it.** Enter a model id on the card and hit *test* — you get latency, whether the
   model respected a JSON schema, and the raw error (with an auth hint) if the call failed.
   Fix problems here, not mid-run.
4. **Add the models it serves.** In the endpoint list's **Models** section → *+ add model*.
   Name it (the name is what routines reference — e.g. `gpt-4o`, `glm`, `opus`), pick the
   endpoint, enter the provider's model id, and set its attributes: **multimodal** (default by
   the endpoint kind), **context window**, **effort**, **temperature**. One endpoint can serve
   many models with different windows and vision support — add one catalog entry per model.
5. **Point roles at models.** Set the server-wide **system model** (used only for setup-time
   work: routine creation and workflow generation) by picking a catalog model, and per
   routine the model roles — **main** (the orchestrator loop; spawned children run it by
   default, and a call may override per child), **tool_call** (the `llm` action), and the
   optional **uncensored** (the refusal-clarification harness — see below) — on the
   routine's page, each a catalog model name.
   main/tool_call fall back to the system model when left unset; **uncensored has no
   fallback** — leave it unset and refusals are still flagged + isolated, but no fragment
   is referred. See *Refusal clarification* below.

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
    context_chars: 400000         # a DEFAULT models on this endpoint inherit

models:                           # the catalog: each entry binds a model id to an endpoint
  glm:
    endpoint: OpenRouter
    model: z-ai/glm-5.2           # text-only → inherits openai's multimodal default (off)
  gpt-4o:
    endpoint: OpenRouter
    model: openai/gpt-4o
    multimodal: true              # this model sees images/PDFs; glm above doesn't
    context_chars: 512000         # overrides the endpoint default for this model

system_model: glm                 # the fallback model for setup-time work — a catalog NAME
```

### Endpoint fields (the transport)

- `base_url` — everything before `/chat/completions`. Local Ollama:
  `http://127.0.0.1:11434/v1`. Self-hosted vLLM: `http://host:8000/v1`.
- `api_key` / `key_var` / `key_env_file` — credential lookup order: inline `api_key`
  first, then `key_var` in the Secrets store, then `key_var` inside `key_env_file`
  (a `~/.credentials/*.env` style file). `key_var` defaults per kind — `OPENAI_API_KEY`
  for `openai`, `ANTHROPIC_API_KEY` for `anthropic` — set it explicitly for an aggregator
  (e.g. `OPENROUTER_API_KEY`). `claude-cli` ignores it (subscription token instead).
- `schema_mode` — how the endpoint enforces the one-JSON-action-per-turn contract:
  - `json_schema` (default): strict `response_format` — OpenRouter, OpenAI, Ollama ≥ 0.5.
    Providers that reject it — with a 400, or a generic 503 that hides a schema-incapable
    backend — get one degraded retry without it, so it is safe to leave on.
  - `json_object`: weaker "any JSON" mode; the scheduler's validator does the rest.
  - `ollama_native`: Ollama's own `format` field — REAL constrained decoding; best for
    small local models that otherwise drift off-schema.
  - `none`: nothing requested; the code-level validate-and-retry loop does all the work.
- `context_chars` — a **default** prompt-size window (in characters, ≈ 4 × tokens) that catalog
  models on this endpoint inherit when they don't set their own. **Default `100_000`** (≈25k
  tokens — deliberately small). Prefer setting the real window per model (below).
- `temperature` — optional **default** temperature catalog models inherit when unset.
- `credentials_env` — `claude-cli` only: the file the OAuth token is read from when it isn't
  in Secrets (default `~/.credentials/claude-code-oauth.env`).
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
fields*: `temperature`, `key_env_file`, and — per kind — the `claude-cli` `credentials_env` or the
`openai` `extra_body` as JSON). A save that omits a field the form doesn't show preserves the
stored value rather than clearing it.

### Model fields (the catalog)

A catalog model binds a provider `model` id to an `endpoint` and carries the attributes that
vary *per model*. Leave an attribute unset (or blank in the UI) to inherit the endpoint's
default. Routines and the system model reference a model by its catalog **name**.

- `endpoint` — the configured endpoint that transports this model (required).
- `model` — the provider's model id (required), e.g. `openai/gpt-4o`, `z-ai/glm-5.2`.
- `multimodal` — whether this model takes image/PDF input natively. **Default by the endpoint
  kind**: on for `anthropic` (images + PDFs) and `claude-cli` (images), off for `openai`. Set
  it explicitly to turn native vision *on* for an `openai` vision model (GPT-4o, Gemini) or
  *off* for a text-only one. When off, images/PDFs a routine views route to the `vision` util
  instead — vision still works, just indirectly.
- `context_chars` — the prompt size (≈ 4 × tokens) at which the engine compacts run history to
  disk, for THIS model. Inherits the endpoint's `context_chars` when unset. Different models on
  one endpoint have very different windows — set the real one here.
- `effort` — a reasoning-effort hint: `low | medium | high | xhigh | max`. Each kind maps it to
  its own reasoning knob (`openai` collapses `xhigh` / `max` → `high`); lower it if a reasoning
  model spends its whole output budget thinking instead of answering.
- `temperature` — sampling temperature; inherits the endpoint's when unset (`openai` and
  `anthropic` apply it, `claude-cli` ignores it).
- `max_tokens` — the model's real **output** limit per completion, sent on every engine call
  (turns, `llm` actions; `claude-cli` maps it to `CLAUDE_CODE_MAX_OUTPUT_TOKENS`). Inherits the
  endpoint's `max_tokens` when unset; with neither set, a generous engine default (16,384)
  applies and Settings flags the model with a **⚠ max_tokens** chip — implausible values
  (below 4,096, or larger than the context window) are flagged too, so "every model set
  correctly" is auditable at a glance.
- `fallbacks` — the ordered **failover chain**: catalog model names tried in order when this
  model fails hard. See *Failover & cooldowns* below.

### Failover & cooldowns (`fallbacks`)

A provider outage at cron-fire time used to kill the run after the transport's own retries
(3 tries, exponential backoff). With `fallbacks: [other-model, …]` on a catalog model, the
engine instead **fails over**:

- **Mid-turn**: when the serving model fails hard (retries exhausted, or a non-retryable
  error such as a dead host or bad key), the turn is re-issued to the next chain member —
  with *its* endpoint, effort, temperature, and max_tokens. The switch is logged visibly as
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
  later resolution in the same run process (main turns, `llm` actions, compaction, spawned
  children) skips it for the first not-cooling chain member instead of hammering a flapping
  provider. When every chain member is cooling, the primary is used anyway (a run never
  stalls on bookkeeping). Cooldowns are process-local: a fresh run probes the primary once
  and re-marks it if the outage persists.
- Chains are **not transitive**: only the named model's own `fallbacks` list is tried, in
  order. Self-references, duplicates, and unknown names are reported as config problems and
  skipped. Roles without fallbacks behave exactly as before — the feature is opt-in per
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

Every adapter uses prompt caching, and it needs no setup. Cache traffic is reported separately in
usage — `cached_in` (the ~0.1× re-reads) and `cache_write` — and kept out of the `in` count, so
token budgets keep their meaning. It matters most for the two kinds that cost real money:
**anthropic** sets cache breakpoints every turn, so the growing prefix re-reads at ~0.1×; and
**claude-cli** keeps one CLI session per run and sends only the new turn each time, so prior turns
serve from cache instead of re-charging the whole transcript against your subscription quota.

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

**Ollama** (local, free) — `kind: openai`, `base_url: http://127.0.0.1:11434/v1`, no key,
`schema_mode: ollama_native`. Mind `context_chars`: small local models often run with
small windows.

**Self-hosted vLLM** (any HF model on your own GPUs, incl. rented ones — Runpod
serverless exposes `https://api.runpod.ai/v2/<endpoint-id>/openai/v1`) — `kind: openai`,
base URL of the server, whatever key you configured it with. This is the guaranteed path
for a model no provider lists.

**Anthropic API** — `kind: anthropic`, no base_url needed, `sk-ant-…` key. Metered: know
your budget caps.

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

What can be discovered, per kind:

| endpoint | window | output cap |
|---|---|---|
| `openai` @ OpenRouter | `GET /models` → `context_length` | `top_provider.max_completion_tokens` |
| `openai` @ Nano-GPT | its own `/api/models` (the OpenAI-compatible route carries none) | same |
| `openai` @ Ollama | `POST /api/show` → the arch's `context_length` | none — derived from the window |
| `openai`, other | `max_model_len` / `context_length` if the gateway emits one (vLLM does) | rarely |
| `anthropic`, `claude-cli` | a built-in table — neither kind has a metadata API | the table |

**The output cap is deliberately NOT maxed out.** Providers validate
`input + requested_output <= window` up front, and the engine subtracts `max_tokens` from the
input budget for the same reason, so adopting a model's full output limit would starve the prompt:
one live model reports a 943,718-token output maximum against a 1,310,720-token window. The
discovered cap is `min(provider maximum, 32,000)` — a ceiling on what this harness needs for one
JSON action plus reasoning, not a claim about the model.

A miss is not a failure: the model drops to the next tier and the Settings card says the id is one
its provider does not list — which is usually a stale catalog entry worth fixing.

**Claude subscription** — `kind: claude-cli`, no base_url or api key. Run
`claude setup-token` on any machine, paste the resulting token on the endpoint's card
(it lands in Secrets as `CLAUDE_CODE_OAUTH_TOKEN`). Metered-auth environment variables
are scrubbed from the CLI's environment, so it can never silently fall back to API billing.

### Seeing what is left of the subscription

The endpoint card and the Routines page both show the account's remaining quota — "5h 61% left
(resets in 2h10m) · 7d 32% left" — read from Anthropic's own usage API, the same source
claude.ai's usage panel and the CLI statusline use. It is UNDOCUMENTED, so every failure is soft:
the chip shows what went wrong and nothing else depends on it. (Until 0.295.0 the card instead
showed a LOCAL tally of tokens this instance had burned. That could not answer "% remaining" even
in principle — the windows are not a token count, and the tally was blind to your own interactive
sessions and to claude.ai on the same subscription — so it was deleted rather than patched.)

**It needs a SECOND token.** The usage API requires the `user:profile` scope, which the headless
`claude setup-token` does not carry (it is inference-only and 403s). An interactive login mints a
full-scope one:

```
docker exec -it -u 1000:1000 rsched claude /login
```

That writes `~/.claude/.credentials.json` inside the container, bind-mounted from
`~/.claude-daemon` on the host so it survives a recreate (a dedicated dir, deliberately not the
host's own `~/.claude` — sharing that would put the daemon and your own Claude Code in one
session store). Nothing refreshes it headlessly, so it eventually expires; the card reads the
expiry stamp and says so, with this command, rather than waiting to fail with an
authentication error. The inference path is untouched by all of this — it keeps using the
setup-token from Secrets.

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
Use **`schema_mode: json_object`**, not `json_schema`: these run on community GPU backends
that can't do strict schema-constrained decoding and reject a `json_schema` `response_format`
with a generic `503 service_unavailable` (which looks like an outage, not a schema problem).
The adapter self-heals a stray 503 by retrying once without the schema, but `json_object`
avoids that wasted probe every turn.

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
    schema_mode: json_object
    context_chars: 400000
```

## Refusal clarification (the `uncensored` harness role)

A routine may configure a third model role, **`uncensored`**, alongside main / tool_call.
It is a **honeypot harness, not an answer machine** (operator, 2026-08-22): it only *acts
as if* it complies, so the catching machinery below can be exercised and evaluated BEFORE
any actually-uncensored model is ever in the loop. Nothing it produces is executed,
returned as an answer, or allowed to become a turn's action — the earlier behaviour
(re-issue the whole refused prompt/turn to it and use the reply) is retired.

When a model refuses, the engine runs the **refusal-clarification process**
(`engine/refusal.py`), at both seams — the `llm` action and the agent turn loop
(children run the same loop and are covered identically):

1. **Detect — reliably, not by a marker list.** A provider classifier stop
   (`stop_reason: "refusal"` / `"content_filter"`, see *Failover & cooldowns* above) is
   authoritative. A free-text decline is judged by an LLM classification subcall on the
   tool_call model (schema'd verdict); the legacy marker list survives only as a zero-cost
   fast path that can CONFIRM an obvious opener — it never decides "not a refusal".
2. **Flag.** A first-class `refusal` transcript event records the seam, the refusing
   model, and the refusal message — the explicit signal the clarification hangs off, and
   what the transcript UI renders.
3. **Isolate.** One schema'd subcall (tool_call model) decomposes the refused task and
   names the MINIMAL fragment that plausibly triggered the refusal — one STEP of its
   action sequence, or a WORD/PHRASE recurring through it.
4. **Deliver the essence.** ONLY the isolated essence of the refusal trigger is sent to
   the uncensored model — as a completely NORMAL model call, no special framing, no test
   markers (the environment must be authentic; the operator manages the dummy responses
   in the background). Everything ELSE is processed by the MAIN model, now without
   refusal danger: the `llm` seam re-issues the prompt with the essence factored out and
   that answer serves the observation; a loop turn's retry message says the flagged
   essence is handled separately, proceed with the rest. The harness reply lands in the
   record as `harness_reply`; isolation failing means nothing is sent — more than the
   essence never reaches the honeypot.

The refused call then continues on its NORMAL path: an `llm` action returns the original
refusal as its reply with the clarification record beside it (`refusal` on the
observation); a loop turn takes the ordinary schema-retry / fallback-chain path (a
classifier refusal still cools the refusing model and advances `fallbacks:`; with no
usable fallback the run fails honestly, naming the category).

- **Opt-in referral, always-on flagging.** Without `models.uncensored` the engine still
  detects, flags and isolates — only the harness delivery is skipped (the event says so).
  The role has no system-model fallback.
- **Only free-text replies are considered** — a schema-constrained (`response_schema`)
  reply is an answer by construction. An explicit `model: uncensored` call is the caller's
  own harness probe and is never re-clarified.
- **Audit.** `ctx.referrals` (status.json + the durable spend stream) counts harness
  deliveries; each incident's full record is its `refusal` transcript event.

## Troubleshooting

- **✗ auth problem** on test → wrong/missing key. Check the endpoint's card: inline key
  set? `key_var` present in Secrets (Settings → Secrets)?
- **schema VIOLATED** on test → the model can't hold the JSON contract in that mode. Try
  `ollama_native` (Ollama), `json_object`, or a stronger model. Weak models + `none`
  still work — the engine repairs and retries — but burn turns.
- **✗ 503 service_unavailable** on test, yet the model *is* in the provider's model list →
  its backend can't honor the requested `response_format`. Set `schema_mode: json_object`
  (common for NanoGPT abliterated/community models). The adapter also retries once without
  the schema on such a 503, but `json_object` avoids that extra probe every turn.
- **Truncated / empty answers from reasoning models** → the model spent its output budget
  thinking. The engine already maps effort to the provider's reasoning knob; pick a lower
  effort for that model role, or a non-reasoning model.
- **A run failed with "refused the turn (… category=…)"** → the provider's safety
  classifier declined the prompt (an HTTP 200, not an outage — see *Failover & cooldowns*).
  Give that catalog model a `fallbacks:` entry pointing at a model without that classifier
  (or configure the routine's `uncensored` role); the transcript's `error` event carries the
  category and explanation.
- **A provider mangles structured output** (dropped fields, foreign keys) → exclude it via
  `extra_body.provider.ignore` (OpenRouter) and keep `allow_fallbacks: true`.
