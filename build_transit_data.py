import json
import math
import re
import time
from difflib import SequenceMatcher
from pathlib import Path

import requests

BASE = "https://suratcitytransportapp.co.in"
OUT = Path("transit_data.json")

session = requests.Session()
session.headers.update({
    "User-Agent": "Dart/2.19 (dart:io)",
    "Accept": "application/json",
})


def get_json(path):
    url = BASE + path
    print("GET", url)
    response = session.get(url, timeout=30)
    response.raise_for_status()
    return response.json()


def distance_m(a, b):
    lat1, lng1 = a
    lat2, lng2 = b
    r = 6371000.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def norm_name(value):
    value = str(value or "").upper().replace("&", " AND ")
    value = re.sub(r"\bBRTS\b", "", value)
    value = re.sub(r"[^A-Z0-9]+", " ", value)
    return " ".join(value.split())


def compact_name(value):
    return norm_name(value).replace(" ", "")


def valid_surat_coordinate(lat, lng):
    return 20.95 <= lat <= 21.40 and 72.60 <= lng <= 73.05


def choose_canonical_coordinate(observations):
    """Choose a real official coordinate from the dominant 50 m cluster."""
    if not observations:
        return None
    if len(observations) == 1:
        return observations[0]

    clusters = []
    for point in observations:
        best = None
        best_distance = float("inf")
        for cluster in clusters:
            d = distance_m(point, cluster["center"])
            if d <= 50 and d < best_distance:
                best = cluster
                best_distance = d
        if best is None:
            clusters.append({"points": [point], "center": point})
        else:
            best["points"].append(point)
            pts = best["points"]
            best["center"] = (
                sum(p[0] for p in pts) / len(pts),
                sum(p[1] for p in pts) / len(pts),
            )

    winning = max(clusters, key=lambda c: len(c["points"]))
    centre = winning["center"]
    return min(winning["points"], key=lambda p: distance_m(p, centre))


def build_name_indexes(stops):
    exact = {}
    compact = {}
    for stop_id, stop in stops.items():
        n = norm_name(stop.get("name"))
        c = compact_name(stop.get("name"))
        if n:
            exact.setdefault(n, []).append(stop_id)
        if c:
            compact.setdefault(c, []).append(stop_id)
    return exact, compact


def resolve_master_stop_id(stop_code, stop_name, master, exact, compact):
    code = str(stop_code)
    if code in master:
        return code

    n = norm_name(stop_name)
    candidates = exact.get(n, [])
    if len(candidates) == 1:
        return candidates[0]

    c = compact_name(stop_name)
    candidates = compact.get(c, [])
    if len(candidates) == 1:
        return candidates[0]

    best_id = None
    best_score = 0.0
    for master_id, master_stop in master.items():
        candidate = norm_name(master_stop.get("name"))
        if not candidate or not n:
            continue
        score = SequenceMatcher(None, n, candidate).ratio()
        if score > best_score:
            best_score = score
            best_id = master_id
    if best_id is not None and best_score >= 0.96:
        return best_id
    return None


def main():
    print("=" * 70)
    print("BUILDING SURAT SITILINK TRANSIT DATABASE")
    print("=" * 70)

    # /Stop/list is the authoritative station list used by LiveBusInfo.aspx.
    # Route-only stop codes must NOT be promoted to master map stations.
    stop_response = get_json("/Stop/list")
    master_stops = {}
    for stop in stop_response.get("data", []):
        stop_id = str(stop["stopId"])
        master_stops[stop_id] = {
            "id": stop_id,
            "name": stop.get("stopName", stop_id),
            "lat": None,
            "lng": None,
            "live_id": stop_id,
        }

    print("Authoritative stops:", len(master_stops))
    exact_names, compact_names = build_name_indexes(master_stops)

    route_response = get_json("/Route/list/")
    routes = route_response.get("data", [])
    print("Routes:", len(routes))

    route_data = {}
    coordinate_observations = {}

    for index, route in enumerate(routes, 1):
        route_id = str(route["routeId"])
        route_name = route.get("routeLongName", "")
        print(f"\n[{index}/{len(routes)}] {route_id} - {route_name}")
        try:
            response = session.get(
                f"{BASE}/Route/stopcoordinates/{route_id}",
                timeout=30,
            )
            response.raise_for_status()
            coordinates = response.json().get("data", [])
            cleaned = []

            for raw_stop in coordinates:
                try:
                    lat = float(raw_stop["stopLatitude"])
                    lng = float(raw_stop["stopLongitude"])
                except (KeyError, TypeError, ValueError):
                    continue

                if not valid_surat_coordinate(lat, lng):
                    print(f"  Ignoring invalid coordinate for {raw_stop.get('stopCode')}: {lat}, {lng}")
                    continue

                stop_code = str(raw_stop.get("stopCode", ""))
                stop_name = raw_stop.get("stopName", stop_code)
                live_id = resolve_master_stop_id(
                    stop_code, stop_name, master_stops, exact_names, compact_names
                )

                item = dict(raw_stop)
                item["stopLatitude"] = lat
                item["stopLongitude"] = lng
                item["liveStopId"] = live_id
                cleaned.append(item)

                if live_id is not None:
                    coordinate_observations.setdefault(live_id, []).append((lat, lng))

            route_data[route_id] = {
                "route_id": route_id,
                "name": route_name,
                "stops": cleaned,
            }
            print("  ✓", len(cleaned), "route stops")
        except Exception as exc:
            print("  ERROR:", exc)
        time.sleep(0.15)

    for stop_id, observations in coordinate_observations.items():
        coordinate = choose_canonical_coordinate(observations)
        if coordinate:
            master_stops[stop_id]["lat"], master_stops[stop_id]["lng"] = coordinate

    missing = sum(
        1 for stop in master_stops.values()
        if stop["lat"] is None or stop["lng"] is None
    )

    output = {
        "generated_at": time.time(),
        "coordinate_source": "Surat Sitilink official Route/stopcoordinates API",
        "stop_count": len(master_stops),
        "route_count": len(route_data),
        "stops": master_stops,
        "routes": route_data,
    }

    OUT.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")

    print("\n" + "=" * 70)
    print("DONE")
    print("=" * 70)
    print("Master stops:", len(master_stops))
    print("Stops without official route coordinates:", missing)
    print("Routes with coordinates:", len(route_data))
    print("Saved:", OUT)


if __name__ == "__main__":
    main()
