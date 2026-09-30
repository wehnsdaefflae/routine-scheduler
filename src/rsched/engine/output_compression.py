"""One-shot LOSSLESS compression of command stdout; never a conversation transform.

Plain engine behaviour, not a setting: an encoding is used only when its exact inverse gives back
the captured output and the result is smaller, so there is nothing to decide per routine — the
evidence that retired the per-routine switch was a fleet that never changed it.

LOSSLESS encodings only (`engine/lossless.py`), each kept only when its exact inverse gives
back the captured output — byte for byte for text, value for value for JSON — so nothing a
run could need is ever dropped: grep-shaped hits grouped under their file, path listings
grouped under their folder, JSON minified with uniform arrays written as tables. Anything
else keeps the existing capped head plus its spill pointer. Stdlib only, no dependency.
Scheduler owns originals and transcript replay.
"""

from __future__ import annotations

import json
import time

from ..paths import atomic_write
from . import lossless, outputs
from .observations import OBS_CAP_CHARS, truncate
from .run_context import RunContext

MIN_CHARS = 2_000


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


def _compress(text: str) -> tuple[str, str] | None:
    """`(kind, encoded)` for the first encoding that recognises this output and survives its
    own inverse, or None. The inverse is the proof: a candidate that does not decode back to
    exactly `text` is never shown to the model.
    """
    js = lossless.encode_json(text)
    if js is not None:
        encoded, tabulated = js
        _verified_json(encoded)   # the text the model reads must itself be unambiguous JSON
        back = json.dumps(lossless.decode_json(encoded, tabulated), ensure_ascii=False)
        if _verified_json(back) != _verified_json(text):
            raise ValueError("JSON encoding changed content")
        return ("json-table" if tabulated else "json"), encoded
    for kind, encode, decode in (("grep", lossless.encode_grep, lossless.decode_grep),
                                 ("paths", lossless.encode_paths, lossless.decode_paths)):
        candidate = encode(text)
        if candidate is not None:
            if decode(candidate) != text:
                raise ValueError(f"{kind} encoding is not exact")
            return kind, candidate
    return None


def command_output(ctx: RunContext, name: str, out: str, err: str, code: int) -> dict:
    """Build the existing capped output, then replace only eligible stdout.

    Compression is used only if its complete preview,
    label and recovery pointer beat the existing capped observation. Originals belong to
    this run's retention, not the five-run spill cache, so another run cannot prune them.

    Every outcome is tallied on the run here, at the ONE seam that produces it: the
    per-observation metadata is the evidence for a single call, the tally is what the
    durable usage record carries to the Stats tab's per-routine roll-up.
    """
    obs = _observation(ctx, name, out, err, code)
    if isinstance(obs.get("compression"), dict):
        ctx.note_compression(obs["compression"])
    return obs


def _observation(ctx: RunContext, name: str, out: str, err: str, code: int) -> dict:
    """The observation itself: the existing capped output, with eligible stdout replaced
    by a verified smaller preview when the comparison allows it.
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
    if capture:
        return obs
    metrics: dict = {"status": "skipped", "input_chars": len(out)}
    obs["compression"] = metrics
    if code != 0 or len(out) < MIN_CHARS:
        metrics["reason"] = "failed command or small output"
        return obs
    started = time.monotonic()
    try:
        found = _compress(out)
        if found is None:
            metrics.update(reason="no lossless encoding fits this output")
            metrics["elapsed_ms"] = round((time.monotonic() - started) * 1000, 2)
            return obs
        kind, encoded = found
        candidate = f"{lossless.LABELS[kind]}\n{encoded}"
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
        else:
            # Saving must succeed BEFORE replacing the evidence carried by the observation.
            atomic_write(ctx.routine.dir / rel, out)
            obs.update(stdout=candidate, full_output=pointer, truncated=trunc_err)
            metrics["status"] = "applied"
    except Exception as exc:
        # Do not expose exception text: a compressor may echo sensitive command output.
        metrics.update(status="fallback", reason=type(exc).__name__)
    metrics["elapsed_ms"] = round((time.monotonic() - started) * 1000, 2)
    return obs
