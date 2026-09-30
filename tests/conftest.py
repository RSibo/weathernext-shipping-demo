from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_weather_cache():
    """Several tests reuse the same waypoint name/coordinates (e.g. "clear-stop" at the same
    lat/lon across many test_weather_tool.py functions) — without this, a later test's mock
    client would never be called because an earlier test's cached result for the "same" request
    key would be returned instead, silently breaking test isolation."""
    from tools.weather import _clear_weather_cache

    _clear_weather_cache()
    yield
    _clear_weather_cache()

