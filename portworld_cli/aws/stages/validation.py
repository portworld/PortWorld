from __future__ import annotations

from time import monotonic
from urllib.parse import urlparse
import socket
import ssl
import time

import httpx


def probe_livez(service_url: str) -> bool:
    url = service_url.rstrip("/") + "/livez"
    try:
        response = httpx.get(url, timeout=15.0)
    except Exception:
        return False
    return response.status_code == 200


def probe_ws(service_url: str, bearer_token: str) -> bool:
    parsed = urlparse(service_url)
    host = parsed.hostname
    if host is None:
        return False
    port = parsed.port or 443
    headers = {
        "Host": host,
        "Connection": "Upgrade",
        "Upgrade": "websocket",
        "Sec-WebSocket-Version": "13",
        "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ==",
    }
    token = bearer_token.strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        raw_response = tls_http_get_upgrade(
            host=host,
            port=port,
            path="/ws/session",
            headers=headers,
            timeout=10.0,
        )
    except Exception:
        return False
    status_code = parse_http_status_code(raw_response)
    return status_code in {101, 401}


def wait_for_public_validation(service_url: str, bearer_token: str) -> tuple[bool, bool]:
    livez_ok = False
    ws_ok = False
    deadline = monotonic() + 300.0
    while monotonic() < deadline:
        if not livez_ok:
            livez_ok = probe_livez(service_url)
        if livez_ok and not ws_ok:
            ws_ok = probe_ws(service_url, bearer_token)
        if livez_ok and ws_ok:
            return True, True
        time.sleep(3)
    return livez_ok, ws_ok


def tls_http_get_upgrade(
    *,
    host: str,
    port: int,
    path: str,
    headers: dict[str, str],
    timeout: float,
) -> str:
    request_lines = [f"GET {path} HTTP/1.1", *(f"{key}: {value}" for key, value in headers.items()), "", ""]
    request = "\r\n".join(request_lines).encode("ascii", errors="ignore")
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    with socket.create_connection((host, port), timeout=timeout) as tcp_sock:
        with context.wrap_socket(tcp_sock, server_hostname=host) as tls_sock:
            tls_sock.settimeout(timeout)
            tls_sock.sendall(request)
            chunks: list[bytes] = []
            deadline = monotonic() + timeout
            while monotonic() < deadline:
                data = tls_sock.recv(4096)
                if not data:
                    break
                chunks.append(data)
                if b"\r\n\r\n" in b"".join(chunks):
                    break
            return b"".join(chunks).decode("iso-8859-1", errors="replace")


def parse_http_status_code(raw_response: str) -> int | None:
    status_line = raw_response.splitlines()[0] if raw_response else ""
    parts = status_line.split(" ")
    if len(parts) < 2:
        return None
    try:
        return int(parts[1])
    except ValueError:
        return None
