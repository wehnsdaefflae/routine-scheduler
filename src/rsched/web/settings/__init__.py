"""Settings API package: one focused router per concern (endpoints, library, the
scheduled library sync, source, github, secrets, the server runtime knobs, restart),
assembled here into the single /settings router that app.py mounts.
"""

from __future__ import annotations

from fastapi import APIRouter

from . import endpoints, github, library, library_sync, oauth, restart, secrets, server, source

router = APIRouter(tags=["settings"])
for _mod in (endpoints, library, library_sync, source, github, oauth, secrets, server, restart):
    router.include_router(_mod.router)
