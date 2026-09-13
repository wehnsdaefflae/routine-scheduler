"""Optional, one-shot compression of command stdout; never a conversation transform.

Use the pinned native Headroom primitives to avoid its global learning/retrieval stores,
provider routing and model downloads. Scheduler owns originals and transcript replay.
"""

from __future__ import annotations

import json
import re
import time

from ..paths import atomic_write
from . import outputs
from .observations import OBS_CAP_CHARS, truncate
from .run_context import RunContext

MIN_CHARS = 2_000
_LOG_LINE = re.compile(r"(?im)^\s*(?:\d{4}-\d\d-\d\d[^\n]{0,45})?"
                       r"\[?(?:INFO|DEBUG|WARN(?:ING)?|ERROR|FATAL|TRACE)\b")


def _kind(text: str) -> str:
    try:
        value = json.loads(text)
    except (ValueError, RecursionError):
        lines = text.splitlines()
        return "logs" if len(_LOG_LINE.findall(text)) >= max(10, len(lines) // 2) else ""
    return "json" if isinstance(value, (dict, list)) else ""


def _verified_json(text: str):
    """Parse conservatively: retain number spelling and reject ambiguous objects.

    Python equality alone conflates true/1/1.0 and rounds decimal numbers. Tagged
    numeric lexemes avoid both; even equivalent numeric reformatting is rejected.
    Duplicate keys and non-standard NaN/Infinity have no supported equivalence.
    """
    def object_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def reject_constant(_):
        raise ValueError("non-standard JSON number")

    return json.loads(text, object_pairs_hook=object_pairs,
                      parse_int=lambda value: ("number", value),
                      parse_float=lambda value: ("number", value),
                      parse_constant=reject_constant)


def _compress(text: str, kind: str) -> str:
    # This deliberately small adapter targets the native API of headroom-ai==0.37.0.
    # No Python pipeline: that pipeline can persist content in a shared learning store.
    from headroom._core import LogCompressor, LogCompressorConfig, SmartCrusher, SmartCrusherConfig

    if kind == "json":
        return SmartCrusher(SmartCrusherConfig(
            lossless_only=True, enable_ccr_marker=False, use_feedback_hints=False,
        )).crush(text, "", 1.0).compressed
    return LogCompressor(LogCompressorConfig(enable_ccr=False)).compress(text, 1.0).compressed


def command_output(ctx: RunContext, name: str, out: str, err: str, code: int) -> dict:
    """Build the existing capped output, then optionally replace only eligible stdout.

    Measure mode emits metadata only. Compression is used only if its complete preview,
    label and recovery pointer beat the existing capped observation. Originals belong to
    this run's retention, not the five-run spill cache, so another run cannot prune them.
    """
    stdout, trunc_out = truncate(out, keep="head")
    stderr, trunc_err = truncate(err, cap=8000 if code != 0 else 2000)
    capture = {key: True for key, text in (("stdout", out), ("stderr", err))
               if getattr(text, "capture_truncated", False)}
    obs: dict = {"stdout": stdout, "stderr": stderr, "truncated": trunc_out or trunc_err}
    if capture:
        obs["capture_truncated"] = capture
    if pointer := outputs.spill(ctx, name, out, err,
                                out_truncated=trunc_out, err_truncated=trunc_err):
        obs["full_output"] = pointer
    mode = ctx.routine.output_compression
    if mode == "off" or capture:
        return obs
    metrics: dict = {"mode": mode, "status": "skipped", "input_chars": len(out)}
    obs["compression"] = metrics
    if code != 0 or len(out) < MIN_CHARS or not (kind := _kind(out)):
        metrics["reason"] = "failed command, small output, or unsupported content"
        return obs
    started = time.monotonic()
    try:
        candidate = _compress(out, kind)
        if not isinstance(candidate, str) or not candidate.strip() or "<<ccr:" in candidate:
            raise ValueError("unusable compression result")
        if kind == "json" and _verified_json(out) != _verified_json(candidate):
            raise ValueError("JSON compression changed content")
        # Keep JSON lossless; logs are explicitly excerpts, never complete evidence.
        label = "lossless JSON representation" if kind == "json" else "log excerpt; lines omitted"
        candidate = f"[Headroom {label}; read full output for original text]\n{candidate}"
        rel = (ctx.routine.dir / "runs" / ctx.run_ts / "outputs"
               / f"t{ctx.turn}-{name}.out").relative_to(ctx.routine.dir)
        pointer = {**obs.get("full_output", {}), "stdout": str(rel), "stdout_chars": len(out)}
        baseline_size = len(stdout) + (len(outputs.pointer_line(obs["full_output"]))
                                      if obs.get("full_output") else 0)
        candidate_size = len(candidate) + len(outputs.pointer_line(pointer))
        metrics.update(kind=kind, candidate_chars=candidate_size, baseline_chars=baseline_size,
                       estimated_tokens_saved=max(0, (baseline_size - candidate_size) // 4))
        if len(candidate) > OBS_CAP_CHARS or candidate_size >= baseline_size:
            metrics.update(status="unchanged", reason="no smaller complete preview")
        elif mode == "measure":
            metrics["status"] = "measured"
        else:
            # Saving must succeed BEFORE replacing the evidence carried by the observation.
            atomic_write(ctx.routine.dir / rel, out)
            obs.update(stdout=candidate, full_output=pointer, truncated=trunc_err or kind == "logs")
            metrics["status"] = "applied"
    except ImportError:
        metrics.update(status="unavailable", reason="install the optional rsched[headroom] extra")
    except Exception as exc:
        # Do not expose exception text: a compressor may echo sensitive command output.
        metrics.update(status="fallback", reason=type(exc).__name__)
    metrics["elapsed_ms"] = round((time.monotonic() - started) * 1000, 2)
    return obs
