"""The INSTANCE's token pace this month — the fleet figure a per-routine card cannot give.

Its own module because `api_routines` is at the house size bar and because the question is a
different one: a card answers *which routine is growing*, this answers *is the fleet heading past
the window it runs on*, which is what the operator asked to be able to anticipate. A subscription
window is spent by every routine at once, so only a sum over all of them can say it.

The per-routine reading rides the card payload (`api_routines._card` → `token_pace`); both come
from `readmodels/token_pace.py`, which names no ceiling — see its docstring for why.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from ..readmodels.stats import monthly_spend
from ..readmodels.token_pace import fleet_pace
from .routines_common import _state

router = APIRouter(tags=["routines"])


@router.get("/routines-token-pace")
def routines_token_pace(request: Request) -> dict:
    """`{"fleet": {...}}` — tokens, volume and notional $ this month, with a month-end
    projection once the month is far enough along to extrapolate from (None before that).
    """
    return {"fleet": fleet_pace(monthly_spend(_state(request).server))}
