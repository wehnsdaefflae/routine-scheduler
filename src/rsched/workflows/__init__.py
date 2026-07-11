"""The workflow library: Python pattern files that DEPICT control flow, never execute.

Workflows are parsed statically (`pyworkflow.py`), linted (`lint.py`), ranked/drafted by
the system model (`suggest.py` / `generate.py`), and decomposed into a routine's own
`main.md` + `steps/` markdown at scaffold time (`adapt.py`) — the runtime interprets only
that materialized markdown.
"""
