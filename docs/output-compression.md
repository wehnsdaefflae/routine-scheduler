# Output compression

Large command output is re-encoded LOSSLESSLY before the model reads it (`engine/lossless.py`,
applied at the one seam `engine/output_compression.command_output`). Every encoding is a
transform plus its exact inverse; a candidate is kept only when the inverse gives back the
captured output — byte for byte for text, value for value for JSON — and the result is smaller:

- `grep` — `path:line:text` search hits, each path written ONCE as a heading over its hits;
- `paths` — one-path-per-line listings, each folder written once over the names inside it;
- `json` — minified with the standard library, every array of same-keyed flat objects written
  as one `{"$table": {"cols": […], "rows": […]}}` (column names once, not once per row).

It is plain engine behaviour, not a setting. There is nothing to decide per routine — an
encoding that loses anything or saves nothing is never shown — and the per-routine switch it
once had was changed by no routine ever. What it buys is small and real: 508 applications
saving ~82k tokens across the fleet in its last twelve days as a setting (2026-09-17 → 09-29).
No proxy, provider reconfiguration or second agent loop is involved — and no package: stdlib only.

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
must fit the observation cap without further truncation. This measures preview character counts
and estimates tokens as characters / 4; it does not measure billing or guarantee savings on a
particular model.
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
operator-only line under that output — outcome, preview characters before and after, the
estimated saving, elapsed time, and the reason behind a fallback. The model never sees it.

**What the feature buys ONE ROUTINE over time**: the Stats tab's *Output compression by routine*
table (`/api/stats` → `compression`, built by `rsched/readmodels/compression_stats.py`). Each run
tallies its own outcomes (`RunContext.compression_stats`, ticked at the single compression seam)
into its durable workflow-usage record, so the roll-up outlives run retention exactly as per-util
stats and monthly spend do. The columns are `candidates` (successful command outputs the engine let
through at all), `attempts` (the compressor ran), `applied` with its hit rate, the estimated
tokens saved, `rejected`, `no gain`, and the wall clock spent inside the compressor.

Read `rejected` as a canary. A refused result costs the run exactly what an accepted one costs and
delivers nothing; with JSON minified deterministically the count should now sit at zero, so a
routine accumulating rejections means a compressor is producing something the validation does not
accept — the table marks a row whose rejections outrun its applications. Runs that
finished before the tally existed carry no counters and are outside the window rather than
counted as zeros — the table names the date its window starts.
