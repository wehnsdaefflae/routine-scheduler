"""The `background` SIDE FIELD as schema — the one field that defers a call (D118).

Its own module for the reason `engine/schema_remind.py` gives: `engine/actionschema.py` is over
the repo's size bar and may only shrink, so a field whose description has to GROW moves out
rather than pushing that file up.

And it had to grow. D118 phase 3 widened `actions.BACKGROUNDABLE_KINDS` to the three mutations
that declare one target, while this description still read "NOT available on a WRITING KIND
(write_file, edit_file, write_util, memory_write)" — so the engine accepted a deferred write
that every model was told to refuse itself, which is a feature no run can reach. The field's
description is the only place a run learns what may be deferred and what the ordering promise
is, so it is also the place the widening had to land.

The prose is CLAUSE-LED (`kind1/kind2: …`), because `kindsurface._project_description` ships
only the clauses of the kinds a run may actually emit, and
`tests/test_kindsurface.py::test_a_shared_field_describes_every_kind_that_accepts_it` holds
every accepting kind to having one.

The UNIVERSAL clause comes FIRST, and that ordering is load-bearing twice over. An unled clause
is kept for every kind (the projection never drops prose it did not positively understand), so
leading with it is what guarantees that a run holding only the read kinds still learns there is
no cap on calls in flight — the field's description is the only place a run learns that at all.
And `test_a_shared_fields_clauses_each_carry_their_own_lead` forbids an unled clause AFTER a
led one, because a continuation that re-names no kind is shown as if it were every kind's own
prose: the same way `verb`'s six `task:` clauses were once shown to `manage_lane` runs.
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
                   "util/script/shell/llm/decide/read_file/view_image/memory_read/read_rule:"
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
