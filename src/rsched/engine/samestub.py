"""The one-line observation that stands in for a result the run was ALREADY shown, unchanged.

Rendered from the stored observation itself — `engine/loopshape.py` marks it with
`same_as_turn`/`same_chars` before the observation is recorded — so the live turn and a resumed
leg's replay say the same words (`observations.render_observation` asks here first). The full
result stays in the transcript record; only the prompt is spared the second copy.
"""

from __future__ import annotations


def render(obs: dict) -> str:
    """The stand-in, or "" for an observation that carries no `same_as_turn` mark."""
    turn = obs.get("same_as_turn")
    if not isinstance(turn, int):
        return ""
    return (f"OBSERVATION ({obs.get('kind')}): the SAME result turn {turn} returned — "
            f"{obs.get('same_chars', '?')} characters, unchanged, so it is not repeated here; "
            "it is above in this conversation. Re-running this unchanged will return it again.")
