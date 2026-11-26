# The Playground

An experimental retrieval layer that runs INSIDE a flagged conversation. You talk to a playground
conversation exactly as you talk to any other; what the flag buys is that the engine asks the
layer what to add to each turn, and that every addition is logged for you to judge.

There was a console subpage on top of this — a Conversations-shaped page listing the flagged
conversations with their firings, plus a retrieval bench comparing a lexical and an embeddings
backend side by side. The operator removed it (F959, 2026-11-25: *"Remove the playground subpage.
We don't need it after all"*), and with it went its HTTP surface (`web/api_playground.py`), the
embeddings backend, the vector store, and the `playground_enabled` instance flag that gated
nothing else. **Nothing about a run changed**: the seam below is untouched, and the firings of a
conversation that already ran are still on disk beside its runs.

There is therefore no page and no API. A playground conversation is started like any other
conversation, and it is turned into one by its `routine.yaml` (below); its firings are read from
the file the run wrote.

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

## Turning it on for one conversation

In that conversation's own `routine.yaml`:

```yaml
playground:
  on: true
  features: [util-hint, memory-hint]
  min_score: 1.1          # optional; else features.MIN_SCORE
```

Absent, malformed, or `on` missing = off, i.e. exactly production behaviour.

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

Each conversation's runs write `playground-firings.jsonl` beside the run
(`features.firings_path`). Two kinds matter:

* `fired` — this text was appended to that turn's observation. The run saw it.
* `below_floor` — a hit was found and **withheld** because it did not clear the score floor.

The near-misses are the point. A layer that logged only its successes could not be calibrated,
and the floor is where this proposal currently fails (below).

## The floor, and the first real finding

Set per conversation (`min_score` above). It is a dial rather than a constant because the
measurement did not find a value that works.

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
verb rather than its whole text.

A second brittleness is pinned as a test: `email` does not match `mail`, and `smtplib` does not
match `smtp`.

## What the removed embeddings comparison measured

Kept here as the record, because the conclusion outlives the bench that produced it. Over 774
indexed conditions, ten hand-labelled cases (seven real manual-re-implementation turns, three
negatives nothing in the library covers):

| | top-1 correct (of 7) |
|---|---|
| lexical (token overlap) | **1** |
| dense (qwen3-embedding-8b) | **7** |

Dense fixed lexical's worst failures outright — the `cp …voice-rewrite-new.py` turn that scored
lexical's highest false positive (14.46 for `voice-rewrite`, matched on the filename) returned
`fs-ops`; the hand-rolled `smtplib` call returned `mail`; `pdftotext` returned `doc-read`.

But the gating question was separate, and the answer was no — for both backends:

* dense's weakest true positive **0.558** sat *below* its strongest false positive **0.630**;
* over 150 real turns, dense's median top score was **0.618** (p10 0.539, p90 0.697) — the true
  positives land *inside* the noise band;
* lexical was worse on both counts (median 1.061, p90 1.206, true positives from 0.707).

**So a fixed floor cannot carry a push channel on either backend.** What the measurement
supports is dense retrieval for *ranking* — "which util is this about" — not for *deciding
whether to interrupt*. Three routes worth testing: a margin test (top-1 minus top-2) rather than
an absolute floor; a cheap classifier over the top few candidates; or restricting the channel to
the moment a run is about to *author* a util, where a list is useful and a false positive is
cheap.

One practical cost the numbers exposed, and the reason the in-run layer is lexical: a per-turn
dense query is a network round trip. A 150-turn serial replay timed out and had to be batched 32
at a time, and the local embedder measured ~1.5 s/text warm — fine for a one-off index build,
unusable on a per-turn query path. (Also learned there: a local model's first batch is cold
start, not throughput — it ran ~7× faster warm. Never quote a rate from the first batch.)

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
layer's call out of the playground seam, delete the flag check, and give it real tests.

## Where the code is

`src/rsched/playground/` — `stores.py` (the four libraries as conditions), `scoring.py` (the
lexical backend), `features.py` (the experiments and their config), `runtime.py` (the one
function the engine calls). `tests/test_playground.py` holds the tests — whose first concern is
that a production run is unchanged — and `tests/test_playground_subpage_removed.py` holds the
absence of the removed subpage.
