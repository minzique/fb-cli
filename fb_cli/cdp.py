"""Small stdlib Chrome DevTools Protocol client used by fb-cli.

The project intentionally avoids Playwright/Selenium runtime dependencies. This
module carries just enough websocket + target discovery code for managed Chrome
auth import and browser-backed Marketplace commands.
"""
from __future__ import annotations

import base64
import json
import os
import select
import socket
import ssl
import struct
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


class CDPError(RuntimeError):
    """Chrome DevTools Protocol operation failed."""


@dataclass(frozen=True)
class Target:
    id: str
    url: str
    title: str
    websocket_url: str


def list_targets(debug_url: str) -> list[Target]:
    url = debug_url.rstrip("/") + "/json"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            raw = resp.read().decode("utf-8")
    except OSError as e:
        raise CDPError(
            f"Could not connect to Chrome DevTools at {debug_url}. "
            "Run `fb-cli auth chrome start`."
        ) from e

    data = json.loads(raw)
    targets: list[Target] = []
    for item in data:
        ws = item.get("webSocketDebuggerUrl")
        if item.get("type") == "page" and ws:
            targets.append(
                Target(
                    id=str(item.get("id", "")),
                    url=str(item.get("url", "")),
                    title=str(item.get("title", "")),
                    websocket_url=str(ws),
                )
            )
    if not targets:
        raise CDPError("Chrome DevTools has no page targets. Open a browser tab and retry.")
    return targets


def choose_target(targets: list[Target], *, prefer_marketplace: bool = True) -> Target:
    def score(t: Target) -> tuple[int, int]:
        url = t.url.lower()
        marketplace = 3 if prefer_marketplace and "facebook.com/marketplace" in url else 0
        facebook = 2 if "facebook.com" in url else 0
        non_chrome = 1 if not url.startswith("chrome://") else 0
        return (marketplace or facebook, non_chrome)

    return max(targets, key=score)


class CDPClient:
    def __init__(self, websocket_url: str) -> None:
        self.websocket_url = websocket_url
        self._sock = self._connect(websocket_url)
        self._next_id = 0

    def __enter__(self) -> "CDPClient":
        return self

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        self.close()

    def close(self) -> None:
        try:
            self._send_frame(b"", opcode=0x8)
        except OSError:
            pass
        try:
            self._sock.close()
        except OSError:
            pass

    def call(self, method: str, params: dict[str, Any] | None = None, *, timeout: float = 5) -> dict[str, Any]:
        self._next_id += 1
        msg_id = self._next_id
        payload: dict[str, Any] = {"id": msg_id, "method": method}
        if params is not None:
            payload["params"] = params
        self._send_json(payload)
        import time

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            msg = self.recv_json(timeout=max(0.1, min(1.0, deadline - time.monotonic())))
            if not msg:
                continue
            if msg.get("id") != msg_id:
                continue
            if "error" in msg:
                raise CDPError(f"CDP {method} failed: {msg['error']}")
            return msg
        raise CDPError(f"Timed out waiting for CDP {method}")

    def recv_json(self, *, timeout: float) -> dict[str, Any] | None:
        ready, _, _ = select.select([self._sock], [], [], timeout)
        if not ready:
            return None
        data = self._recv_message()
        if data is None:
            return None
        return json.loads(data.decode("utf-8"))

    def _send_json(self, payload: dict[str, Any]) -> None:
        self._send_frame(json.dumps(payload, separators=(",", ":")).encode("utf-8"), opcode=0x1)

    @staticmethod
    def _connect(websocket_url: str) -> socket.socket:
        parsed = urllib.parse.urlparse(websocket_url)
        if parsed.scheme not in ("ws", "wss"):
            raise CDPError(f"Unsupported websocket URL: {websocket_url}")
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or (443 if parsed.scheme == "wss" else 80)
        raw_sock = socket.create_connection((host, port), timeout=5)
        sock = ssl.create_default_context().wrap_socket(raw_sock, server_hostname=host) if parsed.scheme == "wss" else raw_sock
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        sock.sendall(request.encode("ascii"))
        response = b""
        while b"\r\n\r\n" not in response:
            chunk = sock.recv(4096)
            if not chunk:
                break
            response += chunk
        if b" 101 " not in response.split(b"\r\n", 1)[0]:
            raise CDPError(f"Chrome rejected websocket handshake: {response[:200]!r}")
        return sock

    def _send_frame(self, payload: bytes, *, opcode: int) -> None:
        # Client websocket frames must be masked.
        first = 0x80 | opcode
        length = len(payload)
        if length < 126:
            header = struct.pack("!BB", first, 0x80 | length)
        elif length < 65536:
            header = struct.pack("!BBH", first, 0x80 | 126, length)
        else:
            header = struct.pack("!BBQ", first, 0x80 | 127, length)
        mask = os.urandom(4)
        masked = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
        self._sock.sendall(header + mask + masked)

    def _recv_message(self) -> bytes | None:
        chunks: list[bytes] = []
        while True:
            fin, opcode, payload = self._recv_frame()
            if opcode == 0x8:  # close
                return None
            if opcode == 0x9:  # ping
                self._send_frame(payload, opcode=0xA)
                continue
            if opcode in (0x1, 0x2, 0x0):
                chunks.append(payload)
                if fin:
                    return b"".join(chunks)

    def _recv_frame(self) -> tuple[bool, int, bytes]:
        header = self._recv_exact(2)
        first, second = header[0], header[1]
        fin = bool(first & 0x80)
        opcode = first & 0x0F
        masked = bool(second & 0x80)
        length = second & 0x7F
        if length == 126:
            length = struct.unpack("!H", self._recv_exact(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self._recv_exact(8))[0]
        mask = self._recv_exact(4) if masked else b""
        payload = self._recv_exact(length) if length else b""
        if masked:
            payload = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
        return fin, opcode, payload

    def _recv_exact(self, size: int) -> bytes:
        data = b""
        while len(data) < size:
            chunk = self._sock.recv(size - len(data))
            if not chunk:
                raise CDPError("Chrome DevTools websocket closed unexpectedly")
            data += chunk
        return data
