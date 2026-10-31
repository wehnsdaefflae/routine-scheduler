# Decision models and the `decide` action

A **decision model** answers a typed question with **probabilities over the answers it was
offered** — never text. Give it some evidence and a question; it returns P(yes) for a yes/no
question, one probability per option for a choice, or a probability-weighted position on an
ordered scale for a score. TypeSafe calls the family *System One* and its model **Jev**; OpenAI's
**Decisions API** (`gpt-6-luna`) is the same idea; SemIf/OpenJev reads the same answer out of an
open model's logits, and that is what [local-model-server](https://github.com/wehnsdaefflae/local-model-server)
runs on predator.

Most decisions a routine makes are small — route this, screen that, is this evidence enough. A
chat model answers them by writing a sentence the run then parses back into an `if`. A decision
model *is* the `if`: there is no reply text, so there is no empty completion that looks like
agreement, no refusal hidden in prose and no malformed JSON — the failure modes `llm` spends most
of its handler on (`engine/llmaction.py`). It is also fast and cheap: one reading of the evidence
answers every question asked of it, and output tokens are free or nonexistent.

## The catalog

Decision models have their own catalog beside the LLM one, in Settings → **Decision endpoints**
(or `config.yaml`). It is separate on purpose: no chat role can resolve into a decision model, and
`decide` can never be handed a chat model — the two contracts are different.

```yaml
decision_endpoints:
  predator:            # local-model-server on the GPU box
    protocol: openai
    base_url: http://192.168.0.246:8790/v1
    key_var: PREDATOR_DECISIONS_TOKEN
    timeout_s: 600     # a dozen photos on a CPU fallback takes minutes
  openrouter-jev:
    protocol: systemone
    base_url: https://openrouter.ai/api/v1
    key_var: OPENROUTER_API_KEY      # the same key the LLM endpoint uses
  otari-decisions:                   # the same Jev through an Otari gateway (docs/model-gateway.md)
    protocol: systemone
    base_url: http://172.17.0.1:4000/api/v1
    key_var: OTARI_API_KEY
  openai:
    protocol: openai                 # base_url blank → https://api.openai.com/v1
decision_models:
  vl8b: {endpoint: predator, model: qwen3-vl-8b-instruct}
  jev: {endpoint: openrouter-jev, model: typesafe/jev-1.13}
  luna: {endpoint: openai, model: gpt-6-luna}
decision_model: jev            # what a decide call uses when it names none
decision_media_model: vl8b     # …and when it carries images (blank → decision_model)
```

A decision model's `multimodal` defaults to its protocol's (`DECISION_MEDIA_DEFAULT`): on for
`openai`, off for `systemone`. Both protocols CARRY images; the default only says what the
protocol's usual model reads — Jev reads none — so a vision model served on `systemone` (through a
gateway, llama-server or local-model-server) is switched on by its own `multimodal: true`. The
loader reports a model bound to a missing endpoint, a default that is not in the catalog, and an
image default that does not take images (`config/decisionconf.py`). Each model card has a **test** button: one yes/no question with
an obvious answer, showing latency, P(yes) and usage.

**An instance with no decision model never shows a run the `decide` action** — the kind is
projected out of the schema, the prose and CAPABILITIES (`engine/loopsetup.py`), so configuring
the first one is what switches it on.

### Per routine

A routine (or a conversation) can pick its own decision models, the way it picks its chat ones:
the routine page's **Models** group carries two more roles once the instance has a decision
model — `decision` (the decide action) and `decision_media` (decide calls that carry images,
offering only the models that take them). A conversation's header carries the `decide` picker.
They live in the same `models:` map in routine.yaml:

```yaml
models: {main: Opus medium, decision: predator-vl8b, decision_media: predator-vl8b}
```

A call that names no `model` takes the first configured model that can carry it — the routine's
roles before the instance defaults (`endpoints/decisions.default_decision_model`):

| call carries | asked in order |
|---|---|
| text only | the routine's `decision` → the instance `decision_model` |
| images | the routine's `decision_media` → its `decision` if that takes images → the instance `decision_media_model` → `decision_model` |

A text-only choice is passed over for an image call rather than refused, so a routine that picked
Jev for its text still has its photos judged. The roles are checked where they are saved: a chat
model in a decision role, a decision model in a chat role, and a text-only model in
`decision_media` are all refused (`web/config_fields.validate_models`). `rsched run-once --model
decision=<name>` overrides one for a single run. CAPABILITIES marks the defaults THIS routine
gets, so a run reads its own picture.

## The two protocols

Every provider speaks one of two wire shapes; one adapter each (`endpoints/decisions_*.py`)
normalises both to the same answers.

| protocol | request | carries | served by |
|---|---|---|---|
| `openai` | `POST {base}/decisions` — `input` (text, or user messages of `input_text` + inline `input_image` data URLs) and a `questions` LIST (`predicate` / `choice` with `choices` / `score` with `levels`) | text **and images** | OpenAI (`gpt-6-luna`, public beta 2026-10, $0.10/Mtok input, output free) · local-model-server on predator |
| `systemone` | `POST {base}/systemone` — `state` (string, object or array), a `questions` MAP (`noul` / `choice` with `criteria` map / `score` with `criteria` levels) and an optional `images` list of `data:image/…;base64,…` URLs (at most 16) | text; **images** where the server reads them | TypeSafe direct (`https://api.typesafe.ai/v1`, `jev-latest`, a TypeSafe key) · OpenRouter (`https://openrouter.ai/api/v1`, `typesafe/jev-1.13` or `~typesafe/jev-latest`, an OpenRouter key) · an Otari gateway (`{gateway}/api/v1`, its key, e.g. `openrouter:typesafe/jev-1.13`) · llama-server and local-model-server, which read the images |

OpenRouter's `/api/alpha/decisions` takes the same Jev shape as `/api/v1/systemone`; the adapter
uses the latter, which is the same path on both vendors. Both adapters go through the shared
transport helpers — retries on 408/409/429/5xx, the credential ladder (inline key → Secrets →
env file), auth failures flagged for the UI — and every call is recorded like a chat completion,
so it shows in the LLM activity dock while it runs. Usage folds into the run's budget; `cost` is
booked when the provider reports it (OpenRouter does).

## The `decide` action

```json
{"kind": "decide", "say": "…",
 "question": "Which team should own this ticket?",
 "options": ["payments: checkout, billing, refunds", "frontend: rendering, browsers"],
 "evidence": "My checkout page shows a blank screen after I click Pay."}
```

- `question` + `options` asks a **choice**; each option is `value: what it means` and the answer
  names the value. No options asks **yes/no** (the answer is P(yes)). `answer_type: "score"`
  reads the options as ordered levels, **lowest first**, and answers with the probability-weighted
  level index. 2–26 options.
- `questions` asks **several independent questions over the same evidence in one call** — each
  with its own snake_case `name`, `question`, `answer_type`, `options`. One reading of the
  evidence answers all of them; on predator the encoded images are kept in the cache across them,
  so six questions over five photos cost about one.
- `evidence` is text or a JSON object/array. `files` adds up to 16 paths: images
  (png/jpeg/webp/gif, ≤ 7 MiB each) go to a model that takes them and are judged **together**;
  text files are read in whole as named evidence fields, so a long document is classified without
  passing through the run's own context first. Paths resolve and gate exactly like `read_file`'s.
  At most 120,000 characters of text per call.
- `model` names a decision model; without it the call goes to the routine's own decision role,
  else the instance default for its payload (see *Per routine* above). Images bound for a
  text-only model are refused before any request, naming the models that take them.
- `background: true` defers it like an `llm` call.

The observation is numbers only:

```
OBSERVATION (decide · vl8b over 2 images):
- full_body: P(yes) = 0.962
- mood: bright, confidence 0.70 — calm 0.300 · bright 0.700
- build: score 1.99 on 0..2, confidence 0.99 — [0] slight 0.000 · [1] average 0.010 · [2] strong 0.990
```

A decision model gives no reasons. Act on the probability with a threshold you can name, and ask
`llm` when the reason matters. Probabilities from a quantised local model are uncalibrated unless
a temperature was fitted for the workload — the 9B's measured calibration was good, the 4B's was
not (below).

## predator: hosting the measured model

The model measured in conversation `c-20261002-165425` — **Qwen3-VL-8B-Instruct Q4_K_M + its
mmproj (Q8_0)**, read the SemIf way: the prompt is evaluated and the logits at the last position
are softmaxed over only the declared answer letters, nothing generated — is served by its own
repository, [local-model-server](https://github.com/wehnsdaefflae/local-model-server). It runs on
`ubuntu-predator` as the systemd user unit `local-model-server.service` on port 8790, installed by
that repository's `install.sh`, and serves the `openai` protocol (`/v1/decisions`) as well as
`/v1/systemone` and `/v1/chat/completions`. Until 0.483.0 its code lived here as
`deploy/decision-server/`; how it loads, unloads, shares the GPU with training jobs and batches
images is documented — and tested — there now.

Configure it like any other decision endpoint: `protocol: openai` (or `systemone`, with
`multimodal: true` on the model, since that protocol's default is off), `base_url:
http://192.168.0.246:8790/v1`, and the bearer token the server's install wrote, stored in Secrets
under the endpoint's `key_var`. Both adapters send a `multimodal` model its images, so the
image decision model may sit on whichever wire the operator prefers.

Measured on predator, 2026-10-07 (RTX 3060 laptop, 6 GB): 12/12 correct on the ground-truth
shapes (shape, colour, three-sides, edges), ~2.2 s for four questions over one small image,
~6 s per 512-px photo (the CPU vision encode dominates). Earlier measurements on SemIf's committed
144-row authored fixture (mean family balanced accuracy): this VL-8B **0.8795** on text, the
text-only Qwen3.5-9B **0.9317**, hosted Jev **0.883** (TypeSafe's subset). So for text-only
decisions Jev (on OpenRouter, or through the gateway) is the natural default and predator's model
the image one.
