import json
import time
import requests
from pathlib import Path

STOPS_API = "http://127.0.0.1:5000/api/stops"
OUTPUT = Path("stops_coordinates.json")

# Start small. Once it works, change to 1095.
LIMIT = 10

session = requests.Session()
session.headers.update({
    "User-Agent": "SuratBRTSHackathon/1.0"
})


def load_stops():
    r = session.get(STOPS_API, timeout=20)
    r.raise_for_status()
    return r.json()["stops"]


def geocode(name):
    params = {
        "q": f"{name}, Surat, Gujarat, India",
        "format": "jsonv2",
        "limit": 1,
    }

    r = session.get(
        "https://nominatim.openstreetmap.org/search",
        params=params,
        timeout=20,
    )
    r.raise_for_status()

    results = r.json()

    if not results:
        return None

    return {
        "lat": float(results[0]["lat"]),
        "lng": float(results[0]["lon"]),
        "display_name": results[0]["display_name"],
    }


def main():
    stops = load_stops()

    existing = {}

    if OUTPUT.exists():
        existing = json.loads(
            OUTPUT.read_text(encoding="utf-8")
        )

    processed = 0

    for stop in stops:
        stop_id = stop["id"]

        if stop_id in existing:
            continue

        if processed >= LIMIT:
            break

        print(
            f"[{processed + 1}/{LIMIT}] "
            f"{stop_id}: {stop['name']}"
        )

        try:
            result = geocode(stop["name"])

            if result:
                existing[stop_id] = {
                    "id": stop_id,
                    "name": stop["name"],
                    **result,
                    "source": "OpenStreetMap Nominatim",
                }

                print(
                    f"   -> {result['lat']}, "
                    f"{result['lng']}"
                )
            else:
                existing[stop_id] = {
                    "id": stop_id,
                    "name": stop["name"],
                    "lat": None,
                    "lng": None,
                    "source": None,
                }

                print("   -> NOT FOUND")

            OUTPUT.write_text(
                json.dumps(
                    existing,
                    indent=2,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

        except Exception as e:
            print(f"   ERROR: {e}")

        processed += 1

        # Be polite to the public geocoder.
        time.sleep(1.2)

    print()
    print(f"Saved {len(existing)} stops to {OUTPUT}")


if __name__ == "__main__":
    main()