# Optional output compression

Routines and conversations offer **Off**, **Measure only**, and **Compress (experimental)**
under their configuration controls. The persisted `routine.yaml` key is `output_compression`:
`off`, `measure`, or `compress` (default). The mode is deliberately not called `on`: YAML 1.1
reads a bare `on` as a boolean, and a hand-edited config must not silently become `True`. Changes apply at the next run/reply; children inherit
that run's setting. Ponytail is independently available in the General rules picker, unbound
by default. Existing installations receive the new rule through the ordinary add-only seed sync.

ONE engine: **JSON is minified with the standard library**
(`json.dumps(..., separators=(",", ":"))`) — whitespace is the only thing JSON's grammar lets a
compressor drop without changing a value, so this needs no package at all and cannot lose data.
Nothing else is a candidate. No proxy, provider reconfiguration or second agent loop is involved.

Only successful util/script/shell calls with stdout of at least 2,000 characters are candidates.
JSON is still accepted only after independent equivalence validation — minification is faithful by
construction, so the check is an assertion rather than a safety net, and it costs a parse. Validation
preserves every array element, object member, value and numeric spelling (including integer/decimal
distinctions and signed zero). Whitespace, object key ordering and equivalent string escaping may
change. Duplicate object keys, NaN/Infinity, non-JSON representations and changed numeric spellings
are conservatively rejected, retaining the existing capped output and recovery pointer. Logs,
ordinary prose, source-file reads, user messages, permissions, stderr and failed commands are not
compressed: they keep the capped head and the spill pointer that already carries the rest.
Existing stdout/stderr capture limits still apply: an original means the full *captured* output, not
unlimited output.

The complete compressed preview, its label and recovery pointer must be smaller than the existing
capped preview and its pointer. Otherwise the current representation wins. The compressed preview
must fit the observation cap without further truncation. Measure mode records the comparison but
keeps the model-visible observation unchanged. This measures preview character counts and estimates
tokens as characters / 4; it does not measure billing or guarantee savings on a particular model.
Measurements and fallback reasons appear in command output details and the observation's
`compression` transcript field. Compare total usage, cache hits, correctness, recovery reads and
elapsed run time when deciding whether to enable it.

Applied compression first saves the original at `runs/<run>/outputs/` (under the child's run
folder for children). Those files share the run's retention, independent of the five-run spill
cache; ordinary `read_file` paging retrieves them without recompression. They are engine-owned,
just like the transcript. Failed saves, compressor exceptions, invalid results and larger results
retain existing output. The transcript stores the actual preview and pointer, so replay re-reads
them rather than compressing anything, and the already-sent conversation prefix remains untouched.

**Why logs are not excerpted.** An excerpting compressor for them (the native `LogCompressor`
of headroom-ai) costs 28 packages in the engine image — litellm, openai, boto3, botocore,
huggingface-hub, tokenizers, tiktoken, opentelemetry — and a private-module import, on a box
that has already OOM-killed PID 1. Measured over five days of fleet traffic it saved 30,923
tokens against 111 M input tokens (0.03%), from ten applications against 576 results that
changed nothing. The capped head plus the spill pointer is the same outcome for a fraction of
the weight.

**Why JSON was never compressed by that package.** Its `SmartCrusher` truncates arrays to
`max_items_after_crush` (15) and emits no CCR marker, and its `lossless_only` flag is inert —
output is byte-identical with the flag on and off
([headroomlabs-ai/headroom#3625](https://github.com/headroomlabs-ai/headroom/issues/3625)). Measured
over one week of fleet traffic: 260 of its results were refused by the validation above, and every
one of the 121 whose original was still recoverable was real data loss (dropped array elements,
dropped object keys, a list replaced by a string). On the same payloads, stdlib minification was
faithful 335 times out of 335 and saved roughly five times the tokens the crusher's accepted results
saved, at ~0.5 ms per payload against ~250 ms. What its successful compressions had been doing was
removing whitespace — which is what minification does, provably.

## Where the numbers surface

Two surfaces, for two different questions.

**What happened to ONE command's output**: the transcript and the conversation chat print an
operator-only line under that output — mode, outcome, preview characters before and after, the
estimated saving, elapsed time, and the reason behind a fallback. The model never sees it.

**What the feature buys ONE ROUTINE over time**: the Stats tab's *Output compression by routine*
table (`/api/stats` → `compression`, built by `rsched/readmodels/compression_stats.py`). Each run
tallies its own outcomes (`RunContext.compression_stats`, ticked at the single compression seam)
into its durable workflow-usage record, so the roll-up outlives run retention exactly as per-util
stats and monthly spend do. The columns are `candidates` (successful command outputs the mode let
through at all), `attempts` (the compressor ran), `applied` with its hit rate, the estimated
tokens saved, `rejected`, `no gain`, and the wall clock spent inside the compressor.

Read `rejected` as a canary. A refused result costs the run exactly what an accepted one costs and
delivers nothing; with JSON minified deterministically the count should now sit at zero, so a
routine accumulating rejections means a compressor is producing something the validation does not
accept — the table marks a row whose rejections outrun its applications. Runs that
finished before the tally existed carry no counters and are outside the window rather than
counted as zeros — the table names the date its window starts.

Ponytail's rule is an adaptation of revision `356918eba965ee1eac64bd3a7f0dd02108350de5`:
https://github.com/DietrichGebert/ponytail/tree/356918eba965ee1eac64bd3a7f0dd02108350de5
It retains the implementation decision order and defers to existing scheduler reporting and project
testing conventions. The upstream MIT notice is preserved in [the license copy](licenses/ponytail-MIT.txt).
It overlaps with `change-restraint`; bind it deliberately for a coding trial, not globally.

For an evaluation, compare baseline and Ponytail on equivalent tasks using the same model and
budgets. Start with measure mode, then use isolated copies for coding trials. Keep a short
conversational task as a control. These evaluation steps do not change live routine settings; the
default described above still applies to missing settings.
