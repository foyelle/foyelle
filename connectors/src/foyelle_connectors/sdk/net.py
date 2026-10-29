"""Outgoing connections for protocols that are not HTTP (IMAP, SMTP).

A connector reaches the internet only through its own egress proxy (HTTPS_PROXY), which allows
named hosts on listed ports. HTTP clients use the proxy by themselves; for IMAP and SMTP this
opens an HTTP CONNECT tunnel to host:port through it, and TLS then runs end to end with the
server inside the tunnel. Without a proxy configured (development, tests), it connects directly.
"""

from __future__ import annotations

import os
import socket
from urllib.parse import urlsplit

MAX_REPLY = 16 * 1024


class TunnelError(OSError):
    """The egress proxy refused or could not open the connection."""


def _proxy() -> tuple[str, int] | None:
    raw = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    if not raw:
        return None
    u = urlsplit(raw if "://" in raw else f"http://{raw}")
    if u.scheme != "http" or not u.hostname:
        raise TunnelError(f"unsupported egress proxy {raw!r}: expected http://host:port")
    return u.hostname, u.port or 3128


def _bypass(host: str) -> bool:
    entries = (os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or "").split(",")
    host = host.lower().rstrip(".")
    for entry in (e.strip().lower().lstrip(".") for e in entries):
        if entry and (entry == "*" or host == entry or host.endswith("." + entry)):
            return True
    return False


def connect(host: str, port: int, timeout: float | None = None) -> socket.socket:
    """A TCP connection to host:port, through the egress proxy when there is one."""
    proxy = _proxy()
    if proxy is None or _bypass(host):
        return socket.create_connection((host, port), timeout=timeout)
    if any(c in host for c in "\r\n /@"):
        raise TunnelError(f"invalid host name {host!r}")
    sock = socket.create_connection(proxy, timeout=timeout)
    try:
        target = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
        sock.sendall(f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n\r\n".encode("ascii"))
        reply = b""
        while b"\r\n\r\n" not in reply:
            chunk = sock.recv(1024)
            if not chunk:
                raise TunnelError("the egress proxy closed the connection")
            reply += chunk
            if len(reply) > MAX_REPLY:
                raise TunnelError("the egress proxy sent an oversized reply")
        head, _, rest = reply.partition(b"\r\n\r\n")
        status = head.split(b"\r\n", 1)[0].split()
        if len(status) < 2 or status[1] != b"200":
            code = status[1].decode("ascii", "replace") if len(status) > 1 else "?"
            raise TunnelError(f"the egress proxy refused {host}:{port} ({code})")
        if rest:
            # IMAP and SMTP servers speak first, so a greeting can arrive with the proxy's reply.
            return _Prefixed(sock, rest)
        return sock
    except BaseException:
        sock.close()
        raise


class _Prefixed(socket.socket):
    """A socket that first returns bytes already read past the proxy's reply."""

    def __init__(self, sock: socket.socket, pending: bytes) -> None:
        timeout = sock.gettimeout()
        super().__init__(sock.family, sock.type, sock.proto, fileno=sock.detach())
        self.settimeout(timeout)
        self._pending = pending

    def recv(self, bufsize: int, flags: int = 0) -> bytes:
        if self._pending:
            out, self._pending = self._pending[:bufsize], self._pending[bufsize:]
            return out
        return super().recv(bufsize, flags)

    def recv_into(self, buffer, nbytes: int = 0, flags: int = 0) -> int:  # type: ignore[no-untyped-def]
        # makefile() (which imaplib and smtplib read through) uses recv_into, not recv.
        if self._pending:
            n = min(nbytes or len(buffer), len(buffer), len(self._pending))
            buffer[:n] = self._pending[:n]
            self._pending = self._pending[n:]
            return n
        return super().recv_into(buffer, nbytes, flags)
