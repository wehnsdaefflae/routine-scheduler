---
effect:
  with: designs each page or document for this brief and reader in the reader's own words — then looks at the rendered result before calling it done
  without: ships default layouts and placeholder copy it has never seen rendered
  when: it produces pages, visuals or documents people look at
assists:
  - id: look-at-the-render
    moment: pre-finish
    predicate: rendered-output-unseen
    payload: remind
    line: >-
      You produced something people will look at and have not seen it rendered. Render it and
      look at the result — a page at phone width too, every page of a document — before you
      call it done; defects that exist only in the render are invisible in the source.
tags: [frontend, design, writing]
---
# rule: interface-craft — design for this brief and reader, then look at what you built

Left alone, generated design converges on the same fonts, palettes, layouts and placeholder
words whatever the subject — and reads as templated because it is. Make every choice one you
can defend for this brief.

- **Pin the subject first.** Name the thing, its audience and the page's one job before
  choosing anything visual; draw distinctive choices from the subject's own world.
- **Know the defaults so you can avoid them:** cream with a high-contrast serif and a
  terracotta accent; near-black with one acid accent; hairline broadsheet columns; Inter or
  Roboto; purple gradients on white. Each is fine when the brief asks for it.
- **Plan, then critique the plan.** A few named colours, a display and a body face, a layout
  idea, one signature element. Revise whatever you would have produced for any other subject.
  Spend boldness in one place; keep the rest quiet.
- **Structure and motion carry meaning.** Numbering only for a real sequence; motion marks
  arrival or change, never idleness. When the brief asks for rich and animated, build it —
  each effect still earns its place.
- **Words are design material.** Name things by what the reader controls, not by how the system
  is built. Labels say what happens; one word per thing across the whole flow; errors say what
  went wrong and how to fix it; one job per element; specific over clever.
- **Meet the floor silently.** Phone width without sideways scroll, visible keyboard focus,
  reduced motion respected; a document's page size, orientation and breaks fit its content.
- **Look at the thing you built.** It is done when the rendered result has been seen, not when
  the source matches your intent. Some defects exist only in the render; diagnose them by
  looking again, not by reasoning about what the code should do.
