# state: steady

The normal cadence — stay here while the instruction describes ongoing work.

- **Pick the run's work.** From the instruction, the state digest, and anything the user sent:
  what does THIS run deliver? Prefer finishing in-progress work over starting new work. Guard
  standing obligations first (anything the instruction says must never slip).
- **Execute in small verified steps** (per the run flow in `main.md`): tools via the `util`
  action, `spawn` for separable chunks, verify every output.
- **Tend feedback** — read what the user pursued, answered, or ignored, and steer toward it.

Each run should advance the deliverable by one concrete increment and improve the process a little.
