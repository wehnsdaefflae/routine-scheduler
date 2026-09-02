---
effect:
  with: edits prose against the known fingerprint of LLM writing, not a gut feel
  without: writes in the default register, which reads as machine-written
  when: its output is read by other people as your writing
tags: [writing, ai-detection, style, editing]
---
# rule: ai-writing-tells — recognise and un-write the AI "tell"

When a step turns on whether prose reads as **human or LLM-generated** — auditing an AI draft,
spotting undisclosed AI text, or editing a draft to read human — work from the known
fingerprint of LLM writing, not a gut "feels robotic". LLMs **regress to the mean**: they
inflate importance, smooth specific facts into generic positives, and reach for a small,
repeated repertoire of words, shapes and formatting. That regression is the tell.

**The core signals (a CLUSTER, never one):**

> **⟨LIVE-FETCH REGION⟩** — the *italicised example words* in the bullets below are a frozen
> **2026-08-13** snapshot, NOT the authoritative list. A consuming agent SHOULD resolve the
> current "Words to watch" lexicons from the MediaWiki source at read-time and prefer them
> when they differ (**live copy wins**):
> - raw source — `https://en.wikipedia.org/w/index.php?title=Wikipedia:Signs_of_AI_writing&action=raw`
> - JSON — `https://en.wikipedia.org/w/rest.php/v1/page/Wikipedia:Signs_of_AI_writing`
>
> Extract the `Words to watch:` lines under these sections: *Undue emphasis…*, *Canned
> emphasis on notability…*, *Superficial analyses*, *Promotional and advertisement-like
> language*, *Vague attributions…*, *Outline-like conclusions…*, *High density of "AI
> vocabulary" words*, *Avoidance of basic copulatives*, *Collaborative communication*,
> *Knowledge-cutoff disclaimers…*. **Carve-out:** the false-precision (*"eat exactly 3 to 5
> walnuts"*) and flat-intensifier (*exactly*) items in *Formulaic shape* are **Usenet-derived
> and static** — keep them as-is, they are not on the Wikipedia page.
- **Puffery & significance-inflation** — *stands/serves as, is a testament, plays a
  pivotal/crucial role, underscores its importance, evolving landscape, indelible mark*; the
  specific ("inventor of the first train-coupling device") smoothed into the generic
  ("a revolutionary titan of industry").
- **Promotional register & vague attribution** — *boasts, vibrant, nestled in the heart of,
  renowned*; *experts argue, observers have cited, several sources* (when few are cited).
- **AI-vocabulary density** — *delve, intricate, tapestry, robust, meticulous, garner,
  showcase, underscore, additionally* (sentence-initial).
- **Formulaic shape** — negative parallelism (*"not just X, but Y"*), rule-of-three tricolons,
  elegant variation, copulative-avoidance (*serves as* for plain *is*), false precision
  (*"eat exactly 3 to 5 walnuts"*) and flat intensifiers (*exactly*).
- **Self-narration & rhetorical scaffolding** — announcing the move instead of making it
  (*"let me address this directly", "I'll answer that myself", "the most justified question
  here is…", "it's worth asking whether…"*), or erecting a rhetorical question / pseudo-standard
  only to answer it or spring the exception in the next sentence. Human expository prose states
  the substance directly — it opens with the fact, not with a frame around the fact — and this
  tell hides in ANY sentence of a block, not just the first, so a lexicon scan misses it and an
  LLM de-tell filter often reproduces it; read the whole block by hand.
- **Chatbot leakage (dead giveaways)** — *Certainly!, I hope this helps, as an AI language
  model, as of my last training update*; model citation debris (`oaicite`, `turn0search0`,
  `[cite: 1]`, lenticular brackets 【 】).

**What human writing has (add these, don't fear them):** plain *is/has*, plain verbs (*wrote*
not *authored*), superlatives, hedges (*very, perhaps*), wordy human constructions (*in order
to*), and above all **genuine specificity + one consistent voice** end to end.

**Do NOT chase (ineffective, cause false accusations):** perfect grammar, mixed
casual/formal register, "bland" or "fancy" prose, transition words in isolation, unsourced
content, em-dashes alone. Humans are ~chance at style-only detection; require a cluster.

**Two hard limits — state them, don't overclaim:**
- **Detectors are not ground truth.** Do not decide AI-authorship on a detector alone; modern
  AI-text detectors have non-trivial error rates and are sensitive to paraphrasing (per Wikipedia's
  own guidance). Do not accuse a human on one tell.
- **De-telling fools people, not robust detectors.** Stripping every signal above makes text
  read human to a *reader*, but empirically does **not** move a robust
  modern AI-text detector (an aggressive humanise-rewrite barely shifted a near-certain AI
  score): such detectors key on a deep token-distribution fingerprint, not surface tics. Use this rule to read/write human, not as a detector bypass.

**Provenance & freshness.** Synthesised from the Wikipedia field guide
*[Wikipedia:Signs of AI writing]* (WP:AITELLS) and the Aug-2026 `alt.usage.english` thread
"AI style: 'exactly'". The Wikipedia "Words to watch" lexicons drift — fetch the current
source at read-time rather than trusting a frozen copy:
`https://en.wikipedia.org/w/rest.php/v1/page/Wikipedia:Signs_of_AI_writing` (raw:
`…/w/index.php?title=Wikipedia:Signs_of_AI_writing&action=raw`). The full four-part field
guide with every lexicon lives in the conversation artifact `ai-writing-tells-rule.md`.

[Wikipedia:Signs of AI writing]: https://en.wikipedia.org/wiki/Wikipedia:Signs_of_AI_writing