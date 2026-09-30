---
effect:
  with: rewrites its own instructions (main.md, stages/ and tuning.yaml) when its runs show them wrong
  without: reports an instruction it thinks is wrong instead of rewording it
  when: refining its own procedure is part of the job, or its recipe keeps naming things that change under it
tags: [self-management, authoring, recipe]
requires:
  actions: [write_recipe]
---
# permission: recipe-authoring — revise this routine's own instructions

Change the smallest thing that fixes what a run observed. Keep the operator's wording and every
constraint; one you think is wrong is a question for him, not an edit.
Name capabilities, never a util or its flags: tools change under a recipe; what worked belongs
in memory. A service or protocol may be named.
Say in your finish summary what you changed and why. The recipe is versioned, but a bad edit is
recoverable only once someone knows it happened.
