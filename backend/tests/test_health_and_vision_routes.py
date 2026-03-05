from __future__ import annotations

import asyncio

import httpx
from fastapi.testclient import TestClient

from backend.app import app


class _CompatClient:
    """Use FastAPI TestClient when available; otherwise fallback to ASGI transport."""

    def __init__(self) -> None:
        try:
            self._client = TestClient(app)
            self._mode = "sync"
        except TypeError:
            self._mode = "asgi"
            self._transport = httpx.ASGITransport(app=app)

    def get(self, path: str):
        if self._mode == "sync":
            return self._client.get(path)
        return asyncio.run(self._async_request("GET", path))

    def post(self, path: str, json: dict[str, object]):
        if self._mode == "sync":
            return self._client.post(path, json=json)
        return asyncio.run(self._async_request("POST", path, json=json))

    async def _async_request(self, method: str, path: str, json: dict[str, object] | None = None):
        async with httpx.AsyncClient(
            transport=self._transport,
            base_url="http://testserver",
        ) as client:
            return await client.request(method, path, json=json)


client = _CompatClient()


def test_healthz_route() -> None:
    response = client.get("/healthz")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "loopa-mock-backend"
    assert body["ws_path"] == "/ws/session"
    assert isinstance(body["model"], str)
    assert body["model"]


def test_vision_frame_route_accepts_frame_id() -> None:
    response = client.post("/vision/frame", json={"frame_id": "frame_1"})

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "frame_id": "frame_1"}


def test_vision_frame_route_allows_missing_frame_id() -> None:
    response = client.post("/vision/frame", json={})

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "frame_id": None}
