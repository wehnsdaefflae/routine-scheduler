---
effect:
  with: makes design choices it can defend for this brief
  without: reaches for the same fonts, palette and layout it always does
  when: it builds pages or visuals somebody will actually look at
tags: [frontend, design, ui]
---
# rule: interface design — build something that looks chosen, not generated

Left alone, generated design converges: the same fonts, the same palettes, the same layout
reached for regardless of subject. The result reads as templated because it is. Design work
is a series of deliberate choices you can defend, made for this brief and no other.

- **Pin the subject first.** Name the thing, its audience, and the page's single job before
  choosing anything visual. Distinctive choices come from the subject's own world — its
  materials, vocabulary, artifacts. A design that could belong to any product will.
- **Know the defaults so you can avoid them.** Generated design currently clusters on a few
  looks: cream background with a high-contrast serif and a terracotta accent; near-black with
  one acid-green or vermilion accent; broadsheet columns with hairline rules and no radius.
  Each is fine when the brief asks for it and a default when it doesn't. Same for overused
  faces (Inter, Roboto, system stacks) and purple-gradient-on-white.
- **Plan before you build, then critique the plan.** Draft a compact token system — 4–6 named
  hex values, typefaces for display and body, a layout concept, and the one signature element
  the page is remembered by. Then read it back against the brief: anything you would have
  produced for a different subject gets revised before a line of code is written.
- **Structure should encode something true.** Numbered markers, eyebrows, dividers and labels
  are information, not decoration — use a 01/02/03 sequence only when the content really is a
  sequence. Motion the same way: one orchestrated moment beats scattered micro-interactions,
  and excess animation is itself a tell.
- **When the brief asks for rich and animated, build rich and animated.** The line above is a
  default for work with no stated direction, not a ceiling. A brief that asks for depth, motion
  and interaction is a brief you follow — the person asking has weighed it and it is their page.
  What does not change is that each effect still has to earn its place: motion marks arrival or
  change, never idleness; a thing that moves forever is noise on a page somebody reads daily; and
  reduced-motion, keyboard focus and a phone-width layout are met whatever the register.
- **Spend boldness in one place.** Let the signature element carry the risk and keep
  everything around it quiet. Match execution to ambition: maximalist directions need
  elaborate follow-through, minimal ones need precise spacing and type.
- **Meet the quality floor silently.** Responsive to mobile, visible keyboard focus, reduced
  motion respected, and CSS specificity kept straight so section and element selectors don't
  cancel each other out. None of this gets announced; it is the baseline.

**Provenance & freshness.** Distilled from Anthropic's `frontend-design` skill, the canonical
and evolving upstream. Its most time-sensitive part is the catalogue of looks that
*"AI-generated design currently clusters on"* — the cream/serif/terracotta, near-black/acid-
accent, and broadsheet defaults named above. That list tracks a moving target, so resolve the
current version at read-time rather than trusting this **2026-08-13** snapshot — **live copy
wins**:
- raw — `https://raw.githubusercontent.com/anthropics/claude-code/main/plugins/frontend-design/skills/frontend-design/SKILL.md`
- dir/API — `https://api.github.com/repos/anthropics/claude-code/contents/plugins/frontend-design`

Refresh the "current default looks" enumeration and the surrounding guidance from that file's
`# Frontend Design` → *Design principles*, *Process*, and *Restraint and self-critique*
sections. The writing half of the same skill (*More on writing in design*) is captured by the
sibling rule `interface-copy`.
