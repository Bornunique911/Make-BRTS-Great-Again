import json
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

    response = session.get(
        url,
        timeout=20
    )

    response.raise_for_status()

    return response.json()


def main():

    print("=" * 70)
    print("BUILDING SURAT TRANSIT DATABASE")
    print("=" * 70)

    # ---------------------------------------------------------
    # 1. Stops
    # ---------------------------------------------------------

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


    # ---------------------------------------------------------
    # 2. Routes
    # ---------------------------------------------------------

    print("\nDownloading route list...")

    route_response = get_json("/Route/list/")

    routes = route_response.get("data", [])

    print("Routes:", len(routes))


    # ---------------------------------------------------------
    # 3. Download coordinates for every route
    # ---------------------------------------------------------

    route_data = {}

    for index, route in enumerate(routes, 1):

        route_id = str(route["routeId"])

        print(
            f"\n[{index}/{len(routes)}] "
            f"{route_id} - "
            f"{route.get('routeLongName', '')}"
        )

        try:

            response = session.get(
                f"{BASE}/Route/stopcoordinates/{route_id}",
                timeout=20
            )

            if response.status_code != 200:
                print(
                    "  HTTP",
                    response.status_code
                )
                continue

            payload = response.json()

            coordinates = payload.get(
                "data",
                []
            )

            if not coordinates:
                print("  No stops")
                continue

            route_data[route_id] = {
                "route_id": route_id,
                "name": route.get(
                    "routeLongName",
                    ""
                ),
                "stops": coordinates,
            }

            # Add coordinates to master stop database
            for stop in coordinates:

                stop_id = str(
                    stop["stopCode"]
                )

                if stop_id not in stops:
                    stops[stop_id] = {
                        "id": stop_id,
                        "name": stop["stopName"],
                        "lat": None,
                        "lng": None,
                    }

                stops[stop_id]["lat"] = float(
                    stop["stopLatitude"]
                )

                stops[stop_id]["lng"] = float(
                    stop["stopLongitude"]
                )

            print(
                "  ✓",
                len(coordinates),
                "stops"
            )

        except Exception as e:

            print(
                "  ERROR:",
                e
            )

        # Don't hammer the server
        time.sleep(0.2)


    # ---------------------------------------------------------
    # 4. Save
    # ---------------------------------------------------------

    output = {
        "generated_at": time.time(),
        "stop_count": len(stops),
        "route_count": len(route_data),
        "stops": stops,
        "routes": route_data,
    }

    OUT.write_text(
        json.dumps(
            output,
            indent=2,
            ensure_ascii=False
        ),
        encoding="utf-8"
    )

    print("\n" + "=" * 70)
    print("DONE")
    print("=" * 70)

    print(
        "Stops:",
        len(stops)
    )

    print(
        "Routes with coordinates:",
        len(route_data)
    )

    print(
        "Saved:",
        OUT
    )


if __name__ == "__main__":
    main()