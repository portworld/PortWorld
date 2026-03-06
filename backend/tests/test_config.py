from __future__ import annotations

import importlib


def test_settings_from_env_uses_valid_integer_values(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_REALTIME_UPLINK_ACK_EVERY_N_FRAMES", "42")
    monkeypatch.setenv("OPENAI_REALTIME_MANUAL_TURN_FALLBACK_DELAY_MS", "1200")
    monkeypatch.setenv("PORT", "9090")

    from backend.config import Settings

    settings = Settings.from_env()

    assert settings.openai_realtime_uplink_ack_every_n_frames == 42
    assert settings.openai_realtime_manual_turn_fallback_delay_ms == 1200
    assert settings.port == 9090


def test_settings_from_env_falls_back_for_invalid_integer_values(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_REALTIME_UPLINK_ACK_EVERY_N_FRAMES", "bad")
    monkeypatch.setenv("OPENAI_REALTIME_MANUAL_TURN_FALLBACK_DELAY_MS", "broken")
    monkeypatch.setenv("PORT", "not-a-number")

    from backend.config import Settings

    settings = Settings.from_env()

    assert settings.openai_realtime_uplink_ack_every_n_frames == 20
    assert settings.openai_realtime_manual_turn_fallback_delay_ms == 900
    assert settings.port == 8080


def test_settings_from_env_enforces_minimum_bounds(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_REALTIME_UPLINK_ACK_EVERY_N_FRAMES", "0")
    monkeypatch.setenv("OPENAI_REALTIME_MANUAL_TURN_FALLBACK_DELAY_MS", "99")

    from backend.config import Settings

    settings = Settings.from_env()

    assert settings.openai_realtime_uplink_ack_every_n_frames == 1
    assert settings.openai_realtime_manual_turn_fallback_delay_ms == 100


def test_module_import_does_not_crash_on_malformed_numeric_env(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_REALTIME_UPLINK_ACK_EVERY_N_FRAMES", "x")
    monkeypatch.setenv("OPENAI_REALTIME_MANUAL_TURN_FALLBACK_DELAY_MS", "y")
    monkeypatch.setenv("PORT", "z")

    import backend.config as config_module

    reloaded = importlib.reload(config_module)

    assert reloaded.settings.openai_realtime_uplink_ack_every_n_frames == 20
    assert reloaded.settings.openai_realtime_manual_turn_fallback_delay_ms == 900
    assert reloaded.settings.port == 8080
