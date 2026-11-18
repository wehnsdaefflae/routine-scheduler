"""The actual turn-completion request boundary, shared by every retry and model switch.

Contract delivery belongs beside the outbound call: the turn-head check cannot see a
schema shed or an endpoint switch inside the retry loop. Keeping this boundary separate
also leaves completion.next_action's statement and file-size ratchets unchanged or smaller.
"""

from __future__ import annotations

from .schemaindex import request_contract


def complete_attempt(loop, endpoint, ref, schema: dict | None, *, attempt: int):
    """Prepare the actual request's contract, then forward the existing turn-call options."""
    request_contract(loop, endpoint, schema)
    return endpoint.complete(loop.messages, model=ref.model,
                             schema=schema, effort=ref.effort,
                             temperature=ref.temperature,
                             max_tokens=ref.max_tokens,
                             # Bookkeeping only: the wrapper consumes these, not the transport.
                             purpose=f"turn {loop.ctx.turn + 1}"
                                     + ("" if attempt == 1 else f" · retry {attempt}"),
                             kind="turn")
