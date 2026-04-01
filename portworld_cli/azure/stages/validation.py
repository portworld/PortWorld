from __future__ import annotations

from time import monotonic
from urllib.parse import urlparse
import socket
import ssl

import httpx

from portworld_cli.azure.common import normalize_optional_text


def probe_livez(base_url: str) -> bool:
    try:
        response = httpx.get(f"{base_url.rstrip('/')}/livez", timeout=10.0)
    except Exception:
        return False
    return response.status_code == 200


def probe_ws(base_url: str, bearer_token: str | None) -> bool:
    parsed = urlparse(base_url)
    if parsed.scheme != "https" or parsed.hostname is None:
        return False
    host = parsed.hostname
    port = parsed.port or 443
    headers = {
        "Host": host,
        "Connection": "Upgrade",
        "Upgrade": "websocket",
        "Sec-WebSocket-Version": "13",
        "Sec-WebSocket-Key": "cG9ydHdvcmxkLWF6dXJlLXYxLTEyMzQ1",
    }
    token = normalize_optional_text(bearer_token)
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
