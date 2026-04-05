from __future__ import annotations


class ConfigRuntimeError(RuntimeError):
    """Base error for config UX runtime failures."""


class ConfigUsageError(ConfigRuntimeError):
    """Raised when config command flags or state are invalid."""


class ConfigValidationError(ConfigRuntimeError):
    """Raised when required config values are missing."""
