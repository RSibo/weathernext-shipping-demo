"""Centralized environment-variable configuration.

Note: GOOGLE_GENAI_USE_VERTEXAI / VERTEX_LOCATION here configure the *target app's* Gemini client
(agent/loop.py), independent of this Claude Code session's own ANTHROPIC_VERTEX_PROJECT_ID /
CLOUD_ML_REGION in .claude/settings.json — the two are unrelated clients hitting the same project.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()  # no-op if .env doesn't exist (e.g. in CI)

PROJECT_ID = os.getenv("GCP_PROJECT_ID", "weathernext3-route-agent")
BQ_LOCATION = os.getenv("BQ_LOCATION", "US")

# PLACEHOLDER: the linked dataset ID is chosen at subscribe time and still unknown — subscription
# is pending. See scripts/01_discover_weathernext_listing.md.
WEATHERNEXT_DATASET = os.getenv("WEATHERNEXT_DATASET", "weathernext3")
# CONFIRMED real table name (from Google's official WeatherNext 3 BigQuery docs) — not a guess.
WEATHERNEXT_TABLE = os.getenv("WEATHERNEXT_TABLE", "weathernext_3_0_0_0p1deg")
# Grid resolution of the table above (0.1deg); used only to size HAZARD_BUFFER_DEGREES's margin —
# spatial joins use the table's real geography_polygon directly, not this value.
WEATHERNEXT_GRID_DEGREES = float(os.getenv("WEATHERNEXT_GRID_DEGREES", "0.1"))
# Extra outward buffer applied to a hazard grid cell before it's used to filter candidate routes —
# deliberately conservative rather than exactly the forecast cell's edge.
HAZARD_BUFFER_DEGREES = float(os.getenv("HAZARD_BUFFER_DEGREES", "0.05"))
# UNVERIFIED GUESS: not shown in Google's example queries (only temperature/precipitation are).
# Isolated here so correcting it once real access arrives is a one-line change, not a query edit.
WEATHERNEXT_WIND_VARIABLE = os.getenv("WEATHERNEXT_WIND_VARIABLE", "wind_speed_10m")

# No default: tools/routing.py raises at call time (not import time) if unset, so importing this
# module and running unit tests never requires a real key.
GOOGLE_MAPS_ROUTES_API_KEY = os.getenv("GOOGLE_MAPS_ROUTES_API_KEY")

# Not a tunable request parameter — computeRoutes returns "up to 3" alternatives when
# computeAlternativeRoutes=true; this is just documenting that ceiling, not configuring it.
MAX_ALTERNATIVE_ROUTES = 3

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
VERTEX_LOCATION = os.getenv("VERTEX_LOCATION", "global")
GOOGLE_GENAI_USE_VERTEXAI = os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "1") == "1"
