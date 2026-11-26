---
slug: grant-latex-writing
title: Write a funding proposal or manuscript in LaTeX, to the call's own rules
when: 'A funding proposal or a manuscript has to be produced in LaTeX against a call or a journal: mandated sections, page and character limits, a required template — and it has to compile.'
tags:
- writing
- latex
- grants
- academia
- documents
axis: the CALL and the document vary — what holds across them is reading the funder's formal rules into a checklist before drafting, keeping the template they mandate rather than a nicer one, compiling after every change, and reporting the counts the submission portal will enforce
updated: '2026-11-26'
setup:
  permissions:
  - shell
  rules:
  - work-order
  - verify-independently
  - make-failure-visible
  - ask-policy
  workdir: '{{workdir}}'
  fs_write_roots:
  - '{{workdir}}'
  fs_read_roots: []
  connections: {}
  note: >-
    A LaTeX build is a local toolchain run, so `shell` is the permission this playbook needs —
    and with it, `{{workdir}}` as a write root so the file actions can edit the sources the build
    reads. Keep every other path READ-only: a proposal directory often sits beside last year's
    submissions, and those are not to be rewritten. The engine, the class file and the packages
    the call mandates must already be installed on the box; a missing document class is an
    environment gap to report, not something to paper over by switching the class. Nothing here
    submits anything: a portal upload is a separate decision and a separate access.
---

## Parameters

- `{{call}}` — the funder's call, programme or journal, with a link to its own guidelines page.
- `{{manuscript}}` — the document being produced or revised, and its main `.tex` file.
- `{{limits}}` — the formal limits that apply: pages per section, character counts, font and
  margin rules, the mandated template. Default: whatever `{{call}}` states, read from the source.
- `{{deadline}}` — when it must be submitted, and in which timezone.
- `{{workdir}}` — the directory holding the sources and the build output.

## Instructions

1. **Turn the call's rules into a checklist before writing a sentence.** Fetch `{{call}}`'s own
   guidelines and list every formal requirement with the line it comes from: section order and
   names, page or character limits per section, the template or document class, font size,
   margins, reference style, what goes in which uploaded file. A proposal rejected on form was
   never read on content.
2. **Use the template the call mandates, exactly.** Where it ships a class or style file, build
   against it unmodified. Improving a funder's layout is how a submission becomes non-compliant;
   if the template is broken, say so and ask with the `ask_user` action rather than silently
   replacing it.
3. **Write to the limits, measured.** After each section, report its real page extent and
   character count against `{{limits}}` — the compiled page count, not an estimate from the
   source length. A section that fits in the editor and overflows at compile time is the normal
   failure here.
4. **Compile after every change, and read the log, not just the exit status.** The capability in
   your catalog that builds LaTeX returns warnings that matter: overfull boxes past the margin,
   undefined references, a missing bibliography pass. Quote the ones that change the page.
   A document that "compiled" with an unresolved reference ships `??` to the reviewer.
5. **Keep the content the principal's.** Claims about his record — what he has published, funded,
   supervised or achieved — are his to confirm. Draft them with the exact wording you propose and
   put them to him; never invent a number, an affiliation, a prior grant or a collaborator.
6. **Answer the call's questions in the call's own words.** Reviewers score against the criteria
   as written, so mirror their vocabulary (impact, feasibility, novelty, work packages) and make
   the mapping visible — one subsection per criterion where the form allows it.
7. **Make the budget and the work plan arithmetic check out.** Person-months, totals per year,
   percentages: add them up and state the sums. A table whose columns do not add is the first
   thing an administrator notices.
8. **Keep the bibliography reproducible.** One source of truth for references, with DOIs where
   they exist, and a full build (document, bibliography, document again) before any count is
   reported. Hand-typed citations drift from the entries they claim to be.
9. **Deliver the artefacts the submission actually needs** — the compiled PDF, the sources, and a
   short note of what remains open — and state what is still missing before `{{deadline}}`:
   signatures, letters of support, a collaborator's section, an institutional number.

## Notes

- Page limits are usually about the *compiled* document and sometimes exclude references or
  annexes. Which pages count is a rule to read, not to assume.
- A funder's PDF requirements can include no embedded fonts of a certain kind, no hyperlinks, or
  a flattened form. Check the produced file against them rather than trusting the toolchain.
- Several calls forbid changing the template's margins or font; a reviewer with a page counter
  will find a shrunk one.
- Where a section exists in two languages, both are part of the document and both compile —
  a translated abstract pasted in without a build is a broken build waiting for the deadline.
- Build output belongs beside the sources, not in the repository history; keep the artefact the
  principal needs, drop the intermediates.
