# Optional output compression

Routines and conversations offer **Off**, **Measure only**, and **Headroom (experimental)**
under their configuration controls. The persisted `routine.yaml` key is `output_compression`:
`off`, `measure`, or `headroom` (default). Changes apply at the next run/reply; children inherit
that run's setting. Ponytail is independently available in the General rules picker, unbound
by default. Existing installations receive the new rule through the ordinary add-only seed sync.

The Docker deployment includes Headroom in the image and at startup. Compression remains
enabled by default; each routine or conversation can choose Off or Measure only. For other installations, install the optional dependency with `uv sync --extra headroom` (or `pip install 'rsched[headroom]'`
for a packaged installation). No proxy, provider reconfiguration or second agent loop is involved.
Without the extra, enabled modes preserve existing output and show an unavailable measurement.

Only successful util/script/shell calls with stdout of at least 2,000 characters are candidates.
JSON objects/arrays are accepted only after independent JSON equivalence validation; the upstream
`lossless_only` flag is not proof. Validation preserves every array element, object member, value
and numeric spelling (including integer/decimal distinctions and signed zero). Whitespace, object
key ordering and equivalent string escaping may change. Duplicate object keys, NaN/Infinity,
non-JSON representations and changed numeric spellings are conservatively rejected, retaining
the existing capped output and recovery pointer. Recognisable level-prefixed logs use
log excerpts; their label explicitly says lines were omitted. Ordinary prose, source-file reads,
user messages, permissions, stderr and failed commands are not compressed. Existing stdout/stderr
capture limits still apply: an original means the full *captured* output, not unlimited output.

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
retain existing output. The transcript stores the actual preview and pointer, so replay uses them
without calling Headroom, and the already-sent conversation prefix remains untouched.

The adapter is pinned to **headroom-ai 0.37.0** and calls its native compression primitives directly.
This intentionally avoids the Python pipeline's shared learning store, CCR retrieval service,
model downloads, prompt rewriting and effort routing. JSON uses `lossless_only`; CCR and feedback
hints are disabled. Package upgrades must pass the real-package smoke test before changing the pin.
Headroom: https://github.com/headroomlabs-ai/headroom (Apache-2.0).

Ponytail's rule is an adaptation of revision `356918eba965ee1eac64bd3a7f0dd02108350de5`:
https://github.com/DietrichGebert/ponytail/tree/356918eba965ee1eac64bd3a7f0dd02108350de5
It retains the implementation decision order and defers to existing scheduler reporting and project
testing conventions. The upstream MIT notice is preserved in [the license copy](licenses/ponytail-MIT.txt).
It overlaps with `change-restraint`; bind it deliberately for a coding trial, not globally.

For an evaluation, compare baseline, Ponytail alone, Headroom alone and both on equivalent tasks
using the same model and budgets. Start with measure mode, then use isolated copies for coding
and log-analysis trials. Keep a short conversational task as a control. These evaluation steps do
not change live routine settings; the default described above still applies to missing settings.
