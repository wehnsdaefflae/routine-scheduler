"""The run engine — the ONE agent loop in the system.

The workflow document is the harness: each turn the orchestrator model returns a single
JSON action (`actions.py` is the contract), the engine dispatches it, appends the
observation, and repeats until `finish`. Everything else in this package serves that
cycle: prompt composition, budgets/control between turns, effect execution, history
compaction, subruns, user interaction, and the append-only transcript.
"""
