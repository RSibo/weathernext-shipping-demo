"""Streamlit dispatcher UI. Single-process: calls agent.loop.run_advisory_with_context directly,
no HTTP layer — appropriate for a single-user dispatcher demo.
"""

from __future__ import annotations

import shapely.geometry
import streamlit as st

import config
from agent.loop import run_advisory_with_context
from data import load_mock_shipments
from schemas import HazardPolygon, RouteWeatherResult, Shipment, WeatherObservation
from tools.thresholds import evaluate_breach
from ui.map_layers import build_deck

st.set_page_config(page_title="WeatherNext Route Disruption Copilot", layout="wide")


@st.cache_data
def _shipments() -> list[dict]:
    # Cached as plain dicts, not Shipment objects: st.cache_data pickles the return value, and ETAs
    # are resolved once per session (not re-resolved to "now" on every rerun).
    return [s.model_dump(mode="json") for s in load_mock_shipments()]


def _find_shipment(shipment_id: str) -> Shipment:
    raw = next(s for s in _shipments() if s["shipment_id"] == shipment_id)
    return Shipment.model_validate(raw)


def _generate_simulated_weather(shipment: Shipment, scenario: str) -> RouteWeatherResult:
    """Fabricates plausible wind/precip/temp numbers for a demo scenario, but always runs them
    through the real evaluate_breach() — never hand-writes breach/reason text. Otherwise this
    simulation silently disagrees with production logic: it previously always said "exceeds
    45mph threshold" even for a dry-van shipment, which should trip the 35mph threshold instead."""
    all_wp = shipment.all_waypoints()
    observations = []
    # Pick target breach waypoint (prefer first intermediate waypoint if present, else destination)
    target_idx = 1 if len(all_wp) > 2 else (len(all_wp) - 1)

    for idx, wp in enumerate(all_wp):
        # "(Breach)" (with the leading paren), not bare "Breach": the clear scenario's own label,
        # "Clear Weather (Safe, No Breach)", contains the substring "Breach" too ("No Breach") —
        # a bare substring check here would have silently injected a storm breach into the
        # "clear" demo scenario.
        is_breach_target = (idx == target_idx) and ("(Breach)" in scenario)
        if is_breach_target and "Black Ice" in scenario:
            wind, precip, temp = 14.0, 3.2, 28.5
        elif is_breach_target:
            wind, precip, temp = 54.0, 32.0, 68.0
        else:
            wind, precip, temp = 12.0, 0.0, 72.0

        breached, reasons = evaluate_breach(
            wind_speed_mph=wind,
            precip_mm_hr=precip,
            temp_f=temp,
            cargo_description=shipment.cargo_description,
        )

        hazard = None
        if breached:
            lat, lon = wp.location.lat, wp.location.lon
            box = shapely.geometry.box(lon - 0.15, lat - 0.15, lon + 0.15, lat + 0.15)
            hazard = HazardPolygon(
                geojson=shapely.geometry.mapping(box),
                waypoint_name=wp.name,
                reason_summary="; ".join(reasons),
            )

        observations.append(
            WeatherObservation(
                waypoint_name=wp.name,
                location=wp.location,
                valid_time=wp.eta,
                wind_speed_mph=wind,
                precip_mm_hr=precip,
                temp_f=temp,
                breached=breached,
                reasons=reasons,
                hazard_polygon=hazard,
            )
        )

    return RouteWeatherResult(shipment_id=shipment.shipment_id, observations=observations)


st.title("WeatherNext Route Disruption Copilot")

shipment_ids = [s["shipment_id"] for s in _shipments()]
selected_id = st.sidebar.selectbox("Active shipment", shipment_ids)
shipment = _find_shipment(selected_id)

st.sidebar.markdown(f"**Carrier:** {shipment.carrier or 'n/a'}")
st.sidebar.markdown(f"**Cargo:** {shipment.cargo_description or 'n/a'}")

st.sidebar.markdown("---")
data_source = st.sidebar.radio(
    "Weather Data Source",
    ["Simulation Mode (Offline Demo)", "Live WeatherNext 3 (BigQuery)"],
    index=0,
    help="Simulation mode allows testing live Gemini reasoning and Maps routing without waiting for BigQuery dataset allowlisting.",
)

scenario = None
if data_source == "Simulation Mode (Offline Demo)":
    scenario = st.sidebar.selectbox(
        "Weather Scenario",
        [
            "Severe Storm & High Wind (Breach)",
            "Black Ice Risk (Breach)",
            "Clear Weather (Safe, No Breach)",
        ],
    )

run_clicked = st.sidebar.button("Run Disruption Check", type="primary")

state_key = f"advisory::{selected_id}::{data_source}::{scenario}"
if run_clicked:
    with st.spinner("Checking weather forecasts and reasoning over the route with Gemini..."):
        weather_override = None
        if data_source == "Simulation Mode (Offline Demo)" and scenario:
            weather_override = _generate_simulated_weather(shipment, scenario)

        try:
            advisory = run_advisory_with_context(shipment, weather_result_override=weather_override)
        except Exception as exc:
            err_msg = str(exc)
            # Narrow, specific signal only: BigQuery's own "dataset not found" message. The
            # earlier version also matched the bare substring "weathernext3" (the project id),
            # which would have mislabeled an unrelated error — e.g. an auth/IAM failure that
            # happens to mention the project id — as "switch to simulation mode".
            if "Not found: Dataset" in err_msg and "weathernext" in err_msg.lower():
                err_msg = (
                    "BigQuery WeatherNext 3 dataset is not yet subscribed in this GCP project. "
                    "Switch to 'Simulation Mode (Offline Demo)' in the sidebar to test with live Gemini & Maps routing."
                )
            st.session_state[state_key] = {"error": err_msg}
        else:
            st.session_state[state_key] = {
                "text": advisory.text,
                "weather_result": advisory.weather_result,
                "detour_result": advisory.detour_result,
                "simulated": weather_override is not None,
            }

result = st.session_state.get(state_key)

if result and "error" not in result:
    m1, m2, m3, m4 = st.columns(4)
    weather_res = result.get("weather_result")
    detour_res = result.get("detour_result")

    if weather_res and weather_res.any_breach:
        m1.metric(
            "Safety Status", "Breach Detected", delta="Action Required", delta_color="inverse"
        )
    else:
        m1.metric("Safety Status", "Clear", delta="Normal Operations")

    if detour_res:
        delta_km = detour_res.distance_delta_meters / 1000
        delta_min = detour_res.eta_delta_seconds / 60
        # still_hazardous means Routes API couldn't find any candidate that fully avoids the
        # hazard (e.g. the hazard sits on a mandatory waypoint itself) — the numbers below then
        # describe the *least-bad* candidate it fell back to, not a working detour. Labeling this
        # distinctly matters: a dispatcher skimming only the metric row (not the advisory text or
        # the staging-guidance warning below) should not read "+4.2 km" as "problem solved."
        label_suffix = " (Still Hazardous)" if detour_res.still_hazardous else ""
        m2.metric(f"Distance Delta{label_suffix}", f"{delta_km:+.1f} km")
        m3.metric(f"ETA Delta{label_suffix}", f"{delta_min:+.0f} min")
        # Deliberately the cost of the DETOUR itself (extra miles), not a "cost avoided" figure —
        # the actual avoided cost (an accident, cargo damage, delay penalties) is real but
        # unquantifiable here. See config.DETOUR_COST_PER_MILE_USD.
        delta_miles = detour_res.distance_delta_meters / 1609.344
        detour_cost_usd = delta_miles * config.DETOUR_COST_PER_MILE_USD
        m4.metric(
            f"Est. Reroute Cost{label_suffix}",
            f"${detour_cost_usd:+,.0f}",
            help=(
                f"Rough estimate only: extra distance x ${config.DETOUR_COST_PER_MILE_USD:.2f}/mile "
                "blended operating cost. This is the cost of taking the detour, not a claim about "
                "cost 'avoided' — the cost of NOT rerouting (an accident, cargo damage, delay "
                "penalties) is real but not quantified here."
                + (
                    " No candidate route fully avoids the hazard — this is the least-bad option "
                    "found, not a safe detour. See the staging guidance below."
                    if detour_res.still_hazardous
                    else ""
                )
            ),
        )
    else:
        m2.metric("Distance Delta", "0.0 km")
        m3.metric("ETA Delta", "0 min")
        m4.metric("Est. Reroute Cost", "$0")

col_map, col_advisory = st.columns([2, 1])

with col_map:
    st.subheader("Route map")
    weather_result = result.get("weather_result") if result else None
    detour_result = result.get("detour_result") if result else None
    deck = build_deck(shipment, weather_result=weather_result, detour_result=detour_result)
    st.pydeck_chart(deck)

with col_advisory:
    st.subheader("Dispatcher advisory")
    if result is None:
        st.info("Click “Run Disruption Check” to analyze this shipment's route.")
    elif "error" in result:
        st.error(result["error"])
    else:
        if result.get("simulated"):
            # Persistent, in-panel — not just the sidebar radio state — so this can't be mistaken
            # for a real WeatherNext-backed advisory if the panel is screenshotted or copied out
            # of the app's own context.
            st.error(
                "🧪 SIMULATED DATA — this advisory was generated from a fabricated weather scenario, not live WeatherNext 3 data."
            )
        weather_res = result.get("weather_result")
        detour_res = result.get("detour_result")
        if weather_res and weather_res.unmonitored_waypoints:
            st.warning(
                f"⚠️ Weather data unavailable for: {', '.join(weather_res.unmonitored_waypoints)}"
            )
        if detour_res and detour_res.advisory_guidance:
            st.warning(f"⚠️ **Staging Guidance:** {detour_res.advisory_guidance}")
        st.markdown(result["text"])
