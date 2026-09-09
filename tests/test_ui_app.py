"""Importing ui.app executes the whole Streamlit script body (it's not guarded by
`if __name__ == "__main__"`) — Streamlit tolerates this in "bare mode" (prints
missing-ScriptRunContext warnings, doesn't fail), so importing it here to reach
_generate_simulated_weather is safe, if noisy.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from schemas import Coordinate, Shipment, Waypoint
from tools.thresholds import DRY_VAN_WIND_THRESHOLD_MPH, WIND_THRESHOLD_MPH, evaluate_breach
from ui.app import _generate_simulated_weather

_NOW = datetime.now(timezone.utc)


def _shipment(cargo_description) -> Shipment:
    return Shipment(
        shipment_id="sim-test",
        origin=Waypoint(name="Origin", location=Coordinate(lat=41.87, lon=-87.62), eta=_NOW),
        destination=Waypoint(
            name="Destination",
            location=Coordinate(lat=39.73, lon=-104.99),
            eta=_NOW + timedelta(hours=10),
        ),
        waypoints=[
            Waypoint(
                name="Midpoint",
                location=Coordinate(lat=41.52, lon=-90.57),
                eta=_NOW + timedelta(hours=3),
            )
        ],
        cargo_description=cargo_description,
    )


def test_simulated_storm_breach_uses_dry_van_threshold_for_dry_van_cargo():
    shipment = _shipment("Palletized electronics, dry van")

    result = _generate_simulated_weather(shipment, "Severe Storm & High Wind (Breach)")

    breached_obs = [o for o in result.observations if o.breached]
    assert len(breached_obs) == 1
    # Must match evaluate_breach's own output exactly — not a hand-written duplicate of it — so a
    # future change to the reason format can't silently drift between simulation and production.
    expected_breached, expected_reasons = evaluate_breach(
        wind_speed_mph=54.0,
        precip_mm_hr=32.0,
        temp_f=68.0,
        cargo_description=shipment.cargo_description,
    )
    assert breached_obs[0].reasons == expected_reasons
    assert f"exceeds {DRY_VAN_WIND_THRESHOLD_MPH:.0f}mph" in breached_obs[0].reasons[0]
    assert "dry-van rollover risk" in breached_obs[0].reasons[0]


def test_simulated_storm_breach_uses_standard_threshold_for_non_dry_van_cargo():
    shipment = _shipment("Bulk cement, flatbed")

    result = _generate_simulated_weather(shipment, "Severe Storm & High Wind (Breach)")

    breached_obs = [o for o in result.observations if o.breached]
    assert len(breached_obs) == 1
    assert f"exceeds {WIND_THRESHOLD_MPH:.0f}mph" in breached_obs[0].reasons[0]
    assert "dry-van rollover risk" not in breached_obs[0].reasons[0]


def test_simulated_clear_scenario_has_no_breaches():
    shipment = _shipment("Refrigerated produce")

    result = _generate_simulated_weather(shipment, "Clear Weather (Safe, No Breach)")

    assert result.any_breach is False
    assert all(not o.breached for o in result.observations)
    assert all(o.hazard_polygon is None for o in result.observations)


def test_simulated_black_ice_scenario_breaches_on_temperature_not_wind():
    shipment = _shipment("Bulk cement, flatbed")

    result = _generate_simulated_weather(shipment, "Black Ice Risk (Breach)")

    breached_obs = [o for o in result.observations if o.breached]
    assert len(breached_obs) == 1
    assert "Black ice risk" in breached_obs[0].reasons[0]
