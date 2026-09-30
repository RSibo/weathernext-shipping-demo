"""evaluate_departure_delay: same route, later departure. Zero Routes API calls — the route is
fixed; only the ETAs (and therefore which forecast hours each point sees) shift."""

from __future__ import annotations

from datetime import timedelta
from typing import Sequence

from schemas import DelayAnalysis, DelayOption, RoutePlan, Shipment
from tools.assess import assess_route
from tools.cube import ForecastCube

# +12 h matters: a Wyoming wind event often lasts a whole day, and 'hold until tonight' is a real
# dispatcher answer.
DEFAULT_SHIFTS_H = (2, 4, 6, 8, 12)


def evaluate_departure_delay(
    shipment: Shipment,
    plan: RoutePlan,
    cube: ForecastCube,
    shifts_h: Sequence[int] = DEFAULT_SHIFTS_H,
) -> DelayAnalysis:
    options = []
    for h in shifts_h:
        depart = shipment.origin.eta + timedelta(hours=h)
        a = assess_route(plan, depart, cube, shipment.cargo_description)
        # A route that runs past the forecast horizon is unknown, and unknown is never clean.
        verdict = "beyond_forecast" if a.verdict == "unmonitored" else a.verdict
        options.append(
            DelayOption(
                option_id=f"delay:+{h}h",
                shift_hours=h,
                depart_at=depart,
                arrive_at=a.arrive_at,
                verdict=verdict,
                peak_wind_mph=a.peak_wind_mph,
                meets_deadline=(a.arrive_at <= shipment.delivery_deadline) if shipment.delivery_deadline else None,
            )
        )
    earliest = next((o for o in options if o.verdict in ("clean", "at_risk")), None)
    return DelayAnalysis(
        shipment_id=shipment.shipment_id,
        deadline=shipment.delivery_deadline,
        options=options,
        earliest_safe_id=earliest.option_id if earliest else None,
    )
