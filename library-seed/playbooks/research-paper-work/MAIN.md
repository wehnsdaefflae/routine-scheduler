---
slug: research-paper-work
title: Work through a research paper — read it properly, reproduce it, or review it
when: 'A specific paper is the subject: you want it read and explained against its own evidence, its result reproduced or checked, its claims weighed for a review, or its place in the literature established.'
tags:
- academia
- literature
- peer-review
- reproducibility
- reading
axis: the PAPER and the job asked of it vary — reading, reproducing, reviewing, or placing it in its literature — while the discipline holds throughout, namely working from the paper itself rather than its abstract, separating what it shows from what it asserts, checking the numbers in the tables against the text, and marking every inference as yours
updated: '2026-11-26'
setup:
  permissions: []
  rules:
  - evidence-discipline
  - web-research
  - second-opinion
  - outcome-first
  workdir: '{{workdir}}'
  fs_write_roots:
  - '{{workdir}}'
  fs_read_roots:
  - '{{paper}}'
  connections: {}
  note: >-
    Reading and citing needs no reserved permission: a web fetch and a document-to-text
    conversion are ordinary catalog capabilities, and the file actions read a local PDF
    directly. Grant READ on wherever `{{paper}}` and any supplementary files live, and WRITE only
    in `{{workdir}}` for the notes and the deliverable. Reproducing a result is a different
    matter: running the authors' code is shell work on this box and needs that permission
    granted deliberately, in a throwaway directory, with the paper's own data read-only. A paper
    behind a publisher's paywall needs a configured institutional access or a copy the principal
    supplies — an abstract is not the paper, and saying so beats reviewing the abstract.
---

## Parameters

- `{{paper}}` — which paper: title, authors, year, venue, and a DOI or link, plus the local file
  if there is one.
- `{{job}}` — what is wanted: a faithful summary, an explanation of one method or figure, a
  reproduction attempt, a referee report, or the paper's standing in its literature.
- `{{depth}}` — how far to go. Default: the whole paper including the methods section; the
  supplement only where a claim depends on it.
- `{{output}}` — where the deliverable lands. Default: `artifacts/` in `{{workdir}}`, so it
  renders in the side panel.
- `{{workdir}}` — the directory for notes, extracted tables and any reproduction attempt.

## Instructions

1. **Get the paper itself and say which version you have.** Preprint, accepted manuscript and
   published version differ in the numbers that matter, and a retraction or a correction notice
   changes everything downstream. Record the DOI, the version and the date you fetched it.
2. **Read the claims off the paper, not the abstract.** List the paper's concrete claims, each
   with the section, table or figure that is supposed to support it. An abstract states what the
   authors wish to have shown; the methods state what they did.
3. **Separate what was measured from what is concluded.** Sample size, population, how
   assignment or selection happened, what the control was, what the outcome actually measured.
   Then name the gap between that and the sentence in the discussion — the inference from a
   proxy to the thing of interest is where most papers are weakest.
4. **Check the internal arithmetic.** Do the group sizes in the tables sum to the reported total?
   Do percentages match their counts? Does every figure's caption agree with its axis? Does the
   abstract's effect size equal the one in the results? Report each check with the numbers, and
   say plainly where they disagree.
5. **Read the statistics for what they claim.** What was pre-registered against what is
   exploratory, how many comparisons were made, whether the test matches the design, what the
   confidence intervals span. A small p-value from an unstated number of tried analyses is a
   weaker claim than it looks.
6. **For a reproduction attempt, run it from the paper's own artefacts and log the divergences.**
   Fetch the stated code and data, use the versions the paper names, and record every deviation
   you had to make. Report the numbers you obtained beside the published ones. A reproduction
   that "worked" without a number comparison has not reproduced anything — and a failure to
   reproduce is a finding to report precisely, never a conclusion about the authors.
7. **Place the paper in its literature where `{{job}}` asks for it.** Find what it builds on and
   what has cited it since, note any later work that contradicts or supersedes it, and give the
   citation with its date beside each. Corroborate a load-bearing outside claim against a second
   independent source.
8. **For a referee report, write it in the venue's own form** — summary, significance,
   specific numbered comments tied to section or line, major separated from minor, and a
   recommendation. Comment on the work, not the authors; propose the experiment or analysis that
   would settle each objection. A review signed in the principal's name is his to approve before
   it leaves.
9. **Mark every inference as yours,** and keep quotes verbatim with their page or section. Where
   you doubt a result, say what evidence would change your mind. Where the paper is simply right,
   say so — a review that finds only faults is not a reading.
10. **Deliver `{{job}}`'s answer first, in the reply,** with the long form in `{{output}}`:
    the claim table, the arithmetic checks, the reproduction log, and a closing list of open
    questions and low-confidence points.

## Notes

- A figure may be the only place a result appears; reading the rendered page matters, and a
  scanned PDF with no text layer needs a look rather than an extraction.
- Supplementary material often holds the sample sizes, the exclusions and the failed analyses.
  A claim that depends on an exclusion rule cannot be judged without it.
- Author affiliations, funding and conflict-of-interest statements are part of the paper; note
  them factually without inferring motives.
- Citation counts measure attention, not correctness, and a highly cited paper may be cited as
  the thing being refuted.
- Never paste a paywalled full text into a deliverable; quote what a review needs and cite the
  rest.
- The published numbers are the record. Where your own calculation disagrees, present both and
  show the arithmetic rather than silently correcting the authors.
