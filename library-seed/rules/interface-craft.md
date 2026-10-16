---
effect:
  with: designs each page or document for this brief and reader in the reader's own words — then looks at the rendered result before calling it done
  without: ships default layouts, invented filler and placeholder copy it has never seen rendered
  when: it produces pages, visuals or documents people look at
read_when: >-
  Before you build or change a page, a visual or a document people will look at, and before you
  call it finished.
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

Left alone, generated design converges on the same fonts, palettes, layouts and phrases whatever
the subject, and reads as templated because it is. The tell is rarely one choice; it is
unexamined defaults stacking up. Make every visible choice one you can defend for this brief;
what the brief pins down, follow exactly, even a look named below.

- **Pin the subject first.** Name the thing, its audience, the scene it is used in and the
  page's one job, and whether people will act on it, read it or be persuaded by it: a working
  screen opens on status and actions, not a slogan. Draw distinctive choices from the subject's
  own world (its materials, notation, publications, places), not from its category's usual look
  or a costume of the tools its audience uses.
- **Know the defaults so you can step past them:** cream with a high-contrast serif and a
  terracotta accent; near-black with one acid accent; hairline broadsheet columns; Inter or
  Roboto; purple gradients on white; identical rounded cards under one soft shadow; glass, glow
  or gradient washes as atmosphere; a centred hero over three feature cards or a strip of big
  numbers. Chrome too: a small spaced-capitals label over every heading, one headline word set
  off in italic or colour, numbers on things with no order, coloured edge stripes, emoji for
  icons, fake browser frames. Swapping one for its neighbour (cyan for purple, a fashionable
  grotesque for Inter, glass for cards) keeps the template.
- **Plan, then critique the plan.** A few colours with named roles, one or two type families on
  a clear scale, a layout idea, one signature element only this subject could have; sketch more
  than one direction before choosing. Would you have planned the same for any similar brief?
  Revise whatever you would have. Spend boldness in one place, keep the rest quiet, and refine
  what is there before adding more.
- **Hierarchy does most of the work.** One thing leads each view. Build tiers from weight, tone
  and space together, not size alone; leave more space around a group than inside it, and let
  tight and open gaps alternate rather than one gap everywhere. Separate with space and tone
  before borders and boxes; a card is for a unit someone acts on.
- **Structure and motion carry meaning.** Numbering only for a real sequence; a label only where
  it says what its heading cannot. Motion marks arrival or a change the person caused, never
  idleness; the more often something happens, the less it should move, and one orchestrated
  moment beats an entrance on every section. When the brief asks for rich and animated, build
  it — each effect still earns its place.
- **Words are design material.** Name things by what the reader controls, not by how the system
  is built. Labels say what happens; one word per thing across the whole flow; errors say what
  went wrong and how to fix it; an empty state offers the next step. Write one plain sentence
  about this subject instead of slogan fragments, triplets or buzzwords; say each fact once
  rather than restaging the brief as hero, cards, stats and questions. No working notes or
  placeholder text reach the artefact.
- **Use real content and never invent proof.** Build with the brief's own material; invent only
  what you must, plausibly and marked as a sample, and never customers, quotes, figures, prices
  or features the subject lacks. Try the awkward real cases (a long name, one item, an empty
  list, a missing image): demo data that fits perfectly hides defects.
- **Meet the floor silently.** Body text at 4.5:1 contrast or better; colour never the only
  signal; phone width without sideways scroll; visible keyboard focus; loading, error and empty
  states designed; reduced motion respected; nothing overflows, overlaps or runs off the page; a
  document's page size, orientation and breaks fit its content.
- **Look at the thing you built.** It is done when the rendered result has been seen — at phone
  width too, every page of a document — not when the source matches your intent. Hierarchy,
  rhythm and sameness exist only in the render: does one thing lead, and could this belong to
  any other subject? Diagnose what you see by looking again, not by reasoning about what the
  code should do; fix everything one look shows, look once more, then stop polishing.
