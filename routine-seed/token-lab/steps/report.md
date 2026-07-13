# report — rewrite artifacts/report.html

The report IS the deliverable — the user reads it in the artifact sidebar. Rewrite it
whole every run (same path: `artifacts/report.html`), self-contained: inline CSS, no
external assets, dark-friendly (light text on dark works in the app's viewer).

Structure, in order:
1. **Header** — "Token lab", the run date, and a 2-3 sentence executive summary.
2. **What changed since the last report** — short bullets.
3. **Measured baseline** — the numbers from `state/baseline.json` as small tables:
   totals, tokens/turn by routine and model, cache hit share by endpoint, the three most
   expensive runs with their why. Format large numbers readably (1.2M, 45k).
4. **Experiment log** — this run's experiment: hypothesis, design in one line, the A/B
   numbers, verdict. Then a compact table of ALL past experiments (date, method, verdict,
   effect) from `state/experiments/`.
5. **Greatest potential** — the ranked list, the heart of the report. For each method:
   expected saving on THIS instance (derived from the baseline: "routine X would save
   ~N tokens/run"), evidence (your measurement or a cited source), effort to adopt, and
   risk to output quality. Methods you refuted go in a closing "tested, not worth it"
   list — they are the fence against re-proposing.
6. **Sources** — links.

Style: clean typographic hierarchy, tables over prose for numbers, no decoration that
does not carry information. Write it with ONE `write_file` (the whole document), then
verify it landed by reading the first 20 lines back.

Next state: `record-close` — write `{"phase": "record-close"}` to `state/phase.json`.
