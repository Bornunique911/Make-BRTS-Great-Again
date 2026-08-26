import json
import math
import re
import time
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


def valid_surat_coordinate(lat, lng):
    # Surat city + immediate BRT corridor area. Anything outside this box is
    # rejected before it can ever reach the map.
    return 20.95 <= lat <= 21.40 and 72.60 <= lng <= 73.05


def choose_canonical_coordinate(observations):
    """Choose an actual official coordinate from the dominant cluster."""
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


def build_name_index(stops):
    index = {}
    for stop_id, stop in stops.items():
        name = norm_name(stop.get("name"))
        if name:
            index.setdefault(name, []).append(stop_id)
    return index


def resolve_live_id(stop_code, stop_name, master_stops, name_index):
    """Resolve a route stop to the authoritative live stop ID only exactly."""
    code = str(stop_code or "")
    if code in master_stops:
        return code

    name = norm_name(stop_name)
    candidates = name_index.get(name, [])
    if len(candidates) == 1:
        return candidates[0]

    return None


def main():
    print("=" * 70)
    print("BUILDING SURAT BRTS TRANSIT DATABASE")
    print("=" * 70)

    # /Stop/list contains the complete Sitilink stop catalogue. It is NOT a
    # BRT-only list. Keep it only as the authoritative ID/name dictionary.
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

    print("Authoritative Sitilink stops:", len(master_stops))
    name_index = build_name_index(master_stops)

    route_response = get_json("/Route/list/")
    routes = route_response.get("data", [])
    print("Routes downloaded:", len(routes))

    route_data = {}
    coordinate_observations = {}
    brt_stop_ids = set()

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
                    print(
                        f"  Ignoring invalid coordinate for "
                        f"{raw_stop.get('stopCode')}: {lat}, {lng}"
                    )
                    continue

                stop_code = str(raw_stop.get("stopCode", ""))
                stop_name = raw_stop.get("stopName", stop_code)
                live_id = resolve_live_id(
                    stop_code, stop_name, master_stops, name_index
                )

                item = dict(raw_stop)
                item["stopLatitude"] = lat
                item["stopLongitude"] = lng
                item["liveStopId"] = live_id
                cleaned.append(item)

                # Only stops actually present on a downloaded BRT route become
                # map stations. This prevents the complete city-bus stop
                # catalogue from appearing as BRT stations.
                if live_id is not None:
                    brt_stop_ids.add(live_id)
                    coordinate_observations.setdefault(live_id, []).append(
                        (lat, lng)
                    )

            route_data[route_id] = {
                "route_id": route_id,
                "name": route_name,
                "stops": cleaned,
            }
            print("  ✓", len(cleaned), "route stops")
        except Exception as exc:
            print("  ERROR:", exc)
        time.sleep(0.15)

    # Give every BRT-served master stop one canonical official coordinate.
    for stop_id, observations in coordinate_observations.items():
        coordinate = choose_canonical_coordinate(observations)
        if coordinate:
            master_stops[stop_id]["lat"], master_stops[stop_id]["lng"] = coordinate

    # IMPORTANT: do not publish all /Stop/list entries. That list contains
    # stops that are not served by BRT. Publish only stops that occur on the
    # BRT route geometry we downloaded above.
    brt_stops = {
        stop_id: master_stops[stop_id]
        for stop_id in sorted(brt_stop_ids)
        if stop_id in master_stops
        and master_stops[stop_id]["lat"] is not None
        and master_stops[stop_id]["lng"] is not None
    }

    # Final safety check: every published station must be in Surat.
    invalid = []
    for stop_id, stop in brt_stops.items():
        lat = float(stop["lat"])
        lng = float(stop["lng"])
        if not valid_surat_coordinate(lat, lng):
            invalid.append((stop_id, lat, lng))

    if invalid:
        raise RuntimeError(
            f"Refusing to publish {len(invalid)} station(s) outside Surat: "
            f"{invalid[:5]}"
        )

    output = {
        "generated_at": time.time(),
        "coordinate_source": "Surat Sitilink official Route/stopcoordinates API",
        "stop_count": len(brt_stops),
        "route_count": len(route_data),
        "catalog_stop_count": len(master_stops),
        "stops": brt_stops,
        "routes": route_data,
    }

    OUT.write_text(
        json.dumps(output, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print("\n" + "=" * 70)
    print("DONE")
    print("=" * 70)
    print("BRT map stops:", len(brt_stops))
    print("Full Sitilink catalogue:", len(master_stops))
    print("Routes:", len(route_data))
    print("Saved:", OUT)


if __name__ == "__main__":
    main()
