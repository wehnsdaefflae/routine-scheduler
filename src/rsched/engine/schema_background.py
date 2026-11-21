"""The `background` SIDE FIELD as schema — the one field that defers a call (D118).

Out of `engine/actionschema.py` (over the size bar, shrink-only). The description is the only
place a run learns what may be deferred and the ordering promise, so it must track
`actions.BACKGROUNDABLE_KINDS`.

The prose is CLAUSE-LED (`kind1/kind2: …`), because `kindsurface._project_description` ships
only the clauses of the kinds a run may emit, and
`tests/test_kindsurface.py::test_a_shared_field_describes_every_kind_that_accepts_it` holds
every accepting kind to having one.

The UNIVERSAL (unled) clause comes FIRST: an unled clause is kept for every kind, which is how a
run holding only read kinds still learns there is no cap on calls in flight; and
`test_a_shared_fields_clauses_each_carry_their_own_lead` forbids an unled clause AFTER a led one,
which would read as every kind's own prose.
"""

from __future__ import annotations

#: The `background` flag — defer ONE call and get the turn back at once.
BACKGROUND_FIELD = {
    "type": "boolean",
    "description": ""
                   "run THIS ONE call in the BACKGROUND and get the turn back at once. The "
                   "observation you receive names a handle and says the call is running; the "
                   "REAL observation is appended at a later turn boundary, tagged with that "
                   "handle. Use it for a call that would make a waiting human sit — a heavy "
                   "fetch, a long `llm`, a slow test run, a big write — and keep working or "
                   "keep talking meanwhile. Do not background a call whose result your VERY "
                   "NEXT action needs — you would only have to wait for it anyway. There is no "
                   "cap on how many may be in flight at once; your own turn budget is the "
                   "limit, since every result still has to be read. A kind that STEERS the run "
                   "(finish, ask_user, report, spawn, subtask, wait, kill) may not defer, nor "
                   "may delete, move, mkdir, write_util or write_rule — each refusal says why · "
                   "util/websearch/code_search/script/shell/llm/decide/read_file/view_image/"
                   "memory_read/read_rule:"
                   " a READ or a FETCH, whose only effect is the observation it brings back. A "
                   "backgrounded `shell` or `util` CAN write, and declares no target, so "
                   "ordering what it writes stays yours · "
                   "write_file/edit_file/memory_write: a WRITE defers too, and is ORDERED "
                   "rather than trusted — any later action touching the target you declare "
                   "(`path`, or `name` for a memory note) is HELD until the write lands, so it "
                   "can never be read stale even if you never `wait`. A second deferred write "
                   "to one target is refused (no defined order), as is a `then_script` riding a "
                   "deferred change: defer the change alone, then run the script",
}
