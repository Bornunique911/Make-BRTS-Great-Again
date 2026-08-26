import json
import math
import time
import requests
from pathlib import Path

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
    response = session.get(url, timeout=20)
    response.raise_for_status()
    return response.json()


def distance_m(a, b):
    """Distance in metres between two WGS84 coordinates."""
    lat1, lng1 = a
    lat2, lng2 = b
    r = 6371000.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def choose_canonical_coordinate(observations):
    """
    Pick an actual coordinate returned by Sitilink rather than inventing a
    coordinate between observations.

    A stop can occur on many routes. A simple average/median can move a stop
    away from the road when one route record contains a bad point. We cluster
    observations within 40 m, keep the largest cluster, and choose the point
    nearest that cluster's centre (a medoid).
    """
    if not observations:
        return None
    if len(observations) == 1:
        return observations[0]

    clusters = []
    for point in observations:
        best_cluster = None
        best_distance = float("inf")
        for cluster in clusters:
            d = distance_m(point, cluster["center"])
            if d <= 40 and d < best_distance:
                best_cluster = cluster
                best_distance = d

        if best_cluster is None:
            clusters.append({"points": [point], "center": point})
        else:
            best_cluster["points"].append(point)
            points = best_cluster["points"]
            best_cluster["center"] = (
                sum(p[0] for p in points) / len(points),
                sum(p[1] for p in points) / len(points),
            )

    clusters.sort(key=lambda c: len(c["points"]), reverse=True)
    winning = clusters[0]["points"]
    centre = (
        sum(p[0] for p in winning) / len(winning),
        sum(p[1] for p in winning) / len(winning),
    )
    return min(winning, key=lambda p: distance_m(p, centre))


def valid_surat_coordinate(lat, lng):
    # Generous Surat bounding box; catches obvious 0/garbage values without
    # rejecting legitimate edge-of-city stops.
    return 20.95 <= lat <= 21.40 and 72.60 <= lng <= 73.05


def main():
    print("=" * 70)
    print("BUILDING SURAT TRANSIT DATABASE")
    print("=" * 70)

    print("\nDownloading stop list...")
    stop_response = get_json("/Stop/list")
    stops = {}
    for stop in stop_response.get("data", []):
        stop_id = str(stop["stopId"])
        stops[stop_id] = {
            "id": stop_id,
            "name": stop["stopName"],
            "lat": None,
            "lng": None,
        }
    print("Stops:", len(stops))

    print("\nDownloading route list...")
    route_response = get_json("/Route/list/")
    routes = route_response.get("data", [])
    print("Routes:", len(routes))

    route_data = {}
    coordinate_observations = {}

    for index, route in enumerate(routes, 1):
        route_id = str(route["routeId"])
        print(f"\n[{index}/{len(routes)}] {route_id} - {route.get('routeLongName', '')}")
        try:
            response = session.get(
                f"{BASE}/Route/stopcoordinates/{route_id}",
                timeout=20,
            )
            if response.status_code != 200:
                print("  HTTP", response.status_code)
                continue

            coordinates = response.json().get("data", [])
            if not coordinates:
                print("  No stops")
                continue

            cleaned = []
            for stop in coordinates:
                try:
                    lat = float(stop["stopLatitude"])
                    lng = float(stop["stopLongitude"])
                except (KeyError, TypeError, ValueError):
                    continue

                if not valid_surat_coordinate(lat, lng):
                    print(f"  Ignoring invalid coordinate for {stop.get('stopCode')}: {lat}, {lng}")
                    continue

                stop["stopLatitude"] = lat
                stop["stopLongitude"] = lng
                cleaned.append(stop)

                stop_id = str(stop["stopCode"])
                if stop_id not in stops:
                    stops[stop_id] = {
                        "id": stop_id,
                        "name": stop["stopName"],
                        "lat": None,
                        "lng": None,
                    }
                coordinate_observations.setdefault(stop_id, []).append((lat, lng))

            route_data[route_id] = {
                "route_id": route_id,
                "name": route.get("routeLongName", ""),
                # Keep exact route coordinates returned by the official API.
                # Do not replace every route with a synthetic master point.
                "stops": cleaned,
            }
            print("  ✓", len(cleaned), "stops")
        except Exception as exc:
            print("  ERROR:", exc)
        time.sleep(0.2)

    # Master coordinate = one real coordinate from the largest consistent
    # cluster of official observations for that stop ID.
    for stop_id, observations in coordinate_observations.items():
        coordinate = choose_canonical_coordinate(observations)
        if coordinate:
            stops[stop_id]["lat"], stops[stop_id]["lng"] = coordinate

    output = {
        "generated_at": time.time(),
        "stop_count": len(stops),
        "route_count": len(route_data),
        "stops": stops,
        "routes": route_data,
    }

    OUT.write_text(
        json.dumps(output, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print("\n" + "=" * 70)
    print("DONE")
    print("=" * 70)
    print("Stops:", len(stops))
    print("Routes with coordinates:", len(route_data))
    print("Saved:", OUT)


if __name__ == "__main__":
    main()
