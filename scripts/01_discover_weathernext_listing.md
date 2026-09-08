# Discovering the WeatherNext 3 Analytics Hub listing (manual step)

**Status: investigated, not found — treated as not currently available.** `tools/weather.py` and
`config.py` run against the placeholder dataset/table/column names copied from the original
blueprint's guesses, by deliberate decision (not an oversight), until this changes.

## What we tried (2026-09-08)

- The Analytics Hub API has no cross-organization "search listings" method (only
  `dataExchanges.listings.list`, which requires already knowing the publisher project + exchange
  ID) and no `organizations.locations.dataExchanges.list` results for org `539770835457` at `us`,
  `us-central1`, or `eu`.
- This session's web search is blocked by org policy
  (`constraints/vertexai.allowedPartnerModelFeatures`); other search engines fetched directly
  block bot/scraper access.
- Scanned every BigQuery project reachable from this account (`weathernext3-route-agent`,
  `genai-showcase-search`, `fde-k8s-sandbox-dev-503722`, `hcls-capstone-project-503716`,
  `hcls-capstone-project`) for any dataset already named like WeatherNext — none found.
- **A human checked BigQuery Studio's "+ ADD DATA" panel directly** (project
  `weathernext3-route-agent`): searching "weather" surfaces only paid third-party connectors
  (AccuWeather) from Google Cloud Marketplace's data-connector catalog — not Analytics Hub's
  public dataset exchange, and no "WeatherNext" result of any kind.

**Conclusion:** WeatherNext 3 does not currently appear to be a public, subscribable BigQuery
Analytics Hub listing — at least not one discoverable from this org today. It may be in limited
preview, allowlist-gated, or distributed through a different channel entirely (Google's actual
WeatherNext/DeepMind forecasts have historically shipped via Earth Engine's data catalog, not
BigQuery). If you obtain access some other way (a doc, an allowlist invite, a different product),
revisit this file and the steps below.

## How to find it

1. Open the BigQuery Console: https://console.cloud.google.com/bigquery?project=weathernext3-route-agent
2. Click **+ ADD DATA** (or the equivalent "Add" button in the Explorer panel) → **Search
   marketplace listings** (or navigate directly to
   https://console.cloud.google.com/bigquery/analytics-hub).
3. Search for `WeatherNext`. If nothing appears under that exact name, also try `Weather Next`,
   `Google WeatherNext`, or check the "Google Cloud Datasets" / "Google Research" publisher
   sections directly — the listing name may not exactly match "WeatherNext 3".
4. Once found, record here:
   - Publisher project ID: `______`
   - Data exchange ID: `______`
   - Listing ID: `______`
   - Spatial resolution variant (0.1° vs 0.05°) and its exact listing/table name: `______`
5. Subscribe to the listing (via the Console's "Subscribe" button, or run
   `scripts/02_subscribe_analytics_hub_listing.sh` once the IDs above are filled in). Subscribing
   creates a **linked dataset** in `weathernext3-route-agent` — it does not copy data, but it is a
   real resource; confirm before subscribing if using the script.
6. After subscribing, run:
   ```bash
   bq ls --project_id=weathernext3-route-agent
   bq show --schema --format=prettyjson weathernext3-route-agent:<LINKED_DATASET>.<TABLE>
   ```
   and paste the **real** column names, types, partitioning column, and clustering column below.

## Real schema (fill in after subscribing)

- Linked dataset name: `______`
- Table name: `______`
- Partitioning column: `______` (blueprint assumes `init_time`, TIMESTAMP)
- Clustering column: `______` (blueprint assumes `geography`, GEOGRAPHY)
- Columns actually present (do not assume any of these blueprint-guessed names are correct):
  `wind_speed_10m_p90`, `total_precipitation_p90`, `temperature_2m_mean`, `forecast_hour`,
  `init_time`, `geography`.

## Once filled in

Update:
- `.env` (not `.env.example`): `WEATHERNEXT_DATASET`, `WEATHERNEXT_TABLE`.
- `tools/weather.py`: replace the placeholder column names in `_QUERY_TEMPLATE` with the real
  ones, and adjust the partition-pruning clause if the partitioning column differs from
  `init_time`.
- Remove the "PLACEHOLDER SCHEMA" warning comments in both files.
