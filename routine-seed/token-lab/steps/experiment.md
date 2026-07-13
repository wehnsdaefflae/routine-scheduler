# experiment — one bounded A/B, measured

Test today's candidate with the `llm` action only. You are the lab bench, not the plumber:
the system itself is never touched.

1. Design first, in `state/experiments/<date>-<method>.md`: hypothesis, the A and B
   prompts (or A/B context layouts), sample size (3-5 trials per arm is enough for a
   direction), and the metric — usually the observation's reported `usage` tokens plus a
   task-success judgment.
2. Run the arms via `llm` subcalls. Keep every prompt self-contained (the subcall sees
   ONLY what you send). Cap: ~30 subcalls per run — a smaller clean experiment beats a
   big sloppy one.
3. Judge success blind where you can: a separate `llm` subcall grades outputs WITHOUT
   knowing which arm produced them.
4. Append raw numbers + verdict (supported / refuted / inconclusive, with effect size)
   to the same experiment file, and update the method's **status** in `state/backlog.md`.
5. `memory_write` anything expensive you learned about experiment technique itself
   (what confounded a measurement, what sample size sufficed) — future runs must not
   re-learn it.

An inconclusive result with a sharper follow-up design is a fine outcome; say so plainly.

Next state: `report` — write `{"phase": "report"}` to `state/phase.json`.
