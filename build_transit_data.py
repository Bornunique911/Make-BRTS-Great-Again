import json
import time
import requests
from pathlib import Path
from statistics import median

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

            route_data[route_id] = {
                "route_id": route_id,
                "name": route.get("routeLongName", ""),
                "stops": coordinates,
            }

            for stop in coordinates:
                stop_id = str(stop["stopCode"])
                lat = float(stop["stopLatitude"])
                lng = float(stop["stopLongitude"])

                if stop_id not in stops:
                    stops[stop_id] = {
                        "id": stop_id,
                        "name": stop["stopName"],
                        "lat": None,
                        "lng": None,
                    }

                coordinate_observations.setdefault(stop_id, []).append((lat, lng))

            print("  ✓", len(coordinates), "stops")
        except Exception as exc:
            print("  ERROR:", exc)
        time.sleep(0.2)

    # A stop can appear in several routes. Do not let the last route overwrite
    # the station's location. Use the median observation, which is robust to a
    # single bad coordinate and keeps one physical station in one place.
    for stop_id, observations in coordinate_observations.items():
        if not observations:
            continue
        stops[stop_id]["lat"] = median(point[0] for point in observations)
        stops[stop_id]["lng"] = median(point[1] for point in observations)

    # Replace route-level coordinates with the same canonical station
    # coordinate so a station does not jump between routes.
    for route in route_data.values():
        for stop in route["stops"]:
            stop_id = str(stop["stopCode"])
            canonical = stops.get(stop_id)
            if canonical and canonical["lat"] is not None:
                stop["stopLatitude"] = canonical["lat"]
                stop["stopLongitude"] = canonical["lng"]

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
