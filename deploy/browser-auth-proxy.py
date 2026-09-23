#!/usr/bin/env python3
"""A bearer-token door in front of the browser's two protocols, which have none of their own.

Chrome's DevTools port and a VNC screen authenticate NOTHING: whoever reaches the socket drives
a browser holding live site sessions. The engine container shares a docker network with this
one, and `net:` in a util's header is a BOOLEAN — Landlock allows all TCP or none — so every
`net: outbound` util and every `shell` command could reach those ports directly. The
`browser-session` util is reserved behind a permission, but that gate is on the ACTION, not on
the socket, which made it decoration.

This turns reaching the browser into a CREDENTIAL, which is the authorization primitive this
system already has: a util declares `BROWSER_CDP_TOKEN` on its `secrets:` line, and the engine
injects it only if the util declares it AND the routine was granted it. The reserved-util gate
and the secret-exposure decision then both actually bind.

Why a header check and a splice rather than a real HTTP proxy: DevTools echoes back whichever
IP-literal `Host` it was dialled with, which is what makes the existing socat forward work at
all. Because this listens on the same address the client dialled and passes `Host` through
untouched, Chrome still hands out WebSocket URLs pointing here — so there is nothing to rewrite,
which is the part that usually makes a CDP proxy fragile. Both plain requests and the WebSocket
upgrade begin with an HTTP head, so one check covers both.

Fail CLOSED: with no token configured this refuses every connection rather than forwarding it.
An unauthenticated browser port that looks like it is protected is worse than one nobody
believed in.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import hmac
import os
import sys

#: Cap on the request head we buffer before deciding. Real CDP heads are a few hundred bytes;
#: anything past this is not a client we want to keep reading for.
HEAD_MAX = 65536
HEAD_END = b"\r\n\r\n"


def _log(msg: str) -> None:
    print(f"[browser-auth] {msg}", file=sys.stderr, flush=True)


def _authorized(head: bytes, token: str) -> bool:
    """True when the request head carries `Authorization: Bearer <token>`.

    Compared with `hmac.compare_digest`, like every other credential check here — a bearer
    token compared with `==` leaks its prefix to a patient caller on the same network.
    """
    if not token:
        return False
    for line in head.split(b"\r\n")[1:]:          # [0] is the request line
        name, _, value = line.partition(b":")
        if name.strip().lower() != b"authorization":
            continue
        presented = value.strip().decode("latin-1", "replace")
        scheme, _, got = presented.partition(" ")
        if scheme.lower() != "bearer":
            continue
        return hmac.compare_digest(got.strip(), token)
    return False


async def _read_head(reader: asyncio.StreamReader) -> bytes | None:
    """The bytes up to and including the blank line that ends an HTTP head, or None."""
    buf = b""
    while HEAD_END not in buf:
        if len(buf) > HEAD_MAX:
            return None
        chunk = await reader.read(4096)
        if not chunk:
            return None
        buf += chunk
    return buf


async def _splice(src: asyncio.StreamReader, dst: asyncio.StreamWriter) -> None:
    """Copy one direction until it ends. Both directions run as tasks; the first to finish
    tears the pair down, which is what closes a WebSocket cleanly from either end.
    """
    try:
        while chunk := await src.read(65536):
            dst.write(chunk)
            await dst.drain()
    except (ConnectionError, asyncio.IncompleteReadError, OSError):
        pass
    finally:
        with contextlib.suppress(OSError):
            dst.close()


def _refuse(writer: asyncio.StreamWriter, status: str, detail: str) -> None:
    body = detail.encode()
    writer.write(
        f"HTTP/1.1 {status}\r\nContent-Type: text/plain; charset=utf-8\r\n"
        f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body)


async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                  target: tuple[str, int], token: str, label: str) -> None:
    peer = writer.get_extra_info("peername")
    try:
        if not token:
            _refuse(writer, "503 Service Unavailable",
                    "the browser proxy has no BROWSER_CDP_TOKEN configured, so it refuses "
                    "every connection rather than forwarding an unauthenticated one")
            await writer.drain()
            return
        head = await _read_head(reader)
        if head is None:
            return                                  # not HTTP, or gave up mid-head
        if not _authorized(head, token):
            _log(f"{label}: refused {peer} — no valid bearer token")
            _refuse(writer, "401 Unauthorized",
                    "this browser port needs `Authorization: Bearer <BROWSER_CDP_TOKEN>`. A "
                    "util reaches it by declaring BROWSER_CDP_TOKEN on its `secrets:` line; "
                    "the routine must also be granted that secret.")
            await writer.drain()
            return
        try:
            up_r, up_w = await asyncio.open_connection(*target)
        except OSError as exc:
            _log(f"{label}: upstream {target[0]}:{target[1]} unreachable: {exc}")
            _refuse(writer, "502 Bad Gateway", f"browser upstream did not answer: {exc}")
            await writer.drain()
            return
        up_w.write(head)                            # replay the head we consumed to decide
        await up_w.drain()
        await asyncio.gather(_splice(reader, up_w), _splice(up_r, writer))
    except (ConnectionError, OSError):
        pass
    finally:
        with contextlib.suppress(OSError):
            writer.close()


async def _serve(listen: tuple[str, int], target: tuple[str, int], token: str,
                 label: str) -> None:
    server = await asyncio.start_server(
        lambda r, w: _handle(r, w, target, token, label), listen[0], listen[1])
    _log(f"{label}: {listen[0]}:{listen[1]} -> {target[0]}:{target[1]} (bearer required)")
    async with server:
        await server.serve_forever()


def _hostport(value: str) -> tuple[str, int]:
    host, _, port = value.rpartition(":")
    if not host or not port.isdigit():
        raise argparse.ArgumentTypeError(f"expected HOST:PORT, got {value!r}")
    return host, int(port)


async def _main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--map", action="append", required=True, metavar="LISTEN=TARGET:LABEL",
                    help="one listener, e.g. 0.0.0.0:9222=127.0.0.1:9223:cdp (repeatable)")
    ap.add_argument("--token-env", default="BROWSER_CDP_TOKEN")
    args = ap.parse_args()
    token = os.environ.get(args.token_env, "").strip()
    if not token:
        _log(f"{args.token_env} is empty — every connection will be refused with 503")
    jobs = []
    for spec in args.map:
        listen_s, _, rest = spec.partition("=")
        target_s, _, label = rest.rpartition(":")
        jobs.append(_serve(_hostport(listen_s), _hostport(target_s), token, label or "proxy"))
    await asyncio.gather(*jobs)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(_main()))
    except KeyboardInterrupt:
        raise SystemExit(130) from None
