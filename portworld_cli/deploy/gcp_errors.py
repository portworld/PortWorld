from __future__ import annotations


def gcp_error_message(error: object | None, fallback: str) -> str:
    message = getattr(error, "message", None)
    if isinstance(message, str) and message.strip():
        return message
    return fallback


def gcp_error_action(error: object | None, fallback: str) -> str:
    action = getattr(error, "action", None)
    if isinstance(action, str) and action.strip():
        return action
    return fallback
