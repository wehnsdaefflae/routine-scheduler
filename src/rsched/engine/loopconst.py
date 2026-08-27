"""
The engine loop timing constants — how often a blocking wait wakes to look around.

A leaf both the loop and everything it calls need (the action router, the subrun manager),
so it lives apart rather than being imported from whichever module happened to define it.
"""

from __future__ import annotations

POLL_S = 2.0
