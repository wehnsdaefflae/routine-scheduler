# Select targets

Pick what this run improves: **the three least recently RUN candidates** — always.

## Do
1. Sort the qualifying candidates by their newest finished run timestamp, **oldest first**
   (least recently run at the front). This is the whole rule: routines whose latest
   activity has waited longest for a pass come first; a routine that just ran goes to the
   back of the line.
2. Take the first 3 (fewer if fewer qualify).
3. If no candidate qualifies (nothing ran since your last sweep), skip to `record` with an
   honest "nothing new to improve".

## Next
Write `state/phase.json = {step: "study-target", cursor: {targets: [...], done: []}}`.
Read `steps/study-target.md`.
