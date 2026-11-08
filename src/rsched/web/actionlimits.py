"""The engine's DEFAULT operation limits, for the console to measure elapsed action time against.

`static/components/actiontime.js` shows how long a call has been running beside the limit the
engine would apply to it — and carried its own copy of the numbers (`{util: 300, script: 300,
shell: 120, wait: 600}`). A copy with nothing tying it to the engine is a number that goes
quietly wrong: raising `shell`'s default in `shellrun.py` would leave every console in the
fleet drawing its progress bar against 120 s, with no test anywhere to notice (D252).

So the values are built HERE, from the constants the executor actually applies — `/api/status`
serves them and the console reads them. This module adds no numbers of its own: every entry is
an import, and the one default that had no name got one (`waitop.WAIT_DEFAULT_TIMEOUT_S`).

Only the kinds that run with a deadline belong in the map. Engine work inside the turn itself
(a read, a write, an `llm` call) has no operation limit, and the console says so rather than
inventing one — which is why an absent kind is meaningful and nothing fills the gaps.
"""

from __future__ import annotations

from ..engine.fileops import UTIL_DEFAULT_TIMEOUT_S
from ..engine.waitop import WAIT_DEFAULT_TIMEOUT_S
from ..scripts import SCRIPT_TIMEOUT_S
from ..shellrun import SHELL_DEFAULT_TIMEOUT_S

#: action kind → the seconds the engine allows it when the action names no `timeout_s`.
LIMITS: dict[str, int] = {
    "util": UTIL_DEFAULT_TIMEOUT_S,          # engine/executor.do_util
    "script": SCRIPT_TIMEOUT_S,              # engine/executor.do_script
    "shell": SHELL_DEFAULT_TIMEOUT_S,        # engine/executor.do_shell
    "wait": WAIT_DEFAULT_TIMEOUT_S,          # engine/waitop.wait
}
