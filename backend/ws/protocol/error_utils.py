from __future__ import annotations

from typing import Any

from backend.ws.session.transport_contracts import SendControl


def make_error_payload(*, code: str, message: str, retriable: bool) -> dict[str, Any]:
    return {
        "code": code,
        "message": message,
        "retriable": retriable,
    }


async def send_error(
    send_control: SendControl,
    *,
    code: str,
    message: str,
    retriable: bool,
    fallback_session_id: str | None = None,
    extra_payload: dict[str, Any] | None = None,
) -> None:
    payload = make_error_payload(
        code=code,
        message=message,
        retriable=retriable,
    )
    if extra_payload:
        payload.update(extra_payload)
    kwargs: dict[str, Any] = {}
    if fallback_session_id is not None:
        kwargs["fallback_session_id"] = fallback_session_id
    await send_control(
        "error",
        payload,
        **kwargs,
    )
