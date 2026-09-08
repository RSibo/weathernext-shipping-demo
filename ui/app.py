"""Streamlit dispatcher UI. Single-process: calls agent.loop.run_advisory_with_context directly,
no HTTP layer — appropriate for a single-user dispatcher demo, not a scope this app was ever asked
to grow into.
"""

from __future__ import annotations

import streamlit as st

from agent.loop import run_advisory_with_context
from data import load_mock_shipments
from schemas import Shipment
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


st.title("WeatherNext Route Disruption Copilot")

shipment_ids = [s["shipment_id"] for s in _shipments()]
selected_id = st.sidebar.selectbox("Active shipment", shipment_ids)
shipment = _find_shipment(selected_id)

st.sidebar.markdown(f"**Carrier:** {shipment.carrier or 'n/a'}")
st.sidebar.markdown(f"**Cargo:** {shipment.cargo_description or 'n/a'}")
run_clicked = st.sidebar.button("Run Disruption Check", type="primary")

state_key = f"advisory::{selected_id}"
if run_clicked:
    with st.spinner("Checking WeatherNext 3 forecasts and reasoning over the route..."):
        try:
            advisory = run_advisory_with_context(shipment)
        except Exception as exc:  # live BigQuery/Routes/Gemini failure (e.g. dataset not yet subscribed)
            st.session_state[state_key] = {"error": str(exc)}
        else:
            st.session_state[state_key] = {
                "text": advisory.text,
                "weather_result": advisory.weather_result,
                "detour_result": advisory.detour_result,
            }

result = st.session_state.get(state_key)

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
        st.markdown(result["text"])
