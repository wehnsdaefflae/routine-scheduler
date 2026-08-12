"""Settings API package: one focused router per concern (endpoints, source, github,
secrets, the server runtime knobs, restart), assembled here into the single /settings
router that app.py mounts. The library repository has NO settings surface — it is
managed exclusively by the library-sync routine.
"""

from __future__ import annotations

from fastapi import APIRouter

from . import (
    endpoints,
    github,
    machines,
    oauth,
    pause,
    restart,
    secrets,
    server,
    source,
)

router = APIRouter(tags=["settings"])
for _mod in (endpoints, source, github, oauth, machines, secrets, server,
             restart, pause):
    router.include_router(_mod.router)
