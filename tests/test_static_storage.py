"""No console module touches web storage bare — util.js's `storage`/`session` are the one way.

Merely READING the `localStorage`/`sessionStorage` global throws in a browser that blocks site
data, and a bare call anywhere on a route's mount path takes that route down: the run rail did
it once (tests/ui/test_conversation_rail.py), then the admin toggle and the fork's prefill
hand-off did it to the default route. util.js wraps both areas with a degrade-to-memory
fallback; index.html's pre-paint theme read is the one inline exception and carries its own
try/catch.
"""

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "static"
BARE = re.compile(r"\b(?:localStorage|sessionStorage)\s*\.")
ALLOWED = {STATIC / "util.js"}


def _code(src: str) -> str:
    """The source with `//` line comments dropped — prose may name the globals freely."""
    return "\n".join(line.split("//", 1)[0] if not line.lstrip().startswith("//") else ""
                     for line in src.splitlines())


def test_no_bare_web_storage_call():
    offenders = [f"{p.relative_to(STATIC.parent)}:{n}"
                 for p in sorted(STATIC.rglob("*.js")) if p not in ALLOWED
                 for n, line in enumerate(_code(p.read_text(encoding="utf-8")).splitlines(), 1)
                 if BARE.search(line)]
    assert not offenders, ("use storage / session from static/util.js (they survive a browser "
                           "that refuses site data):\n" + "\n".join(offenders))
