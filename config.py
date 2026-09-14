"""Centralized environment-variable configuration.

Note: GOOGLE_GENAI_USE_VERTEXAI / VERTEX_LOCATION here configure the *target app's* Gemini client
(agent/loop.py), independent of this Claude Code session's own ANTHROPIC_VERTEX_PROJECT_ID /
CLOUD_ML_REGION in .claude/settings.json — the two are unrelated clients hitting the same project.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()  # no-op if .env doesn't exist (e.g. in CI)

# No real-project default on purpose: this repo was originally built against
# weathernext3-route-agent, and a same-named fallback here would let anyone who clones this and
# forgets to set GCP_PROJECT_ID silently attempt to hit *that* project instead of their own,
# surfacing as a confusing permission-denied error instead of a clear "you haven't configured
# this yet" one. The placeholder below fails obviously and immediately when actually used against
# GCP, while still letting `pytest` (fully mocked, no live calls) run without any .env at all.
PROJECT_ID = os.getenv("GCP_PROJECT_ID", "your-gcp-project-id")
BQ_LOCATION = os.getenv("BQ_LOCATION", "US")

# CONFIRMED real linked dataset name — verified against the live subscribed dataset in
# weathernext3-route-agent (2026-09-14). Real name is `weathernext_3`, not the earlier placeholder
# `weathernext3`.
WEATHERNEXT_DATASET = os.getenv("WEATHERNEXT_DATASET", "weathernext_3")
# CONFIRMED real table name (from Google's official WeatherNext 3 BigQuery docs) — not a guess.
WEATHERNEXT_TABLE = os.getenv("WEATHERNEXT_TABLE", "weathernext_3_0_0_0p1deg")
# Grid resolution of the table above (0.1deg); used only to size HAZARD_BUFFER_DEGREES's margin —
# spatial joins use the table's real geography_polygon directly, not this value.
WEATHERNEXT_GRID_DEGREES = float(os.getenv("WEATHERNEXT_GRID_DEGREES", "0.1"))
# Extra outward buffer applied to a hazard grid cell before it's used to filter candidate routes —
# deliberately conservative rather than exactly the forecast cell's edge.
HAZARD_BUFFER_DEGREES = float(os.getenv("HAZARD_BUFFER_DEGREES", "0.05"))
# CONFIRMED: wind_speed_10m exists as a direct field (no u/v component decomposition needed) and
# is in m/s — verified against real sample data (2026-09-14), not a guess.
WEATHERNEXT_WIND_VARIABLE = os.getenv("WEATHERNEXT_WIND_VARIABLE", "wind_speed_10m")

# How far back (from now) to search for a usable init_time. This is NOT about the forecast
# horizon (how far into the future a single init reaches, up to 360h) — it's about how far into
# the PAST we need to look to find at least one fresh init. Inits occur at minimum every 6 hours
# (the 6-hourly cadence), so any window meaningfully wider than 6h already has safety margin; 24h
# gives 4x margin against pipeline delays. This used to be hardcoded as 16 DAY (conflating horizon
# with lookback) and was the actual root cause of a real, measured 8.19TB / ~$51 query — not, as
# first assumed, an inherent property of the dataset. Confirmed live (2026-09-14) on the real
# production query, interval-only comparison: 16 DAY = 7.45TB, 24 HOUR = 363GB (95% less),
# 12 HOUR = 61GB (99% less). 24h chosen for safety margin over the theoretical 12h minimum.
WEATHERNEXT_INIT_LOOKBACK_HOURS = int(os.getenv("WEATHERNEXT_INIT_LOOKBACK_HOURS", "24"))

# Safety cap, not a cost-optimization knob: a malformed query or an accidental widening of
# WEATHERNEXT_INIT_LOOKBACK_HOURS should fail loudly and for free, not silently bill an unbounded
# amount. Sized with generous headroom above the real measured cost at the default lookback above
# (~350-900GB for shipments up to 5 waypoints, confirmed live 2026-09-14 — cost is dominated by the
# lookback window's width, not waypoint count).
WEATHERNEXT_MAX_BYTES_BILLED = int(
    os.getenv("WEATHERNEXT_MAX_BYTES_BILLED", str(2 * 1024**4))
)  # 2 TB

# Forecasts update roughly hourly; caching a shipment's weather result for this long avoids
# re-paying the real BigQuery cost above on every UI click/rerun for the same shipment.
WEATHERNEXT_CACHE_TTL_SECONDS = int(
    os.getenv("WEATHERNEXT_CACHE_TTL_SECONDS", str(60 * 60))
)  # 1 hour

# No default: tools/routing.py raises at call time (not import time) if unset, so importing this
# module and running unit tests never requires a real key.
GOOGLE_MAPS_ROUTES_API_KEY = os.getenv("GOOGLE_MAPS_ROUTES_API_KEY")

# Not a tunable request parameter — computeRoutes returns "up to 3" alternatives when
# computeAlternativeRoutes=true; this is just documenting that ceiling, not configuring it.
MAX_ALTERNATIVE_ROUTES = 3

# Rough, commonly-cited blended trucking operating cost per mile (fuel, driver wages, maintenance,
# insurance — comparable to figures ATRI has published in past operational-cost reports), used
# ONLY to give the UI's detour cost estimate a real-world unit. This is deliberately a cost of the
# *detour itself* (extra distance), NOT a claimed "cost avoided" figure — the actual avoided cost
# (an accident, cargo damage, delay penalties) is real but unquantifiable here, and this repo does
# not invent a number for it. Replace with your own fleet's real blended operating cost per mile.
DETOUR_COST_PER_MILE_USD = float(os.getenv("DETOUR_COST_PER_MILE_USD", "2.00"))

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
VERTEX_LOCATION = os.getenv("VERTEX_LOCATION", "global")
GOOGLE_GENAI_USE_VERTEXAI = os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "1") == "1"
