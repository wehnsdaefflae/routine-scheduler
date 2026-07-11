# /// script
# dependencies = ["httpx"]
# ///
"""vision — see images/PDFs for a text-only model: forward them + a question to a cloud vision model.

usage: gu vision FILE_OR_URL [FILE_OR_URL ...] --prompt TEXT [--task describe|ocr|chart|ui|pdf] [--model ID] [--max-tokens N] [--json]
calls: (none)
tags: image, pdf, multimodal, vision
secrets: OPENROUTER_VISION_KEY

Compensates for a non-multimodal main model: pass local image files (png/jpeg/webp/gif) or
https URLs plus a prompt; the answer comes back as text. --task picks the best model for the
job (each is strong at different things — see TASKS below); --model overrides it with any
OpenRouter vision model id. PDFs go through --task pdf (native file input, billed as tokens).
Free-tier models are rate-limited (~50 req/day); on HTTP 429 the util retries once on the
task's paid fallback. Needs OPENROUTER_VISION_KEY in Secrets — a deliberately separate key
from the endpoints' OPENROUTER_API_KEY (which is stripped from util environments so utils
can't silently bill the orchestrator's key). --selftest is offline (request building only)."""

import argparse
import base64
import json
import mimetypes
import os
import sys
from pathlib import Path

API_URL = "https://openrouter.ai/api/v1/chat/completions"
KEY_VAR = "OPENROUTER_VISION_KEY"
MAX_FILE_MB = 20  # request-payload sanity cap per file, before base64 (+33%)

# task → (primary model, fallback used when the primary is rate-limited / rejects).
# Chosen 2026-07 from OpenRouter's live catalog: Nemotron 3 Nano Omni leads OCRBench-V2 /
# ChartQA / ScreenSpot and is free (rate-limited); Qwen3.6-35B-A3B is the best-per-$ general
# VQA model; Gemini 3.1 Flash Lite takes PDFs natively at 1M context.
TASKS = {
    "describe": ("qwen/qwen3.6-35b-a3b", "qwen/qwen3-vl-30b-a3b-instruct"),
    "ocr": ("nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
            "qwen/qwen3-vl-235b-a22b-instruct"),
    "chart": ("nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free", "qwen/qwen3.6-35b-a3b"),
    "ui": ("nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free", "qwen/qwen3.6-35b-a3b"),
    "pdf": ("google/gemini-3.1-flash-lite", "google/gemini-3.1-flash-lite"),
}
IMAGE_MIMES = {"image/png", "image/jpeg", "image/webp", "image/gif"}


def models_for(task: str, override: str | None = None) -> tuple[str, str]:
    """(primary, fallback) for a task; an explicit --model is used for both (no fallback)."""
    if override:
        return override, override
    if task not in TASKS:
        raise ValueError(f"unknown task {task!r} (one of {', '.join(TASKS)})")
    return TASKS[task]


def part_for(source: str, data: bytes | None = None) -> dict:
    """One content part per input: https URLs pass through; local files are embedded as a
    base64 data URI (image/*) or an OpenRouter `file` part (.pdf). `data` injectable for tests."""
    if source.startswith(("http://", "https://")):
        return {"type": "image_url", "image_url": {"url": source}}
    path = Path(source)
    if data is None:
        if not path.is_file():
            raise ValueError(f"not a file: {source}")
        if path.stat().st_size > MAX_FILE_MB * 1024 * 1024:
            raise ValueError(f"{source}: over {MAX_FILE_MB} MB — downscale it first")
        data = path.read_bytes()
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    b64 = base64.b64encode(data).decode("ascii")
    if mime == "application/pdf":
        return {"type": "file",
                "file": {"filename": path.name, "file_data": f"data:{mime};base64,{b64}"}}
    if mime not in IMAGE_MIMES:
        raise ValueError(f"{source}: unsupported type {mime} (png/jpeg/webp/gif/pdf)")
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}


def build_body(model: str, prompt: str, parts: list[dict], max_tokens: int) -> dict:
    """OpenRouter chat body — text part first, then attachments (the documented order)."""
    return {"model": model,
            "messages": [{"role": "user",
                          "content": [{"type": "text", "text": prompt}, *parts]}],
            "max_tokens": max_tokens}


def ask(model: str, prompt: str, parts: list[dict], key: str, max_tokens: int,
        timeout: int = 180) -> dict:
    import httpx

    resp = httpx.post(API_URL, json=build_body(model, prompt, parts, max_tokens),
                      headers={"Authorization": f"Bearer {key}"}, timeout=timeout)
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code} from {model}: {resp.text[:300]}")
    data = resp.json()
    try:
        text = data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        raise RuntimeError(f"malformed response from {model}: {json.dumps(data)[:300]}")
    usage = data.get("usage") or {}
    return {"model": model, "text": text.strip(),
            "usage": {"in": usage.get("prompt_tokens", 0),
                      "out": usage.get("completion_tokens", 0)}}


def run(sources: list[str], prompt: str, task: str, override: str | None,
        max_tokens: int) -> dict:
    key = os.environ.get(KEY_VAR, "").strip()
    if not key:
        raise RuntimeError(f"{KEY_VAR} is not set — add an OpenRouter key under that name "
                           "in Settings → Secrets (kept separate from the endpoints' key "
                           "on purpose: util billing is opt-in)")
    if any(s.lower().endswith(".pdf") for s in sources) and task != "pdf":
        task = "pdf"   # PDFs need a file-input model regardless of the asked task
    primary, fallback = models_for(task, override)
    parts = [part_for(s) for s in sources]
    try:
        return ask(primary, prompt, parts, key, max_tokens)
    except RuntimeError as exc:
        if fallback == primary or "HTTP 429" not in str(exc):
            raise
        print(f"note: {primary} is rate-limited — retrying on {fallback}", file=sys.stderr)
        return ask(fallback, prompt, parts, key, max_tokens)


PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")


def selftest() -> int:
    # task routing: defaults, override, unknown
    assert models_for("describe") == TASKS["describe"]
    assert models_for("ocr")[0].endswith(":free") and not models_for("ocr")[1].endswith(":free")
    assert models_for("chart", "my/model") == ("my/model", "my/model")
    try:
        models_for("nope")
        raise AssertionError("unknown task must raise")
    except ValueError:
        pass
    # content parts: URL pass-through, image data URI, pdf file part, unsupported type
    assert part_for("https://x.test/a.png") == {
        "type": "image_url", "image_url": {"url": "https://x.test/a.png"}}
    img = part_for("shot.png", data=PNG_1PX)
    assert img["type"] == "image_url" and img["image_url"]["url"].startswith(
        "data:image/png;base64,iVBOR"), img
    pdf = part_for("doc.pdf", data=b"%PDF-1.4 fake")
    assert pdf["type"] == "file" and pdf["file"]["filename"] == "doc.pdf" \
        and pdf["file"]["file_data"].startswith("data:application/pdf;base64,"), pdf
    try:
        part_for("notes.txt", data=b"hi")
        raise AssertionError("unsupported type must raise")
    except ValueError:
        pass
    # body shape: text part FIRST, then attachments; model + max_tokens forwarded
    body = build_body("m/x", "what is this?", [img], 512)
    content = body["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": "what is this?"} and content[1] is img
    assert body["model"] == "m/x" and body["max_tokens"] == 512
    # missing key is a clean error naming the secret
    os.environ.pop(KEY_VAR, None)
    try:
        run(["https://x.test/a.png"], "p", "describe", None, 64)
        raise AssertionError("missing key must raise")
    except RuntimeError as exc:
        assert KEY_VAR in str(exc)
    print("selftest: ok", file=sys.stderr)
    return 0


def main() -> int:
    p = argparse.ArgumentParser(
        prog="gu vision",
        description="Forward images/PDFs + a question to a cloud vision model (OpenRouter).")
    p.add_argument("sources", nargs="*", metavar="FILE_OR_URL",
                   help="image files, https image URLs, or PDF files")
    p.add_argument("--prompt", default="", help="what to ask about the input(s)")
    p.add_argument("--task", default="describe", choices=sorted(TASKS),
                   help="picks the model: describe=general VQA/captioning, ocr=read text, "
                        "chart=figures/tables, ui=screenshots, pdf=documents (default: describe)")
    p.add_argument("--model", default=None, help="override with any OpenRouter vision model id")
    p.add_argument("--max-tokens", type=int, default=2048, help="answer budget (default 2048)")
    p.add_argument("--json", action="store_true", help="structured JSON on stdout")
    p.add_argument("--selftest", action="store_true")
    args = p.parse_args()
    if args.selftest:
        return selftest()
    if not args.sources:
        p.error("provide at least one image/PDF path or URL")
    if not args.prompt.strip():
        p.error("provide --prompt (what should the model look for?)")
    try:
        result = run(args.sources, args.prompt.strip(), args.task, args.model, args.max_tokens)
    except Exception as exc:  # network / file / key errors → clean nonzero exit
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(result["text"])
        print(f"[{result['model']} · {result['usage']['in']} in / "
              f"{result['usage']['out']} out tok]", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
