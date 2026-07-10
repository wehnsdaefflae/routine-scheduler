"""Settings API package: one focused router per concern (endpoints, library, source,
github, secrets), assembled here into the single /settings router that app.py mounts."""

from __future__ import annotations

from fastapi import APIRouter

from . import endpoints, github, library, secrets, source

router = APIRouter(tags=["settings"])
for _mod in (endpoints, library, source, github, secrets):
    router.include_router(_mod.router)
