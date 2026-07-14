# Playbooks

A **playbook** is a saved, generalized **conversation brief** — the proven spec of a kind of
work, captured from a conversation that went well and reused to one-shot the same kind of work
next time. Playbooks turn a good one-off conversation into a repeatable capability without
copy-pasting instructions.

They are the interactive-session analog of two moves you already make by hand: "do this again the
way it worked last time" (reuse) and "that was right — remember how to do it" (capture).

## What a playbook is — and isn't

- A playbook is a **brief**, not a workflow. Every conversation runs on the same `converse`
  harness; a playbook only changes the *instruction* the conversation starts from. It never
  changes how the agent loops, what it's allowed to do, or its schedule.
- It is **generalized**. A playbook isn't a frozen recording of one conversation — it's the
  *pattern* behind it, with the specifics that should change between uses turned into parameters.
- It lives in the **library** (Library tab), git-versioned and synced alongside workflows, traits,
  permissions, and utils — so a playbook you save on one instance travels with the rest.

## The three moves

| Move | Where | What happens |
|---|---|---|
| **Reuse** | Playbook picker on the *new conversation* form | The playbook's brief seeds the conversation; your first message just *specializes* it (and can be left empty). |
| **Capture** | **Save as playbook** button in a conversation | The system model reads the conversation — your intent plus the procedure that satisfied it — and writes a new, generalized playbook to the library. |
| **Refine** | **Update playbook** button (only when the conversation was started from a playbook) | The same distillation, but folded back into the playbook you started from — so it absorbs the corrections you made this time. |

All three are one click. Capture and Refine commit straight to the library; there's no approval
gate — you edit or roll back afterwards (every save is a git commit).

## Reuse: start a conversation from a playbook

1. Open **Conversations → + new**.
2. Pick a playbook from the **playbook** dropdown. Its one-line *when* and its *generalization
   axis* (what varies) show beneath the picker so you can confirm it fits.
3. Optionally type a first message to **specialize** it — the topic, the file, the specific ask.
   You can leave it empty; the playbook's brief is enough, and the agent will ask you for any
   parameters it needs.
4. Start. The playbook's brief becomes the conversation's working instruction, and your first
   message is layered on as "this conversation's specific request".

The conversation remembers which playbook it came from, which is what enables **Update playbook**
later.

## Capture: save a conversation as a playbook

When a conversation reached a good result, click **＋ save as playbook** at the bottom. The system
model distills the whole conversation — the first message, your later messages, and the steps the
agent actually took — into a generalized playbook:

- it infers your **true intent** (including course-corrections you made mid-way — the corrected
  instruction wins, the superseded one is dropped),
- it captures the **procedure** that worked as imperative steps,
- it picks a **generalization axis** and turns the things that should vary into `{{parameters}}`,
- and it writes a one-line **when** (when to reach for this playbook) and a few **tags**.

Save always creates a **new** playbook (the slug is suffixed if it collides). A toast tells you the
slug and the axis it chose. If the axis or steps aren't quite right, open the playbook in the
Library tab and edit `MAIN.md` directly.

## Refine: fold this conversation's changes back in

If you started the conversation *from* a playbook and then adjusted course — corrected the intent,
added a better step, dropped one — click **⟳ update playbook**. It re-distills the same way, but
revises the **source** playbook instead of making a new one, keeping what still holds and changing
only what this conversation showed should change. This is how a playbook gets better every time you
use it.

## The generalization axis — the important idea

The whole value of a playbook is getting one balance right: **specific enough to one-shot the
result, general enough to apply across the cases it's for.** The *axis* names what varies.

> Example — a playbook saved from "clean up `sales.csv` and chart revenue by month":
> the **axis** is *the dataset and the chart* (those become `{{dataset}}` / `{{chart}}`
> parameters), while the *method* — load, clean, validate, chart, save to `artifacts/` — stays
> fixed and concrete.

When you Save, the model proposes the axis; you see it in the toast and can correct it by editing
the playbook. A good axis is the difference between a playbook that works for one case and one that
works for a whole family.

## Anatomy of a playbook

Each playbook is a subfolder in the library, `playbooks/<slug>/`, holding an always-loaded
`MAIN.md` plus optional on-demand detail files. `MAIN.md` is front matter followed by the brief:

```markdown
---
slug: research-and-report
title: Research a topic and deliver a cited report
when: You want a topic researched across multiple sources and written up as one cited deliverable.
tags: [research, report, web]
axis: the research TOPIC and the report's depth/format — the method stays fixed
updated: 2026-07-14
---

# Research a topic and deliver a cited report

## Parameters
- `{{topic}}` — the question or subject to research.
- `{{output}}` — the deliverable's format and location. Default: `artifacts/report.md`.

## Instructions
1. Restate `{{topic}}` as 3–6 concrete sub-questions…
2. …

## Notes / gotchas
- Recency matters: write "as of <date>" for anything that changes over time.
```

- **`when`** is the one-line catalog entry — it's what you see in the picker and the Library list.
- **`axis`** states the generalization axis.
- **Parameters** use `{{named}}` placeholders for what varies; everything else stays concrete.
- **Instructions** are imperative steps for a future agent, not a narrative of what happened.
- **Detail files** (optional) hold long material — examples, schemas, references — that `MAIN.md`
  points to with a one-line "read `<name>.md` when you need X". `MAIN.md` stays lean (loaded every
  time); details are pulled in only when a step calls for them. When you start a conversation from
  a playbook that has detail files, they're copied into the conversation so the agent can read them
  on demand.

## Managing playbooks in the Library tab

The **Library → Playbooks** section lists every playbook with its `when`, tags, and any lint
issues. Open one to edit its `MAIN.md` (front matter + body); saving is lint-gated (it must keep a
kebab `slug`, a `title`, a one-line `when`, an `axis`, at least one tag, and an `## Instructions`
section) and commits to the library. Delete removes the subfolder — recoverable from git history.

## Where playbooks live and how they travel

Playbooks are part of the one library repo (`~/.local/share/routine-scheduler-libraries/`), so
they're git-versioned and ride the same instance sync as everything else. A playbook shipped with
the app (there's a starter, *Research a topic and deliver a cited report*) lands in an existing
instance automatically at the next daemon restart.

## See also

- **Traits & permissions** — the other library docs, and how a conversation's conduct and
  capability are set.
- **Getting started** — the pieces around routines and conversations.
