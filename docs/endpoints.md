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
   Secrets — paste the token the card asks for.
3. **Test it.** Enter a model id on the card and hit *test* — you get latency, whether the
   model respected a JSON schema, and the raw error (with an auth hint) if the call failed.
   Fix problems here, not mid-run.
4. **Add the models it serves.** In the endpoint list's **Models** section → *+ add model*.
   Name it (the name is what routines reference — e.g. `gpt-4o`, `glm`, `opus`), pick the
   endpoint, enter the provider's model id, and set its attributes: **multimodal** (default by
   the endpoint kind), **context window**, **effort**, **temperature**. One endpoint can serve
   many models with different windows and vision support — add one catalog entry per model.
5. **Point roles at models.** Set the server-wide **system model** (used only for setup-time
   work: the new-routine wizard and workflow generation) by picking a catalog model, and per
   routine the model roles — **main** (the orchestrator loop), **subroutine** (spawned
   children), **tool_call** (the `llm` action), and the optional **uncensored** (a refused
   `llm` tool-call is re-referred here) — on the routine's page, each a catalog model name.
   main/subroutine/tool_call fall back to the system model when left unset; **uncensored has no
   fallback** — leave it unset and the routine never refers. See *Refusal referral* below.

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
  (a `~/.credentials/*.env` style file). `key_var` defaults to `ANTHROPIC_API_KEY` — set it
  explicitly (e.g. `OPENROUTER_API_KEY`) for an `openai` endpoint.
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

**Claude subscription** — `kind: claude-cli`, no base_url or api key. Run
`claude setup-token` on any machine, paste the resulting token on the endpoint's card
(it lands in Secrets as `CLAUDE_CODE_OAUTH_TOKEN`). Metered-auth environment variables
are scrubbed from the CLI's environment, so it can never silently fall back to API billing.

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

```yaml
endpoints:
  NanoGPT:
    kind: openai
    base_url: https://nano-gpt.com/api/v1
    key_var: NANO_GPT_API_KEY     # value goes in Settings → Secrets
    schema_mode: json_object
    context_chars: 400000
```

## Refusal referral (the `uncensored` model role)

A routine may configure a fourth model role, **`uncensored`**, alongside main / subroutine /
tool_call. When the routine's **tool_call** model (the `llm` action) replies with a *content
refusal* (it declines the request in free text — "I can't help with that…"), the engine
re-issues the **same** prompt to the routine's `uncensored` model and returns that answer
instead, with `referred: true` on the observation.

- **Opt-in and inert by default.** Referral fires ONLY when `models.uncensored` is set — and
  that role has no system-model fallback, so leaving it blank means "never refer". Every
  routine that doesn't configure it behaves exactly as before.
- **Only free-text tool-call replies are considered** — a schema-constrained (`response_schema`)
  reply is an answer, not a refusal, and is never rerouted. The refusal detector is
  deliberately conservative (matches a decline only at the head of the reply) to avoid
  rerouting genuine answers.
- **Typical wiring:** point `uncensored` at a Nano-GPT abliterated model (above), keep
  `tool_call` on your normal model. Requests the normal model refuses get answered by the
  abliterated one; everything else stays on the normal model.
- **Scope: the `llm` tool-call AND the agent loops** (main orchestrator + subroutine). In a
  loop, a turn is a schema-constrained *action*, so a refusal shows up as a free-text reply
  that fails to parse as an action **and** reads as a decline: when that happens and an
  `uncensored` model is configured, the engine re-issues the same turn to it once and, if it
  produces a valid action, continues the run with it (the `assistant_action` transcript event
  carries `referred: true`). A malformed-but-not-refusing reply still takes the normal
  schema-retry path — the uncensored model is only consulted on a genuine decline. Subroutines
  run the same loop, so they are covered by the same mechanism.

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
- **A provider mangles structured output** (dropped fields, foreign keys) → exclude it via
  `extra_body.provider.ignore` (OpenRouter) and keep `allow_fallbacks: true`.
