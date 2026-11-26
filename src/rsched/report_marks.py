"""What a report DELIVERY is MARKED with — and what those marks say about who owes a reply.

A pure mapping with no I/O, beside `report_detail` (the delivered bound) and `report_disposal`
(the disposal value): the ledger writes these marks at the filing and reads them back one drain
later, and those two sites agreeing is the whole reason this is one module. They were two
literal dicts half a file apart, each spelling the flags for itself, which is how a flag comes
to be written by one and ignored by the other.

The two marks are NOT the same thing, and the difference is the recipient's visibility:

- `closes` — a reply that ENDS a thread, born settled. It owes nothing back and it is not
  freight: the run gate keeps a closure out of its inbox predicate
  (`daemon/gate_builtin.pending_inbox`), because firing runs for closures chains.
- `no_reply` — an ORIGINATING notice whose `closes` the disposal dropped, having nothing to
  complete (`report_disposal`). It owes nothing back either, but it IS freight: nobody has read
  it yet, so marking it a closure would hide it from its recipient instead of delivering it.
  The sender asked for no answer, not for no reader.
"""

from __future__ import annotations

from .report_detail import Disposed


def delivery_marks(report_id: str, sender: str, disposal: Disposed | None = None) -> dict:
    """The `extra` an addressed report's inbox message carries (`engine/inbox.file_message`),
    on top of the msg-* shape every channel shares.
    """
    return {"report": str(report_id), "from": str(sender),
            **({"closes": True} if getattr(disposal, "closes", False) else {}),
            **({"no_reply": True} if getattr(disposal, "closes_dropped", False) else {})}


def owes_reply(msg: dict) -> bool:
    """Does this drained report message owe its reader an answer?

    Asking a run to reply "thanks, done" to a closure is the ratchet `closes` exists to stop,
    and a notice whose closure was dropped asked for no answer either — only for a reader.
    """
    return not (msg.get("closes") or msg.get("no_reply"))
