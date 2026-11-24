# The Playground

A Conversations-shaped page for features that are not production yet. You talk to a playground
conversation exactly as you talk to any other; what the flag buys is that the engine asks an
experimental layer what to add to each turn, and that every addition is logged for you to judge.

Off by default. `playground_enabled: true` in the instance config makes the page and its nav
entry appear (the same gate `browser_view_url` and the desktop addresses use).

## What is isolated, and what deliberately is not

Isolated: **production runs are untouched.** The engine's seam (`engine/tails.compose` →
`playground.runtime.at_observation`) returns `""` for any run whose own `routine.yaml` does not
carry `playground: {on: true}`, so a routine's turns are composed exactly as before. The
experiment reads its config from the conversation's `routine.yaml` directly and adds no field to
`RoutineConfig`, so nothing about it can change how a production config is parsed. The layer is
wrapped whole: an exception inside it is logged and swallowed, never raised into a turn.

Not isolated, on purpose: **the same engine, the same code path, the same stores.** An
experiment that ran on a fork of the loop would be testing the fork. The layer reads the real
utils, the real `.memory/` notes, the real reminders and rules — read-only — because retrieval
judged against toy data tells you nothing.

## The experiments

| slug | what it claims | query |
|---|---|---|
| `util-hint` | after a `shell`/`script`/`write_util` turn, name a util whose subject covers what the run just did by hand | `A+O` — the action and the observation |
| `memory-hint` | name a memory note whose `about` line bears on the situation; memory is otherwise pull-only | `O+R` — the observation and the run's reading of it |
| `intent-field` | ask the run to state its next intention as one clause, so the query signal is not mixed with reflection | `I` |

The query components are the proposed turn composition: **A** the action, **O** the observation,
**R** the reflection, **I** the intention, **P** the person's words. Note that **`R` and `I` are
the same text today** — the current composition has one `say` field carrying both — which is
what `intent-field` exists to separate, and what makes a clean `I`-only query possible at all.

Why `util-hint` queries `A+O` and not `I`: a run re-implementing a util does not *intend* to.
The evidence is the command it just ran, not its plan.

## Reading the results

Each conversation's runs write `playground-firings.jsonl` beside the run. Two kinds matter:

* `fired` — this text was appended to that turn's observation. The run saw it.
* `below_floor` — a hit was found and **withheld** because it did not clear the score floor.

The near-misses are the point. A layer that logged only its successes could not be calibrated,
and the floor is where this proposal currently fails (below).

## The floor, and the first real finding

Set per conversation: `playground: {on: true, min_score: 1.1}`, or on the page when you start
one. It exists as a dial rather than a constant because the measurement did not find a value
that works.

Measured over **6,731 real `shell`/`script`/`write_util` turns** from 138 run transcripts,
ranking the 56-util catalog on `A+O` with token overlap:

* median top score **1.21**, p90 **2.09**, max **14.46**;
* and the **high scorers are coincidences** — `voice-rewrite` ranked top for
  `cp voice-rewrite-new.py`, `code-search` for the word "search" inside a Python heredoc;
* while a **true positive** — a hand-rolled `smtplib` call, for which `mail` is the right
  answer — scored **1.26**, i.e. *below the median of the noise*.

So no single threshold separates signal from coincidence on the lexical backend. The cause is
visible: shell boilerplate (`cd`, `python3`, paths, `EOF`) dominates the overlap. Two repairs
are worth testing — shell-specific stopwords, or narrowing the evidential query to the command's
verb rather than its whole text — and both are cheap to try from the bench.

A second brittleness is pinned as a test: `email` does not match `mail`, and `smtplib` does not
match `smtp`. That is the strongest case for the embedding comparison, which is what the bench
is for.

## The bench

`POST /api/playground/bench` and the panel on the page: fill in any components, pick stores, and
see lexical and embedding rankings over the same input, side by side, with the floor drawn on
every row. Embeddings run **here only**, never inside a conversation's turn — a live turn must
not wait on a provider.

Configure a backend with three env vars (any OpenAI-compatible `/v1/embeddings`):

```
RSCHED_PLAYGROUND_EMBED_URL=https://<provider>/v1/embeddings
RSCHED_PLAYGROUND_EMBED_MODEL=<model id>
RSCHED_PLAYGROUND_EMBED_KEY_ENV=<NAME_OF_THE_SECRET_ENV_VAR>
```

Only `http(s)` is accepted (a `file:` URL would make the backend a local-file reader). Vectors
are cached on disk under `<routines_home>/.control/playground-embed-cache`, keyed by model and
text, so a condition is embedded once however often you query it. Unconfigured, the page says
what is missing and shows the lexical column alone.

## What gets indexed

The **condition** — the text that says when an item applies — never its payload:

| store | indexed |
|---|---|
| utils | summary + `usage:` verbs + tags (not the source) |
| memory | the `about` line of `.memory/INDEX.md`, which already states when to consult the note |
| reminders | the `description` (the consequence), never the `regex` — indexing the regex would be circular |
| rules | `effect.when` + each assist's operative `line` |

Reminders keep their blast radius in the result: `owner` is empty for a global row and the
routine slug for a local one. A promotion decision about a global row is not the same decision.

## Promotion

Not a button. When an experiment earns production, that is a deliberate code change: move the
layer's call out of the playground seam, delete the flag check, and give it real tests. This
page exists to tell you whether it earned it.

## Where the code is

`src/rsched/playground/` — `stores.py` (the four libraries as conditions), `scoring.py` (the
backends), `features.py` (the experiments and their config), `runtime.py` (the one function the
engine calls). `src/rsched/web/api_playground.py` is the HTTP surface,
`static/views/playground.js` + `playground-bench.js` the page, `tests/test_playground.py` the
tests — whose first concern is that a production run is unchanged.
