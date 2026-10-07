#!/usr/bin/env python3
"""An OpenAI-Decisions-compatible endpoint over a local vision-language GGUF (`POST /v1/decisions`).

routine-scheduler's `decide` action speaks two decision protocols (docs/decision-models.md); this
serves the one that carries images, so the model measured on predator — Qwen3-VL-8B-Instruct
Q4_K_M + its mmproj, read the SemIf way — becomes a catalog entry like OpenAI's own, several
images per request included. Nothing is generated: each question's prompt is evaluated and the
logits at the LAST position are softmaxed over only the declared answer-slot letters
(`decision_protocol.py`). Reading `create_chat_completion(logprobs=...)` instead returns
logprobs at the wrong positions and once produced a 0.95-confidence WRONG answer (R2254).

Three properties the hosting depends on:

* The model loads on the first request and UNLOADS after `--idle-unload-s` without one. The box
  is an exclusive GPU machine whose training jobs need the card; a resident 4.5 GB server would
  make every one of them run out of VRAM. Loading takes seconds from the page cache.
* GPU when there is room, CPU when there is not: free VRAM is read before each load, and a card
  another job holds gets the CPU path (slower, same answers) instead of an allocation failure.
  The vision encoder ALWAYS runs on the CPU — on CUDA it segfaults in the cu124 0.3.36 wheel at
  every image size (R2258), and a native crash has no Python exception to fall back from.
* Questions over one input share its evaluation: the images and the evidence text are decoded
  once and kept in the KV cache, and each question evaluates only its own suffix. Encoding the
  images is most of the cost, so six questions over five photos cost about one, not six.
"""
from __future__ import annotations

import argparse
import ctypes
import hmac
import json
import logging
import os
import secrets
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import decision_protocol as proto

log = logging.getLogger("decision-server")
#: Request bodies past this are refused before parsing — 128 photos at phone resolution.
BODY_MAX = 256 * 1024 * 1024
#: ggml's level for an error; everything below it is chatter.
GGML_LOG_ERROR = 4
_native_log_hooks: list = []   # the ctypes callbacks must outlive every native call


def quiet_native_logs() -> None:
    """Route llama.cpp's and mtmd's own logging to errors only.

    mtmd logs every prompt it tokenizes at debug level (`add_text: …`) straight to stderr,
    which under systemd is the journal — each question, its evidence and its options, kept on
    disk by a service that never meant to record them.
    """
    import llama_cpp
    from llama_cpp import mtmd_cpp

    @llama_cpp.llama_log_callback
    def forward(level: int, text: bytes, _user: object) -> None:
        if level >= GGML_LOG_ERROR:
            log.error("native: %s", text.decode("utf-8", "replace").rstrip())

    _native_log_hooks.append(forward)
    llama_cpp.llama_log_set(forward, None)
    mtmd_cpp.mtmd_log_set(forward, None)
    mtmd_cpp.mtmd_helper_log_set(forward, None)


def prepare_image(raw: bytes, where: str, max_px: int) -> bytes:
    """`raw` as JPEG bytes, upright and downscaled so its longest side is at most `max_px`.

    Vision tokens grow with area, so the longest side is the one knob that moves speed; the
    benchmarked runs used 512. EXIF orientation is applied first — a phone photo stored sideways
    is otherwise judged sideways.
    """
    import io

    from PIL import Image, ImageOps, UnidentifiedImageError
    try:
        with Image.open(io.BytesIO(raw)) as src:
            img = ImageOps.exif_transpose(src).convert("RGB")
    except (UnidentifiedImageError, OSError) as exc:
        raise proto.BadRequestError(f"{where} could not be decoded ({exc})", "input") from exc
    if max_px and max(img.size) > max_px:
        img.thumbnail((max_px, max_px))
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=88)
    return out.getvalue()


class Engine:
    """One lazily loaded VLM + projector, answering decision requests one at a time."""

    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.lock = threading.Lock()
        self.llm = None
        self.handler = None
        self.device = "none"
        self.last_used = 0.0
        self.slots: list[int] = []
        # The KV cache's content, as chunk signatures with the position each one ends at — what
        # lets the next question skip what this one already evaluated.
        self.cached: list[tuple[tuple, int]] = []

    def _free_vram_mib(self) -> int:
        try:
            out = subprocess.run(["nvidia-smi", "--query-gpu=memory.free",
                                  "--format=csv,noheader,nounits"],
                                 capture_output=True, text=True, timeout=10, check=True)
            return int(out.stdout.split()[0])
        except (OSError, subprocess.SubprocessError, ValueError, IndexError):
            return 0

    def _load(self) -> None:
        from llama_cpp import Llama
        from llama_cpp.llama_chat_format import MTMDChatHandler
        a = self.args
        free = self._free_vram_mib()
        layers = a.n_gpu_layers if a.n_gpu_layers and free >= a.min_free_vram_mib else 0
        started = time.perf_counter()
        self.handler = MTMDChatHandler(clip_model_path=a.mmproj, verbose=False, use_gpu=False)
        try:
            self.llm = Llama(model_path=a.gguf, chat_handler=self.handler, n_ctx=a.ctx,
                             n_batch=a.batch, n_threads=a.threads, n_gpu_layers=layers,
                             verbose=False)
        except ValueError:
            if not layers:
                raise
            # The card filled up between the check and the allocation (a job started): the
            # CPU path answers the same question, only slower.
            log.warning("GPU load failed; loading on the CPU instead")
            layers = 0
            self.llm = Llama(model_path=a.gguf, chat_handler=self.handler, n_ctx=a.ctx,
                             n_batch=a.batch, n_threads=a.threads, n_gpu_layers=0,
                             verbose=False)
        self.handler._init_mtmd_context(self.llm)
        quiet_native_logs()   # after the load: Llama(verbose=False) installs its own hook
        self.slots = []
        for letter in proto.LETTERS:
            enc = self.llm.tokenize(letter.encode(), add_bos=False, special=False)
            if len(enc) != 1:
                raise RuntimeError(f"answer slot {letter!r} is not one token")
            self.slots.append(enc[0])
        if len(set(self.slots)) != len(self.slots):
            raise RuntimeError("answer-slot tokens collide")
        self.device = "cuda" if layers else "cpu"
        self.cached = []
        log.info("loaded %s on %s (free VRAM %s MiB) in %.1fs", Path(a.gguf).name, self.device,
                 free, time.perf_counter() - started)

    def unload_if_idle(self) -> None:
        """Free the weights once nothing has asked for `--idle-unload-s` (the card is shared)."""
        with self.lock:
            if self.llm is not None and time.time() - self.last_used > self.args.idle_unload_s:
                self.llm.close()
                self.llm = self.handler = None
                self.device, self.cached = "none", []
                log.info("unloaded after %ss idle", self.args.idle_unload_s)

    def _chunks(self, system: str, payload: str, bitmaps: list) -> list[tuple[tuple, object]]:
        """The prompt as mtmd chunks: the model's own chat template, images before the payload."""
        mt = self.handler._mtmd_cpp
        marker = mt.mtmd_default_marker().decode("utf-8")
        parts = [{"type": "text", "text": marker} for _ in bitmaps]
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": [*parts, {"type": "text", "text": payload}]}]
        # Rendered exactly as MTMDChatHandler renders it (llama-cpp-python 0.3.36) — the path
        # the benchmarked runs took — so the prompt the model scores is the measured one.
        import jinja2.ext
        from jinja2.sandbox import ImmutableSandboxedEnvironment
        from llama_cpp.llama_chat_format import Jinja2ChatFormatter as Fmt
        env = ImmutableSandboxedEnvironment(
            trim_blocks=True, lstrip_blocks=True,
            extensions=[Fmt.IgnoreGenerationTags, jinja2.ext.loopcontrols])
        env.filters["tojson"] = Fmt.tojson

        def raise_exception(message: str) -> None:
            raise ValueError(message)

        def piece(tok: int) -> str:
            return self.handler._decode_token_piece(self.llm.detokenize([tok]))

        text = env.from_string(self.handler._get_chat_template(self.llm)).render(
            messages=messages, add_generation_prompt=True, raise_exception=raise_exception,
            eos_token=piece(self.llm.token_eos()), bos_token=piece(self.llm.token_bos()),
            strftime_now=Fmt.strftime_now)
        inp = mt.mtmd_input_text()
        raw = text.encode("utf-8")
        inp.text, inp.text_len, inp.add_special, inp.parse_special = raw, len(raw), True, True
        chunks = mt.mtmd_input_chunks_init()
        arr = (mt.mtmd_bitmap_p_ctypes * len(bitmaps))(*bitmaps)
        if mt.mtmd_tokenize(self.handler.mtmd_ctx, chunks, ctypes.byref(inp), arr,
                            len(bitmaps)) != 0:
            mt.mtmd_input_chunks_free(chunks)
            raise RuntimeError("mtmd could not tokenize the prompt")
        out = []
        for i in range(mt.mtmd_input_chunks_size(chunks)):
            chunk = mt.mtmd_input_chunks_get(chunks, i)
            if mt.mtmd_input_chunk_get_type(chunk) == mt.MTMD_INPUT_CHUNK_TYPE_TEXT:
                n = ctypes.c_size_t()
                ptr = mt.mtmd_input_chunk_get_tokens_text(chunk, ctypes.byref(n))
                out.append((("text", tuple(ptr[j] for j in range(n.value))), chunk))
            else:
                image_idx = sum(1 for sig, _ in out if sig[0] == "image")
                out.append((("image", image_idx, mt.mtmd_input_chunk_get_n_tokens(chunk)), chunk))
        return [*out, (("free",), chunks)]

    def _evaluate(self, chunks: list[tuple[tuple, object]]) -> int:
        """Bring the KV cache to `chunks`, re-using the longest prefix it already holds.

        Returns how many positions were evaluated anew (the request's billable input).
        """
        import llama_cpp
        mt, llm = self.handler._mtmd_cpp, self.llm
        # KV CELLS, not positions: Qwen-VL's M-RoPE advances an image by fewer positions than
        # the tokens it stores, so a position-based check passes a prompt the cache cannot hold
        # and mtmd fails a minute into the encode instead of before it.
        cells = sum(len(sig[1]) if sig[0] == "text" else sig[2] for sig, _ in chunks)
        if cells > llm.n_ctx():
            raise proto.BadRequestError(
                f"the input needs {cells} context tokens and this server holds {llm.n_ctx()}; "
                "send fewer images or split the request", "input")
        # Whole chunks first — never the last one, which always ends in the generation prompt
        # and must be evaluated (at least its final token) for its logits to be the ones read.
        keep, pos = 0, 0
        while (keep < len(chunks) - 1 and keep < len(self.cached)
               and chunks[keep][0] == self.cached[keep][0]):
            pos = self.cached[keep][1]
            keep += 1
        fresh, new_cache = 0, self.cached[:keep]
        for idx in range(keep, len(chunks)):
            sig, chunk = chunks[idx]
            if sig[0] == "text":
                tokens = list(sig[1])
                common = 0
                # …then the token prefix of the first chunk that differs: the evidence text
                # leads every payload, so it is what questions over one input share.
                if idx == keep and idx < len(self.cached) and self.cached[idx][0][0] == "text":
                    old, limit = self.cached[idx][0][1], len(tokens) - 1
                    while common < min(len(old), limit) and old[common] == tokens[common]:
                        common += 1
                llm.n_tokens = pos + common
                llm.eval(tokens[common:])
                fresh += len(tokens) - common
                pos += len(tokens)
            else:
                llm._ctx.kv_cache_seq_rm(0, pos, -1)
                new_pos = llama_cpp.llama_pos(0)
                if mt.mtmd_helper_eval_chunk_single(
                        self.handler.mtmd_ctx, llm._ctx.ctx, chunk, llama_cpp.llama_pos(pos),
                        llama_cpp.llama_seq_id(0), llm.n_batch, False,
                        ctypes.byref(new_pos)) != 0:
                    raise RuntimeError("mtmd failed to evaluate an image chunk")
                fresh += sig[2]
                pos = llm.n_tokens = new_pos.value
            new_cache.append((sig, pos))
        self.cached = new_cache
        return fresh

    def _read_slots(self, count: int) -> list[float]:
        import llama_cpp
        ptr = llama_cpp.llama_get_logits_ith(self.llm._ctx.ctx, -1)
        if not ptr:
            raise RuntimeError("llama.cpp returned no logits for the final position")
        buf = ctypes.cast(ptr, ctypes.POINTER(ctypes.c_float * self.llm.n_vocab())).contents
        return [float(buf[t]) for t in self.slots[:count]]

    def decide(self, req: proto.DecisionRequest) -> dict:
        """Answer every question of `req`; the model is loaded first if it is not resident."""
        # Decoded before the lock: a bad image is the caller's 400, and must not wait for (or
        # load) a model to be told so.
        jpegs = [prepare_image(raw, f"image {i}", self.args.max_px)
                 for i, raw in enumerate(req.images)]
        with self.lock:
            if self.llm is None:
                self._load()
            started = time.perf_counter()
            mt = self.handler._mtmd_cpp
            bitmaps: list = []
            answers, fresh = [], 0
            try:
                # extend() appends as it goes, so a bitmap made before a failure is still freed
                bitmaps.extend(self.handler._create_bitmap_from_bytes(j) for j in jpegs)
                system = proto.system_for(len(bitmaps))
                for q in req.questions:
                    chunks = self._chunks(system, proto.payload_text(req.evidence, len(bitmaps),
                                                                     q), bitmaps)
                    try:
                        fresh += self._evaluate(chunks[:-1])
                        answers.append(proto.answer(q, self._read_slots(len(q.options))))
                    finally:
                        mt.mtmd_input_chunks_free(chunks[-1][1])
            except Exception:
                self.cached = []
                raise
            finally:
                for bm in bitmaps:
                    mt.mtmd_bitmap_free(bm)
                self.last_used = time.time()
            # A cached image chunk is only valid for the bitmaps that made it: the next request
            # brings its own, so only a text-only prefix may be carried across requests.
            if req.images:
                self.cached = []
            return {"answers": answers, "usage": {"input_tokens": fresh, "output_tokens": 0,
                                                  "total_tokens": fresh},
                    "x_runtime": {"device": self.device, "images": len(req.images),
                                  "seconds": round(time.perf_counter() - started, 3)}}


def make_handler(engine: Engine, token: str, model_name: str) -> type[BaseHTTPRequestHandler]:
    """The HTTP surface: `/health`, `/v1/models` and `POST /v1/decisions`."""

    class Handler(BaseHTTPRequestHandler):
        server_version = "decision-server/1"

        def log_message(self, fmt: str, *args: object) -> None:
            log.info("%s %s", self.address_string(), fmt % args)

        def _send(self, code: int, body: dict) -> None:
            raw = json.dumps(body, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _error(self, code: int, message: str, kind: str, param: str = "") -> None:
            self._send(code, {"error": {"message": message, "type": kind,
                                        "param": param or None, "code": None}})

        def _authorized(self) -> bool:
            if not token:
                return True
            got = self.headers.get("Authorization", "").removeprefix("Bearer ").strip()
            if hmac.compare_digest(got.encode(), token.encode()):
                return True
            self._error(401, "invalid or missing bearer token", "authentication_error")
            return False

        def do_GET(self) -> None:
            if self.path == "/health":
                self._send(200, {"ok": True, "model": model_name, "device": engine.device})
            elif self.path.rstrip("/") != "/v1/models":
                self._error(404, f"no route {self.path}", "invalid_request_error")
            elif self._authorized():
                self._send(200, {"object": "list", "data": [{
                    "id": model_name, "object": "model", "owned_by": "local",
                    "context_length": engine.args.ctx, "input_modalities": ["text", "image"],
                    "max_images": engine.args.max_images}]})

        def do_POST(self) -> None:
            if self.path.rstrip("/") != "/v1/decisions":
                self._error(404, f"no route {self.path}", "invalid_request_error")
                return
            if not self._authorized():
                return
            length = int(self.headers.get("Content-Length") or 0)
            if not 0 < length <= BODY_MAX:
                self._error(413, f"body must be 1..{BODY_MAX} bytes", "invalid_request_error")
                return
            try:
                body = json.loads(self.rfile.read(length))
                if body.get("model") not in (None, "", model_name):
                    raise proto.BadRequestError(f"this server serves only {model_name!r}", "model")
                req = proto.parse_request(body, max_images=engine.args.max_images,
                                          max_questions=engine.args.max_questions)
                out = engine.decide(req)
            except proto.BadRequestError as exc:
                self._error(400, str(exc), "invalid_request_error", exc.param)
                return
            except (json.JSONDecodeError, AttributeError) as exc:
                self._error(400, f"body is not a JSON object ({exc})", "invalid_request_error")
                return
            except Exception as exc:
                log.exception("decision failed")
                self._error(500, f"{type(exc).__name__}: {exc}", "server_error")
                return
            self._send(200, {"id": f"dec_{secrets.token_hex(12)}", "object": "decision",
                             "created": int(time.time()), "model": model_name, **out})

    return Handler


def main() -> int:
    """Parse flags, then serve until stopped."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--gguf", required=True)
    ap.add_argument("--mmproj", required=True)
    ap.add_argument("--model-name", default="qwen3-vl-8b-instruct")
    ap.add_argument("--host", default="0.0.0.0")  # noqa: S104 — a LAN service behind a bearer token
    ap.add_argument("--port", type=int, default=8790)
    ap.add_argument("--token-file", default="", help="bearer token file (empty = no auth)")
    ap.add_argument("--ctx", type=int, default=8192)
    ap.add_argument("--batch", type=int, default=512)
    ap.add_argument("--threads", type=int, default=os.cpu_count() or 4)
    ap.add_argument("--n-gpu-layers", type=int, default=99)
    ap.add_argument("--min-free-vram-mib", type=int, default=5600)
    ap.add_argument("--max-px", type=int, default=512)
    ap.add_argument("--max-images", type=int, default=16)
    ap.add_argument("--max-questions", type=int, default=16)
    ap.add_argument("--idle-unload-s", type=int, default=180)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    token = Path(args.token_file).read_text().strip() if args.token_file else ""
    engine = Engine(args)

    def reaper() -> None:
        while True:
            time.sleep(15)
            engine.unload_if_idle()

    threading.Thread(target=reaper, daemon=True).start()
    server = ThreadingHTTPServer((args.host, args.port),
                                 make_handler(engine, token, args.model_name))
    log.info("serving %s on %s:%s (auth %s)", args.model_name, args.host, args.port,
             "on" if token else "OFF")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
