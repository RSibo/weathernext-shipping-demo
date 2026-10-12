from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import sys

import numpy as np

REPO = Path(
    "/usr/local/google/home/rsibo/.gemini/jetski/brain/924b423b-86a9-4174-bc35-0cc2edb24d00/scratch/gauravks-weathernext-route-disruption-agent"
)
sys.path.insert(0, str(REPO))

EARTH_RADIUS_KM = 6371.0088

AUSTRALIAN_PLACES = [
    # Sydney & Greater Sydney
    {"name": "Sydney, NSW", "lat": -33.8688, "lon": 151.2093},
    {"name": "Liverpool, NSW", "lat": -33.9200, "lon": 150.9255},
    {"name": "Campbelltown, NSW", "lat": -34.0650, "lon": 150.8142},
    # M31 Hume Hwy corridor (Sydney <-> Melbourne)
    {"name": "Mittagong, NSW", "lat": -34.4519, "lon": 150.4503},
    {"name": "Goulburn, NSW", "lat": -34.7546, "lon": 149.7186},
    {"name": "Yass, NSW", "lat": -34.8403, "lon": 148.9136},
    {"name": "Gundagai, NSW", "lat": -35.0653, "lon": 148.1069},
    {"name": "Tarcutta, NSW", "lat": -35.2764, "lon": 147.7347},
    {"name": "Holbrook, NSW", "lat": -35.7244, "lon": 147.3139},
    {"name": "Albury, NSW", "lat": -36.0737, "lon": 146.9135},
    {"name": "Wodonga, VIC", "lat": -36.1214, "lon": 146.8881},
    {"name": "Wangaratta, VIC", "lat": -36.3584, "lon": 146.3125},
    {"name": "Benalla, VIC", "lat": -36.5519, "lon": 145.9817},
    {"name": "Euroa, VIC", "lat": -36.7500, "lon": 145.5667},
    {"name": "Seymour, VIC", "lat": -37.0267, "lon": 145.1392},
    {"name": "Melbourne, VIC", "lat": -37.8136, "lon": 144.9631},
    # A1 Princes Hwy coastal corridor (Sydney -> Melbourne detour)
    {"name": "Wollongong, NSW", "lat": -34.4278, "lon": 150.8931},
    {"name": "Nowra, NSW", "lat": -34.8736, "lon": 150.6025},
    {"name": "Ulladulla, NSW", "lat": -35.3575, "lon": 150.4733},
    {"name": "Batemans Bay, NSW", "lat": -35.7081, "lon": 150.1744},
    {"name": "Narooma, NSW", "lat": -36.2164, "lon": 150.1236},
    {"name": "Bega, NSW", "lat": -36.6739, "lon": 149.8417},
    {"name": "Eden, NSW", "lat": -37.0633, "lon": 149.9039},
    {"name": "Cann River, VIC", "lat": -37.5667, "lon": 149.1500},
    {"name": "Orbost, VIC", "lat": -37.7042, "lon": 148.4556},
    {"name": "Bairnsdale, VIC", "lat": -37.8253, "lon": 147.6286},
    {"name": "Sale, VIC", "lat": -38.1065, "lon": 147.0680},
    {"name": "Traralgon, VIC", "lat": -38.1958, "lon": 146.5403},
    {"name": "Warragul, VIC", "lat": -38.1631, "lon": 145.9333},
    # B23 Monaro Hwy / Canberra corridor
    {"name": "Canberra, ACT", "lat": -35.2809, "lon": 149.1300},
    {"name": "Cooma, NSW", "lat": -36.2350, "lon": 149.1258},
    {"name": "Bombala, NSW", "lat": -36.9114, "lon": 149.2375},
    # A41 Olympic Hwy / Central West corridor
    {"name": "Lithgow, NSW", "lat": -33.4808, "lon": 150.1575},
    {"name": "Bathurst, NSW", "lat": -33.4193, "lon": 149.5775},
    {"name": "Cowra, NSW", "lat": -33.8347, "lon": 148.6911},
    {"name": "Young, NSW", "lat": -34.3125, "lon": 148.3011},
    {"name": "Wagga Wagga, NSW", "lat": -35.1082, "lon": 147.3598},
    # M1 Pacific Hwy corridor (Sydney <-> Brisbane)
    {"name": "Gosford, NSW", "lat": -33.4267, "lon": 151.3417},
    {"name": "Newcastle, NSW", "lat": -32.9283, "lon": 151.7817},
    {"name": "Taree, NSW", "lat": -31.9108, "lon": 152.4594},
    {"name": "Port Macquarie, NSW", "lat": -31.4308, "lon": 152.9089},
    {"name": "Kempsey, NSW", "lat": -31.0792, "lon": 152.8306},
    {"name": "Coffs Harbour, NSW", "lat": -30.2963, "lon": 153.1157},
    {"name": "Grafton, NSW", "lat": -29.6908, "lon": 152.9331},
    {"name": "Ballina, NSW", "lat": -28.8667, "lon": 153.5667},
    {"name": "Byron Bay, NSW", "lat": -28.6474, "lon": 153.6020},
    {"name": "Gold Coast, QLD", "lat": -28.0167, "lon": 153.4000},
    {"name": "Brisbane, QLD", "lat": -27.4698, "lon": 153.0251},
    # A15 New England Hwy corridor (Sydney -> Brisbane inland)
    {"name": "Singleton, NSW", "lat": -32.5686, "lon": 151.1753},
    {"name": "Muswellbrook, NSW", "lat": -32.2656, "lon": 150.8886},
    {"name": "Scone, NSW", "lat": -32.0481, "lon": 150.8678},
    {"name": "Tamworth, NSW", "lat": -31.0927, "lon": 150.9290},
    {"name": "Armidale, NSW", "lat": -30.5120, "lon": 151.6660},
    {"name": "Glen Innes, NSW", "lat": -29.7406, "lon": 151.7358},
    {"name": "Tenterfield, NSW", "lat": -29.0553, "lon": 152.0178},
    {"name": "Warwick, QLD", "lat": -28.2152, "lon": 152.0350},
    {"name": "Ipswich, QLD", "lat": -27.6144, "lon": 152.7608},
    # A39 Newell Hwy corridor (Brisbane -> Dubbo -> Melbourne)
    {"name": "Toowoomba, QLD", "lat": -27.5598, "lon": 151.9507},
    {"name": "Goondiwindi, QLD", "lat": -28.5461, "lon": 150.3097},
    {"name": "Moree, NSW", "lat": -29.4658, "lon": 149.8417},
    {"name": "Narrabri, NSW", "lat": -30.3250, "lon": 149.7828},
    {"name": "Coonabarabran, NSW", "lat": -31.2756, "lon": 149.2775},
    {"name": "Gilgandra, NSW", "lat": -31.7097, "lon": 148.6631},
    {"name": "Dubbo, NSW", "lat": -32.2430, "lon": 148.6011},
    {"name": "Parkes, NSW", "lat": -33.1372, "lon": 148.1758},
    {"name": "Forbes, NSW", "lat": -33.3844, "lon": 148.0078},
    {"name": "West Wyalong, NSW", "lat": -33.9261, "lon": 147.2094},
    {"name": "Narrandera, NSW", "lat": -34.7475, "lon": 146.5503},
    {"name": "Jerilderie, NSW", "lat": -35.3561, "lon": 145.7294},
    {"name": "Tocumwal, NSW", "lat": -35.8114, "lon": 145.5639},
    {"name": "Shepparton, VIC", "lat": -36.3833, "lon": 145.4000},
]


def update_places() -> None:
    places_path = REPO / "data" / "places.json"
    existing = json.loads(places_path.read_text())
    # Keep US entries so tests/test_cube_geo.py (Arlington, WY) continues to pass, and prepend Australian places
    us_only = [p for p in existing if not p["name"].endswith((", NSW", ", VIC", ", QLD", ", ACT"))]
    merged = AUSTRALIAN_PLACES + us_only
    places_path.write_text(json.dumps(merged, indent=2) + "\n")
    from tools.geo import _places
    _places.cache_clear()
    print(f"Updated {places_path} ({len(AUSTRALIAN_PLACES)} AU places + {len(us_only)} US test places)")


def densify_waypoints(waypoints: list[tuple[float, float]], step_deg: float = 0.08) -> list[tuple[float, float]]:
    """Interpolate smooth highway geometry between town waypoints."""
    out: list[tuple[float, float]] = [waypoints[0]]
    for (lat0, lon0), (lat1, lon1) in zip(waypoints[:-1], waypoints[1:]):
        dist = math.hypot(lat1 - lat0, lon1 - lon0)
        n = max(1, int(math.ceil(dist / step_deg)))
        for k in range(1, n + 1):
            frac = k / n
            out.append((round(lat0 + (lat1 - lat0) * frac, 5), round(lon0 + (lon1 - lon0) * frac, 5)))
    return out


def encode_polyline(coords: list[tuple[float, float]], precision: int = 5) -> str:
    factor = 10**precision
    output = []
    prev_lat = 0
    prev_lon = 0

    def _enc(val: int) -> str:
        val = ~(val << 1) if val < 0 else (val << 1)
        chunks = []
        while val >= 0x20:
            chunks.append(chr((0x20 | (val & 0x1F)) + 63))
            val >>= 5
        chunks.append(chr(val + 63))
        return "".join(chunks)

    for lat, lon in coords:
        ilat = int(round(lat * factor))
        ilon = int(round(lon * factor))
        output.append(_enc(ilat - prev_lat))
        output.append(_enc(ilon - prev_lon))
        prev_lat, prev_lon = ilat, ilon
    return "".join(output)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = np.sin(dp / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return float(2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(a)))


def make_route_dict(
    description: str,
    waypoints: list[tuple[float, float]],
    distance_km: float,
    duration_hours: float,
    *,
    is_default: bool = True,
    leg_split_frac: float | None = None,
) -> dict:
    pts = densify_waypoints(waypoints)
    enc = encode_polyline(pts, 5)
    dist_m = int(round(distance_km * 1000))
    dur_s = int(round(duration_hours * 3600))
    if leg_split_frac is not None:
        leg0_m = int(round(dist_m * leg_split_frac))
        leg0_s = int(round(dur_s * leg_split_frac))
        legs = [
            {"distanceMeters": leg0_m, "duration": f"{leg0_s}s"},
            {"distanceMeters": dist_m - leg0_m, "duration": f"{dur_s - leg0_s}s"},
        ]
    else:
        legs = [{"distanceMeters": dist_m, "duration": f"{dur_s}s"}]
    return {
        "description": description,
        "distanceMeters": dist_m,
        "duration": f"{dur_s}s",
        "polyline": {"encodedPolyline": enc},
        "routeLabels": ["DEFAULT_ROUTE"] if is_default else ["DEFAULT_ROUTE_ALTERNATE"],
        "legs": legs,
    }


def latlng(lat: float, lon: float) -> dict:
    return {"location": {"latLng": {"latitude": round(lat, 5), "longitude": round(lon, 5)}}}


def make_body(
    origin: dict,
    destination: dict,
    *,
    stops: list[dict] = (),
    via: dict | None = None,
    via_index: int | None = None,
    alternatives: bool = False,
) -> dict:
    intermediates = [latlng(s["lat"], s["lon"]) for s in stops]
    if via is not None:
        pos = len(intermediates) if via_index is None else max(0, min(via_index, len(intermediates)))
        intermediates.insert(pos, {**latlng(via["lat"], via["lon"]), "via": True})
    body: dict = {
        "origin": latlng(origin["lat"], origin["lon"]),
        "destination": latlng(destination["lat"], destination["lon"]),
        "travelMode": "DRIVE",
        "routingPreference": "TRAFFIC_UNAWARE",
        "computeAlternativeRoutes": bool(alternatives and not intermediates),
        "units": "IMPERIAL",
    }
    if intermediates:
        body["intermediates"] = intermediates
    return body


def via_index_for(shipment: dict, anchor: dict) -> int:
    pts = [shipment["origin"], *shipment.get("waypoints", []), shipment["destination"]]
    best, best_cost = 0, float("inf")
    for i in range(len(pts) - 1):
        a, b = pts[i], pts[i + 1]
        cost = (
            haversine_km(a["lat"], a["lon"], anchor["lat"], anchor["lon"])
            + haversine_km(anchor["lat"], anchor["lon"], b["lat"], b["lon"])
            - haversine_km(a["lat"], a["lon"], b["lat"], b["lon"])
        )
        if cost < best_cost:
            best, best_cost = i, cost
    return best


def put_cache(cache_dir: Path, body: dict, routes: list[dict]) -> str:
    k = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:24]
    (cache_dir / f"{k}.json").write_text(json.dumps({"request": body, "routes": routes}))
    return k


def build_australian_routes_cache() -> None:
    cache_dir = REPO / "data" / "routes_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    for old_f in cache_dir.glob("*.json"):
        old_f.unlink()

    # 1. Sydney -> Melbourne: M31 Hume Hwy (baseline, 878 km, 9.0h)
    hume_syd_mel_pts = [
        (-33.8688, 151.2093),  # Sydney
        (-33.9200, 150.9255),  # Liverpool
        (-34.0650, 150.8142),  # Campbelltown
        (-34.4519, 150.4503),  # Mittagong
        (-34.7546, 149.7186),  # Goulburn
        (-34.8403, 148.9136),  # Yass
        (-35.0653, 148.1069),  # Gundagai
        (-35.2764, 147.7347),  # Tarcutta
        (-35.7244, 147.3139),  # Holbrook
        (-36.0737, 146.9135),  # Albury
        (-36.1214, 146.8881),  # Wodonga
        (-36.3584, 146.3125),  # Wangaratta
        (-36.5519, 145.9817),  # Benalla
        (-36.7500, 145.5667),  # Euroa
        (-37.0267, 145.1392),  # Seymour
        (-37.8136, 144.9631),  # Melbourne
    ]
    r_hume_syd_mel = make_route_dict("M31 Hume Hwy", hume_syd_mel_pts, 878.0, 9.0, is_default=True)

    # 2. Sydney -> Melbourne: A1 Princes Hwy (coastal detour, 1030 km, 9.9667h = +58 min)
    princes_syd_mel_pts = [
        (-33.8688, 151.2093),  # Sydney
        (-34.4278, 150.8931),  # Wollongong
        (-34.8736, 150.6025),  # Nowra
        (-35.3575, 150.4733),  # Ulladulla
        (-35.7081, 150.1744),  # Batemans Bay
        (-36.2164, 150.1236),  # Narooma
        (-36.6739, 149.8417),  # Bega
        (-37.0633, 149.9039),  # Eden
        (-37.5667, 149.1500),  # Cann River
        (-37.7042, 148.4556),  # Orbost
        (-37.8253, 147.6286),  # Bairnsdale
        (-38.1065, 147.0680),  # Sale
        (-38.1958, 146.5403),  # Traralgon
        (-38.1631, 145.9333),  # Warragul
        (-37.8136, 144.9631),  # Melbourne
    ]
    r_princes_syd_mel = make_route_dict("A1 Princes Hwy", princes_syd_mel_pts, 1030.0, 9.0 + 58 / 60.0, is_default=False)

    # 3. Sydney -> Melbourne: B23 Monaro Hwy via Canberra (965 km, 9.65h = +39 min, passes near Goulburn wind band so hazardous)
    monaro_syd_mel_pts = [
        (-33.8688, 151.2093),  # Sydney
        (-34.4519, 150.4503),  # Mittagong
        (-34.7546, 149.7186),  # Goulburn
        (-35.2809, 149.1300),  # Canberra
        (-36.2350, 149.1258),  # Cooma
        (-36.9114, 149.2375),  # Bombala
        (-37.5667, 149.1500),  # Cann River
        (-37.7042, 148.4556),  # Orbost
        (-37.8253, 147.6286),  # Bairnsdale
        (-38.1065, 147.0680),  # Sale
        (-38.1958, 146.5403),  # Traralgon
        (-37.8136, 144.9631),  # Melbourne
    ]
    r_monaro_syd_mel = make_route_dict("B23 Monaro Hwy", monaro_syd_mel_pts, 965.0, 9.65, is_default=True)

    # 4. Sydney -> Melbourne: A41 Olympic Hwy via Bathurst & Wagga Wagga (952 km, 10.35h = +81 min, clean inland)
    olympic_syd_mel_pts = [
        (-33.8688, 151.2093),  # Sydney
        (-33.4808, 150.1575),  # Lithgow
        (-33.4193, 149.5775),  # Bathurst
        (-33.8347, 148.6911),  # Cowra
        (-34.3125, 148.3011),  # Young
        (-35.1082, 147.3598),  # Wagga Wagga
        (-36.0737, 146.9135),  # Albury
        (-36.3584, 146.3125),  # Wangaratta
        (-37.0267, 145.1392),  # Seymour
        (-37.8136, 144.9631),  # Melbourne
    ]
    r_olympic_syd_mel = make_route_dict("A41 Olympic Hwy", olympic_syd_mel_pts, 952.0, 10.35, is_default=True)

    # 5. Melbourne -> Sydney: M31 Hume Hwy Northbound (878 km, 9.0h)
    r_hume_mel_syd = make_route_dict("M31 Hume Hwy", list(reversed(hume_syd_mel_pts)), 878.0, 9.0, is_default=True)

    # 6. Sydney -> Brisbane: M1 Pacific Hwy (baseline, 915 km, 9.75h)
    pacific_syd_bne_pts = [
        (-33.8688, 151.2093),  # Sydney
        (-33.4267, 151.3417),  # Gosford
        (-32.9283, 151.7817),  # Newcastle
        (-31.9108, 152.4594),  # Taree
        (-31.4308, 152.9089),  # Port Macquarie
        (-31.0792, 152.8306),  # Kempsey
        (-30.2963, 153.1157),  # Coffs Harbour
        (-29.6908, 152.9331),  # Grafton
        (-28.8667, 153.5667),  # Ballina
        (-28.6474, 153.6020),  # Byron Bay
        (-28.0167, 153.4000),  # Gold Coast
        (-27.4698, 153.0251),  # Brisbane
    ]
    r_pacific_syd_bne = make_route_dict("M1 Pacific Hwy", pacific_syd_bne_pts, 915.0, 9.75, is_default=True)

    # 7. Sydney -> Brisbane: A15 New England Hwy (inland alternative, 955 km, 10.4h)
    new_england_syd_bne_pts = [
        (-33.8688, 151.2093),  # Sydney
        (-33.4267, 151.3417),  # Gosford
        (-32.9283, 151.7817),  # Newcastle
        (-32.5686, 151.1753),  # Singleton
        (-32.2656, 150.8886),  # Muswellbrook
        (-32.0481, 150.8678),  # Scone
        (-31.0927, 150.9290),  # Tamworth
        (-30.5120, 151.6660),  # Armidale
        (-29.7406, 151.7358),  # Glen Innes
        (-29.0553, 152.0178),  # Tenterfield
        (-28.2152, 152.0350),  # Warwick
        (-27.6144, 152.7608),  # Ipswich
        (-27.4698, 153.0251),  # Brisbane
    ]
    r_new_england_syd_bne = make_route_dict("A15 New England Hwy", new_england_syd_bne_pts, 955.0, 10.4, is_default=False)

    # 8. Sydney -> Brisbane via Dubbo: A39 Newell Hwy (1180 km, 12.6h)
    newell_syd_bne_pts = [
        (-33.8688, 151.2093),  # Sydney
        (-33.4808, 150.1575),  # Lithgow
        (-33.4193, 149.5775),  # Bathurst
        (-32.2430, 148.6011),  # Dubbo
        (-31.2756, 149.2775),  # Coonabarabran
        (-30.3250, 149.7828),  # Narrabri
        (-29.4658, 149.8417),  # Moree
        (-28.5461, 150.3097),  # Goondiwindi
        (-27.5598, 151.9507),  # Toowoomba
        (-27.4698, 153.0251),  # Brisbane
    ]
    r_newell_syd_bne = make_route_dict("A39 Newell Hwy", newell_syd_bne_pts, 1180.0, 12.6, is_default=True)

    # 9. Brisbane -> Dubbo -> Melbourne: A39 Newell Hwy (1580 km, 16.8h, stop at Dubbo ~53%)
    newell_bne_mel_pts = [
        (-27.4698, 153.0251),  # Brisbane
        (-27.5598, 151.9507),  # Toowoomba
        (-28.5461, 150.3097),  # Goondiwindi
        (-29.4658, 149.8417),  # Moree
        (-30.3250, 149.7828),  # Narrabri
        (-31.2756, 149.2775),  # Coonabarabran
        (-31.7097, 148.6631),  # Gilgandra
        (-32.2430, 148.6011),  # Dubbo (Stop 1)
        (-33.1372, 148.1758),  # Parkes
        (-33.3844, 148.0078),  # Forbes
        (-33.9261, 147.2094),  # West Wyalong
        (-34.7475, 146.5503),  # Narrandera
        (-35.3561, 145.7294),  # Jerilderie
        (-35.8114, 145.5639),  # Tocumwal
        (-36.3833, 145.4000),  # Shepparton
        (-37.0267, 145.1392),  # Seymour
        (-37.8136, 144.9631),  # Melbourne
    ]
    r_newell_bne_mel = make_route_dict("A39 Newell Hwy", newell_bne_mel_pts, 1580.0, 16.8, is_default=True, leg_split_frac=0.53)

    demo_fleet = json.loads((REPO / "data" / "demo_fleet.json").read_text())
    shipments = {s["shipment_id"]: s for s in demo_fleet["shipments"]}
    anchors = json.loads((REPO / "data" / "anchors.json").read_text())

    # Cache baseline routes for all 5 shipments
    baseline_map = {
        "SHP-2001": r_hume_syd_mel,
        "SHP-2002": r_hume_syd_mel,
        "SHP-2003": r_pacific_syd_bne,
        "SHP-2004": r_hume_mel_syd,
        "SHP-2005": r_newell_bne_mel,
    }
    for sid, rdict in baseline_map.items():
        sh = shipments[sid]
        stops = sh.get("waypoints", [])
        body = make_body(sh["origin"], sh["destination"], stops=stops, alternatives=False)
        k = put_cache(cache_dir, body, [rdict])
        print(f"Cached baseline for {sid} -> {k}.json")

    # Cache alternatives and anchor via-routes for Sydney -> Melbourne (SHP-2001, SHP-2002)
    sh_syd_mel = shipments["SHP-2001"]
    body_alt_syd_mel = make_body(sh_syd_mel["origin"], sh_syd_mel["destination"], alternatives=True)
    put_cache(cache_dir, body_alt_syd_mel, [r_hume_syd_mel, r_princes_syd_mel])

    for a in anchors:
        v_idx = via_index_for(sh_syd_mel, a)
        body_via = make_body(sh_syd_mel["origin"], sh_syd_mel["destination"], via=a, via_index=v_idx, alternatives=False)
        if a["highway"] == "A1 Princes Hwy":
            put_cache(cache_dir, body_via, [{**r_princes_syd_mel, "routeLabels": ["DEFAULT_ROUTE"]}])
        elif a["highway"] == "B23 Monaro Hwy":
            put_cache(cache_dir, body_via, [r_monaro_syd_mel])
        elif a["highway"] == "A41 Olympic Hwy":
            put_cache(cache_dir, body_via, [r_olympic_syd_mel])
        else:
            put_cache(cache_dir, body_via, [{**r_hume_syd_mel, "duration": "54000s"}])

    # Cache alternatives and anchor via-routes for Sydney -> Brisbane (SHP-2003)
    sh_syd_bne = shipments["SHP-2003"]
    body_alt_syd_bne = make_body(sh_syd_bne["origin"], sh_syd_bne["destination"], alternatives=True)
    put_cache(cache_dir, body_alt_syd_bne, [r_pacific_syd_bne, r_new_england_syd_bne])

    for a in anchors:
        v_idx = via_index_for(sh_syd_bne, a)
        body_via = make_body(sh_syd_bne["origin"], sh_syd_bne["destination"], via=a, via_index=v_idx, alternatives=False)
        if a["highway"] == "A15 New England Hwy":
            put_cache(cache_dir, body_via, [{**r_new_england_syd_bne, "routeLabels": ["DEFAULT_ROUTE"]}])
        elif a["highway"] == "A39 Newell Hwy":
            put_cache(cache_dir, body_via, [r_newell_syd_bne])
        else:
            put_cache(cache_dir, body_via, [{**r_pacific_syd_bne, "duration": "56000s"}])


def build_australian_forecast_cube() -> None:
    """Create a 0.1° WeatherNext 3 forecast cube over Eastern Australia (lat -39.5..-26.0, lon 143.0..155.0)."""
    lat = np.round(np.arange(-39.5, -25.95, 0.1), 4)  # 136 rows
    lon = np.round(np.arange(143.0, 155.05, 0.1), 4)  # 121 cols
    init_dt = datetime(2026, 3, 12, 6, 0, 0, tzinfo=timezone.utc)
    init_s = int(init_dt.timestamp())
    times_s = np.asarray([init_s + h * 3600 for h in range(49)], dtype=np.int64)  # 49 hours (0..48h)

    nt, ny, nx = len(times_s), len(lat), len(lon)
    LAT, LON = np.meshgrid(lat, lon, indexing="ij")

    # Base pleasant wind field across Eastern Australia (14 - 18 mph)
    wind = np.zeros((nt, ny, nx), dtype=np.float32)
    precip = np.zeros((nt, ny, nx), dtype=np.float32)
    temp = np.full((nt, ny, nx), 64.0, dtype=np.float32)

    # Story 'now' is h = 16 (init + 16h = 2026-03-12T22:00Z = Fri 09:00 AEDT).
    for h in range(nt):
        rel_h = h - 16.0  # hours relative to story 'now' (depart_iso for all trucks)

        # Background synoptic breeze
        bg = 15.0 + 2.5 * np.sin((LAT + 33.0) * 0.35) * np.cos((LON - 148.0) * 0.35)

        # System 1: Southern Tablelands / Hume Corridor Westerly Gale (hits Goulburn / Yass / Gundagai / Canberra)
        # Centered at lat=-34.85, lon=149.15 (between Goulburn, Yass, and Canberra).
        # Active during rel_h in [-4 .. +4.5] (peaks at 42.0 mph), then subsides after +4.5h so:
        #   - SHP-2001 (Syd->Mel M31 Hume Hwy, passes Goulburn/Yass at rel_h +1.5..+3.5h) hits 42 mph -> BREACH (>35 mph)!
        #   - SHP-2001 coastal detour (A1 Princes Hwy via Nowra/Batemans Bay/Bega, lon >= 150.0 south of -34.8) stays sheltered (<25 mph) -> CLEAN!
        #   - SHP-2001 Olympic Hwy detour (via Bathurst/Cowra/Wagga, north/west early then south later) stays <27 mph -> CLEAN!
        #   - SHP-2002 (Syd->Mel M31 Hume Hwy, flatbed 45 mph limit) hits 42 mph -> AT_RISK (85%..100% of 45 mph) -> MONITOR!
        #   - SHP-2004 (Mel->Syd M31 Hume Hwy northbound, reaches Yass/Goulburn at rel_h +5.5..+7.5h) sees subsided wind (~23 mph) -> CLEAN!
        if rel_h <= 4.2:
            t_amp1 = 1.0 if rel_h <= 3.5 else max(0.0, 1.0 - (rel_h - 3.5) / 1.2)
        else:
            t_amp1 = max(0.0, 0.25 * math.exp(-(rel_h - 4.2) / 2.0))

        # Elliptical footprint over Goulburn (-34.75, 149.72) & Yass (-34.84, 148.91) & Canberra (-35.28, 149.13),
        # dropping off sharply before the coastal escarpment (Nowra lon 150.60, Batemans Bay lon 150.17)
        dlat1 = (LAT - (-34.82)) / 0.55
        dlon1 = (LON - 149.25) / 0.62
        g1 = np.exp(-0.5 * (dlat1**2 + dlon1**2))
        # Coastal escarpment shelter east of lon 150.05
        shelter = np.clip((150.15 - LON) / 0.35, 0.0, 1.0)
        storm1 = 26.8 * t_amp1 * g1 * shelter

        # System 2: East Coast Low over Mid North Coast & Northern Tablelands (Port Macquarie / Coffs Harbour / Armidale / Dubbo NE)
        # Hits BOTH M1 Pacific Hwy (Port Macquarie / Coffs Harbour) AND A15 New England Hwy (Tamworth / Armidale / Glen Innes)
        # AND eastern Newell approach for departures at +0h, +2h, +4h, +6h, +8h (so SHP-2003 sits in storm until rel_h +14h),
        # then moves offshore by rel_h = +14.5h so SHP-2003 departing at +12h (reaching Taree/Port Macquarie at rel_h +15.5..+18h) is CLEAN!
        if rel_h <= 13.5:
            t_amp2 = 1.0
        elif rel_h <= 15.0:
            t_amp2 = max(0.0, 1.0 - (rel_h - 13.5) / 1.5)
        else:
            t_amp2 = 0.0

        # Covers lat -32.0 .. -29.3, lon 149.2 .. 153.5 (so M1 Pacific Hwy, A15 New England Hwy, and Syd->Bne via Dubbo are all >38 mph during rel_h <= 13.5)
        # Wait: SHP-2005 (Brisbane -> Dubbo -> Melbourne) travels from Brisbane (-27.47, 153.02) -> Toowoomba (-27.56, 151.95) -> Goondiwindi (-28.55, 150.31) -> Moree (-29.47, 149.84) -> Narrabri (-30.33, 149.78) -> Dubbo (-32.24, 148.60).
        # Let's keep System 2 east of lon 150.5 so SHP-2005 on the inland A39 Newell Hwy (lon <= 150.3 south of -28.5) stays completely CLEAR (<23 mph)!
        # And for Sydney -> Brisbane (SHP-2003), both M1 Pacific Hwy (lon 152.4..153.5) and A15 New England Hwy (lon 150.9..152.1) are east of lon 150.6!
        dlat2 = (LAT - (-30.75)) / 1.05
        dlon2 = (LON - 152.35) / 1.15
        east_mask = np.clip((LON - 150.35) / 0.35, 0.0, 1.0)
        g2 = np.exp(-0.5 * (dlat2**2 + dlon2**2)) * east_mask
        storm2 = 27.5 * t_amp2 * g2

        w_slice = np.clip(bg + storm1 + storm2, 8.0, 43.2)
        wind[h] = w_slice.astype(np.float32)

        # Moderate rain band inside the East Coast Low & Southern Tablelands front (below 10 mm/hr threshold so wind is the primary driver)
        p_slice = 3.5 * t_amp1 * g1 * shelter + 5.2 * t_amp2 * g2
        precip[h] = np.where(p_slice >= 0.15, p_slice, 0.0).astype(np.float32)

    meta = {
        "source": "replay",
        "init_time": "2026-03-12T06:00:00Z",
        "dataset": "weathernext_3.weathernext_3_0_0_0p1deg",
        "bbox": [float(lon[0]), float(lat[0]), float(lon[-1]), float(lat[-1])],
        "grid_deg": 0.1,
        "bytes_billed": 0,
        "captured_at": "2026-10-01T08:00:00Z",
    }
    out_path = REPO / "data" / "replay" / "wn3_20260312T06Z.npz"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        out_path,
        lat=lat,
        lon=lon,
        valid_time_s=times_s,
        wind_mph=wind,
        precip_mm_hr=precip,
        temp_f=temp,
        meta=np.array(json.dumps(meta)),
    )
    print(f"Saved Australian replay cube to {out_path} with shape {wind.shape}")


def build_australian_golden_runs() -> None:
    """Run deterministic tool analysis for SHP-2001..2005 and record grounded golden runs."""
    from agent import events as ev
    from agent.advisory import AdvisorySubmission, Findings, best_option, fallback_advisory, metrics_for, validate
    from agent.views import (
        assessment_view,
        candidate_views,
        delay_views,
        provenance,
        route_view,
        samples_view,
        shipment_view,
    )
    from data import demo_fleet_now, load_demo_fleet
    from tools import delay as delay_tool
    from tools import detour as detour_tool
    from tools.assess import assess_route
    from tools.cube import ForecastCube
    from tools.facts import FactSheet, delay_payload, detour_payload, weather_payload
    from tools.routing import RouteCache, compute_routes

    cube = ForecastCube.load_npz(REPO / "data" / "replay" / "wn3_20260312T06Z.npz")
    now = demo_fleet_now(cube.meta.init_time)
    shipments = {s.shipment_id: s for s in load_demo_fleet(reference_time=now)}
    routes_cache = RouteCache(REPO / "data" / "routes_cache", mode="replay")

    golden_dir = REPO / "data" / "golden"
    golden_dir.mkdir(parents=True, exist_ok=True)

    for sid, s in shipments.items():
        fs = FactSheet(s)
        f = Findings(s)
        fs.put("forecast", cube.meta.label)
        fs.put("now", fs.time(now, weekday=True))

        recorded_events: list[ev._Event] = []
        run_id = f"au-{sid.lower().replace('-', '')}"
        seq = 0

        def emit_at(t_ms: int, event: ev._Event) -> None:
            nonlocal seq
            seq += 1
            event.run_id = run_id
            event.seq = seq
            event.t_ms = t_ms
            recorded_events.append(event)

        emit_at(
            0,
            ev.RunStarted(
                shipment=shipment_view(fs),
                mode="replay",
                provenance=provenance(cube.meta),
                model="gemini-3-flash-preview",
            ),
        )

        # Turn 1: check_route_weather
        emit_at(120, ev.ModelTurn(turn=1, phase="start", model="gemini-3-flash-preview"))
        emit_at(
            980,
            ev.ModelTurn(
                turn=1,
                phase="end",
                model="gemini-3-flash-preview",
                latency_ms=860,
                tokens=ev.TokenUsage(prompt=840, output=28, thoughts=96),
                thought=f"Checking the WeatherNext 3 forecast along {s.origin.name} → {s.destination.name} for {sid} first.",
            ),
        )
        emit_at(990, ev.ToolCall(call_id="call-1-0", name="check_route_weather", args={"shipment_id": sid}))

        plans = compute_routes(
            s.origin.location,
            s.destination.location,
            stops=[w.location for w in s.waypoints],
            cache=routes_cache,
        )
        plan = plans[0]
        a = assess_route(plan, s.origin.eta, cube, s.cargo_description)
        f.baseline, f.assessment = plan, a
        f.tools_called.append("check_route_weather")
        w_payload = weather_payload(fs, plan, a, fs.get("forecast"))

        emit_at(1040, ev.RoutePlanned(route=route_view(fs, plan, s.origin.eta)))
        emit_at(
            1080,
            ev.WeatherChecked(
                assessment=assessment_view(fs, a),
                method=fs.get("method"),
                sample_spacing_km=a.sample_spacing_km,
                samples=samples_view(a),
                hazard_zones=a.hazard_zones,
            ),
        )
        n_seg = len(a.segments)
        w_summary = f"{a.verdict} · peak {w_payload.get('peak_wind') or 'n/a'} vs {w_payload['wind_threshold']} limit"
        if n_seg:
            worst = max(a.segments, key=lambda seg: seg.peak_wind_mph)
            w_summary += f" · {n_seg} breach stretch{'es' if n_seg > 1 else ''}, worst near {worst.near_place}"
        w_summary += f" · {len(a.samples)} points"
        emit_at(1120, ev.ToolResult(call_id="call-1-0", name="check_route_weather", ok=True, latency_ms=130, summary=w_summary))

        t_cursor = 1250
        turns = 2
        if a.verdict == "hazardous":
            turns = 3
            emit_at(t_cursor, ev.ModelTurn(turn=2, phase="start", model="gemini-3-flash-preview"))
            emit_at(
                t_cursor + 1150,
                ev.ModelTurn(
                    turn=2,
                    phase="end",
                    model="gemini-3-flash-preview",
                    latency_ms=1150,
                    tokens=ev.TokenUsage(prompt=1420, output=54, thoughts=164),
                    thought=(
                        f"{plan.description} breaches the {w_payload['wind_threshold']} wind limit "
                        f"({w_payload.get('peak_wind')}). Calling compute_detour_route and evaluate_departure_delay "
                        "together to compare alternative Australian corridors against holding departure."
                    ),
                ),
            )
            emit_at(t_cursor + 1170, ev.ToolCall(call_id="call-2-0", name="compute_detour_route", args={"shipment_id": sid}))
            emit_at(t_cursor + 1180, ev.ToolCall(call_id="call-2-1", name="evaluate_departure_delay", args={"shipment_id": sid}))

            d = detour_tool.find_detour(s, plan, a, cube, cache=routes_cache)
            f.detour = d
            f.tools_called.append("compute_detour_route")
            detour_payload(fs, d)
            emit_at(
                t_cursor + 1450,
                ev.DetourComputed(
                    candidates=candidate_views(fs, d),
                    chosen_id=d.chosen_id,
                    still_hazardous=d.still_hazardous,
                    anchors_tried=d.anchors_tried,
                ),
            )
            if d.chosen_id and not d.still_hazardous:
                chosen_c = next(c for c in d.candidates if c.option_id == d.chosen_id)
                k = f"option.{d.chosen_id}"
                det_best = f"fastest safe: {chosen_c.plan.description} ({fs.get(k + '.delta_duration')})"
            else:
                det_best = "no candidate avoids the hazard"
            emit_at(
                t_cursor + 1480,
                ev.ToolResult(
                    call_id="call-2-0",
                    name="compute_detour_route",
                    ok=True,
                    latency_ms=300,
                    summary=f"{len(d.candidates)} candidate{'s' if len(d.candidates) != 1 else ''} checked · {det_best}",
                ),
            )

            dl = delay_tool.evaluate_departure_delay(s, plan, cube)
            f.delay = dl
            f.tools_called.append("evaluate_departure_delay")
            delay_payload(fs, dl)
            emit_at(
                t_cursor + 1620,
                ev.DelayEvaluated(options=delay_views(fs, dl), earliest_safe_id=dl.earliest_safe_id),
            )
            if dl.earliest_safe_id:
                o = next(o for o in dl.options if o.option_id == dl.earliest_safe_id)
                late = " (misses deadline)" if o.meets_deadline is False else ""
                dl_best = f"earliest safe: +{o.shift_hours} h, arrive {fs.time(o.arrive_at)}{late}"
            else:
                dl_best = "no safe departure inside the forecast"
            emit_at(
                t_cursor + 1650,
                ev.ToolResult(
                    call_id="call-2-1",
                    name="evaluate_departure_delay",
                    ok=True,
                    latency_ms=460,
                    summary=f"{len(dl.options)} departures checked · {dl_best}",
                ),
            )
            t_cursor += 1800

        # Final turn: submit_advisory
        sub = fallback_advisory(f, fs)
        val = validate(f, fs, sub)
        assert val.ok, f"Advisory validation failed for {sid}: {val.issues}"

        emit_at(t_cursor, ev.ModelTurn(turn=turns, phase="start", model="gemini-3-flash-preview"))
        emit_at(
            t_cursor + 1300,
            ev.ModelTurn(
                turn=turns,
                phase="end",
                model="gemini-3-flash-preview",
                latency_ms=1300,
                tokens=ev.TokenUsage(prompt=2150, output=145, thoughts=210),
                thought=f"Decision is {sub.status} ({sub.chosen_option_id}). Verifying every wind speed, highway name, and arrival time against the FactSheet before submitting the advisory.",
            ),
        )
        sub_args = {"shipment_id": sid, **sub.model_dump()}
        emit_at(t_cursor + 1320, ev.ToolCall(call_id=f"call-{turns}-0", name="submit_advisory", args=sub_args))
        emit_at(
            t_cursor + 1340,
            ev.Guardrail(
                kind="advisory_accepted",
                message=f"Decision allowed by the evidence · {val.grounded_numbers}/{val.grounded_numbers} numbers traced to tool results",
            ),
        )
        emit_at(
            t_cursor + 1350,
            ev.ToolResult(call_id=f"call-{turns}-0", name="submit_advisory", ok=True, latency_ms=18, summary=f"{sub.status} · accepted"),
        )
        emit_at(
            t_cursor + 1370,
            ev.Advisory(
                status=sub.status,
                chosen_option_id=sub.chosen_option_id,
                headline=sub.headline,
                recommendation=sub.recommendation,
                rationale=sub.rationale,
                driver_message=sub.driver_message,
                metrics=metrics_for(f, fs, sub.status, sub.chosen_option_id),
                source="gemini",
                grounded_numbers=val.grounded_numbers,
            ),
        )
        emit_at(
            t_cursor + 1400,
            ev.RunCompleted(
                status=sub.status,
                turns=turns,
                total_ms=t_cursor + 1400,
                llm_ms=3310 if turns == 3 else 2160,
                tools_ms=890 if turns == 3 else 148,
                tokens=ev.TokenUsage(prompt=4410 if turns == 3 else 2990, output=227 if turns == 3 else 173, thoughts=470 if turns == 3 else 306),
                routes_live=0,
                routes_cached=4 if turns == 3 else 1,
                model="gemini-3-flash-preview",
            ),
        )

        out_file = golden_dir / f"{sid}.jsonl"
        out_file.write_text("\n".join(e.model_dump_json(exclude_none=True) for e in recorded_events) + "\n")
        print(f"{sid}: verdict={a.verdict} -> status={sub.status} option={sub.chosen_option_id} headline={sub.headline!r}")


if __name__ == "__main__":
    update_places()
    build_australian_routes_cache()
    build_australian_forecast_cube()
    build_australian_golden_runs()
