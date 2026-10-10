"""The run gate's two page surfaces: the check VOCABULARY the gate editor renders its forms
from and "Test the gate now" — the same admission a scheduled fire would get, answered without
starting a run.

A test goes through exactly the path a fire takes (`run_gate._decide`: the same jails, the same
baseline, the same secrets) with a throwaway run directory, so what the button says is what the
next fire would do — a separate "preview" implementation would be a second gate to keep honest.
Nothing about a test is recorded as a run. Its fingerprints are discarded: a baseline is
what an ADMITTED run saw; a test admits nothing.
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from .. import gatekit
from ..config.routine import RunGateConfig
from ..daemon import run_gate
from ..daemon.runner_state import ActiveRun
from .routines_common import _info, _state

router = APIRouter(tags=["gate"])


@router.get("/gate/kinds")
def gate_kinds() -> dict:
    """Every check a gate may list: what it answers and its parameters."""
    return {"kinds": {kind: {"meaning": meaning,
                             "params": {name: {"type": typ, "required": req, "help": help_}
                                        for name, (typ, req, help_) in spec.items()}}
                      for kind, (meaning, spec) in gatekit.KINDS.items()}}


class TestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # the gate as the page currently shows it — possibly not saved yet; absent = the saved one
    run_gate: dict | None = None


@router.post("/routines/{slug}/gate/test")
async def test_gate(request: Request, slug: str, body: TestBody) -> dict:
    info = _info(request, slug)
    cfg = info.cfg.model_copy(deep=True)
    if body.run_gate is not None:
        try:
            cfg.run_gate = RunGateConfig.model_validate({**body.run_gate, "enabled": True})
        except ValueError as exc:
            raise HTTPException(422, f"run_gate: {exc}") from exc
    if not cfg.run_gate.checks:
        raise HTTPException(422, "the gate lists no checks to test")
    tmp = Path(tempfile.mkdtemp(prefix="gate-test-"))
    run = ActiveRun(slug=slug, run_id=f"{slug}:gate-test", run_ts="gate-test", run_dir=tmp)
    meta: dict = {}
    # Every component the gate CONSISTS of, in the order shown. The gate stops at the first
    # component that finds work, so its answer list is routinely shorter than this — and a
    # component silently absent from the page reads as one that passed. Sent so the page can
    # show it as not asked instead.
    asked_of = [str(c.get("kind")) for c in cfg.run_gate.checks if c.get("kind")]
    try:
        async with asyncio.timeout(cfg.run_gate.timeout_s):
            decision = await run_gate._decide(run, cfg, _state(request).server, "test", meta)
        stderr = (tmp / "gate-stderr.txt").read_text(encoding="utf-8") if (
            tmp / "gate-stderr.txt").is_file() else ""
        return {"decision": decision["decision"], "reason": decision["reason"],
                "checks": meta.get("checks") or [], "asked_of": asked_of,
                "stderr": stderr[-2000:]}
    except TimeoutError:
        return {"decision": "error", "checks": [], "asked_of": asked_of,
                "reason": f"the gate did not answer within {cfg.run_gate.timeout_s}s — a "
                          "scheduled fire would be recorded as FAILED"}
    except Exception as exc:
        stderr = (tmp / "gate-stderr.txt").read_text(encoding="utf-8") if (
            tmp / "gate-stderr.txt").is_file() else ""
        return {"decision": "error", "checks": meta.get("checks") or [], "asked_of": asked_of,
                "reason": f"a scheduled fire would be recorded as FAILED: {exc}",
                "stderr": stderr[-2000:]}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
