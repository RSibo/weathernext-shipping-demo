"""Unit conversions for WeatherNext 3 raw fields (verified against real rows, 2026-09-14):
wind in m/s, precipitation in meters, temperature in Kelvin."""

from __future__ import annotations


def kelvin_to_fahrenheit(kelvin: float) -> float:
    return (kelvin - 273.15) * 9 / 5 + 32


def ms_to_mph(meters_per_second: float) -> float:
    return meters_per_second * 2.23694


def meters_to_mm(meters: float) -> float:
    return meters * 1000
