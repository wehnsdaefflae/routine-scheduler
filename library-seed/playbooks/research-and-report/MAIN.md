---
slug: research-and-report
title: Research a topic and deliver a cited report
when: You want a topic researched across multiple sources and written up as one cited deliverable.
tags:
- research
- report
- web
axis: the research TOPIC and the report's depth/format — the method (search, corroborate, cite, synthesize) stays fixed
updated: 2026-07-14
---

# Research a topic and deliver a cited report

## Parameters
- `{{topic}}` — the question or subject to research.
- `{{depth}}` — how deep to go (a quick brief vs. a thorough survey). Default: a focused brief.
- `{{output}}` — the deliverable's format and location. Default: `artifacts/report.md`.

## Instructions
1. Restate `{{topic}}` as 3–6 concrete sub-questions. If the topic is ambiguous, ask ONE clarifying
   question before spending effort.
2. Search the web (the `websearch` util, then fetch promising pages) to gather sources for each
   sub-question. Prefer primary/authoritative sources and note each source's date.
3. Corroborate every load-bearing claim against a SECOND independent source. Drop or explicitly flag
   any claim you cannot corroborate — never present a single-source claim as settled.
4. Synthesize into `{{output}}`: a short summary up top, then one section per sub-question with an
   inline source link on each statement, and a closing "Open questions / low-confidence" section.
5. Reply with the headline findings and the artifact path — do not paste the whole report into chat.

## Notes / gotchas
- Recency matters: write "as of <date>" for anything that changes over time.
- Keep the report under `artifacts/` so it renders in the side panel; re-writing the same filename
  updates it in place.
