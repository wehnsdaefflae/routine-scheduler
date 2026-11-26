---
slug: ai-detection-evaluation
title: Measure how well an AI-text detector performs, reproducibly
when: 'You want a claim about an AI-text detector tested: how accurately it separates machine-written from human-written text on material you control, with its error rates and their uncertainty stated.'
tags:
- evaluation
- measurement
- benchmark
- statistics
- ai-detection
axis: the DETECTOR under test and the corpus it is measured on vary — what holds is that the labelled material is fixed and held out before the first score, every generation prompt and setting is recorded, the decision threshold is declared rather than fitted after the fact, and both error rates are reported with their uncertainty
updated: '2026-11-26'
setup:
  permissions: []
  rules:
  - evidence-discipline
  - verify-independently
  - audit-coverage
  - second-opinion
  workdir: '{{workdir}}'
  fs_write_roots:
  - '{{workdir}}'
  fs_read_roots:
  - '{{corpus}}'
  connections: {}
  note: >-
    This is a measurement job, so the access it needs is narrow: READ on `{{corpus}}`, the
    labelled material being scored, and WRITE only in `{{workdir}}`, where the score table and
    the report land. A hosted detector and a text-generating model are each a configured
    capability with its own credential in the central store — a run never sees the secret, and an
    unconfigured service is simply unavailable, which is an access question for the principal
    rather than something to substitute around. Keep the corpus read-only: a benchmark whose
    inputs the evaluation can rewrite has no held-out set left. This playbook measures detectors;
    it is not for altering text so a detector scores it differently.
---

## Parameters

- `{{detector}}` — the detector or detectors under test, with the version or model identifier
  and the exact scoring endpoint used.
- `{{corpus}}` — the labelled material: which texts are human-written, which are machine-written,
  where each came from, and how the labels are known to be right.
- `{{threshold}}` — the decision rule being evaluated, declared before scoring. Default: the
  detector's own documented default, stated explicitly.
- `{{claim}}` — the claim being tested, in numbers ("flags machine text 98% of the time at a 1%
  false-positive rate"). Default: the vendor's own published claim about `{{detector}}`.
- `{{workdir}}` — where the score table, the run record and the report are written.

## Instructions

1. **Write down the claim as a testable statement first.** `{{claim}}` has to name a metric, a
   population and a threshold before anything is scored. "Is it accurate?" cannot be answered;
   "does it reach 95% true-positive rate at a 1% false-positive rate on undergraduate essays"
   can. State what result would refute it.
2. **Fix the corpus and hold it out before the first score.** Freeze `{{corpus}}` — a manifest
   with a checksum per text — and record where every item came from. Nothing is added, dropped or
   relabelled after scoring begins; a corpus edited once the numbers are in measures the editor,
   not the detector.
3. **Document how the machine-written half was produced.** Model and version, the full generation
   prompt, temperature and any other setting, the date. These are the experiment's conditions: a
   number reported without them is not reproducible by anyone, including you next month.
4. **Make the two halves comparable.** Same topics, same genre, same length distribution, same
   language. Where the human texts are long essays and the machine texts are short answers, the
   measurement separates length, not authorship — report the distributions so a reader can see
   it. Keep a slice of plainly human material (older than the models, with provenance) as the
   control.
5. **Score every item through one unchanged procedure,** and keep the raw per-item output —
   the score, the label the detector returned, the request identifier, the timestamp — in a table
   under `{{workdir}}`. Aggregates are recomputed from that table and never typed by hand.
6. **Report both error rates, not accuracy.** True-positive and false-positive rate at
   `{{threshold}}`, with the confusion matrix and the counts behind it. On a corpus that is mostly
   human text, a detector can be 95% accurate and still be wrong about most of the texts it
   flags; the false-positive rate is the number with real consequences, because a false accusation
   of a person is the harm this measurement exists to quantify.
7. **State the uncertainty.** Give a confidence interval for each rate and say how many items it
   rests on. A rate from 40 texts carries an interval wide enough to contain most competing
   claims, and saying so is the finding.
8. **Declare the threshold before fitting, and show the curve.** Report the detector's full
   score distribution per class and the operating curve across thresholds, then the chosen
   `{{threshold}}`'s numbers. A threshold picked after seeing the scores reports the best case,
   not the performance.
9. **Probe robustness as part of the measurement, with each condition labelled.** Run the same
   procedure on material that differs in a way practice will meet — a different language, a
   non-native writer's prose, a translated text, a lightly edited machine draft, a different
   generating model — and report per-condition rates. The point is to map where the detector's
   accuracy falls off, so a reader knows the limits of the headline number.
10. **Separate the measurement from the verdict.** Report what the numbers support about
    `{{detector}}` on `{{corpus}}`, and name explicitly what they do not support: another
    population, another generator, another language, or any judgement about an individual text.
    Then say whether `{{claim}}` held.
11. **Leave the evaluation re-runnable.** A single saved helper that reads the frozen manifest,
    scores it and recomputes the table — plus the run record of settings — so the same numbers
    come back from the same inputs and a later run compares against them rather than restarting.

## Notes

- Detector vendors change models behind a stable endpoint name. Record the date and any version
  string with every batch; two runs a month apart may not be measuring the same system.
- A detector's own confidence label ("likely AI") is a threshold already applied. Where the raw
  score is available, evaluate that and apply thresholds yourself.
- Scores on very short texts are near-meaningless, and most tools say so in their documentation.
  Report a minimum length, and treat anything under it as a separate condition.
- Human text from after a model's training data may itself have been machine-assisted. Label
  provenance conservatively and prefer older material for the control slice.
- Per-item outputs about identifiable people are sensitive. Keep the table under `{{workdir}}`,
  report aggregates, and do not put a named person's score in a reply.
- This measures detectors. A request to make a particular text score differently is a different
  job and not one this playbook covers — say so rather than drifting into it.
