import requests
import json
import re

BASE = "https://suratcitytransportapp.co.in"

s = requests.Session()
s.headers.update({
    "User-Agent": "Dart/2.19 (dart:io)",
    "Accept": "application/json",
})

print("1. Downloading mobile-app stops...")
r = s.get(BASE + "/Stop/list", timeout=20)
r.raise_for_status()

mobile_stops = r.json()["data"]

print("Mobile stops:", len(mobile_stops))

print("\n2. Downloading mobile-app routes...")
r = s.get(BASE + "/Route/list/", timeout=20)
r.raise_for_status()

routes = r.json()["data"]

print("Mobile routes:", len(routes))


# ------------------------------------------------------------
# Find routes corresponding to our known BRTS routes.
# ------------------------------------------------------------

print("\n3. Searching for route 11 / UDHANA / SACHIN...")

matches = []

for route in routes:
    text = " ".join([
        str(route.get("routeId", "")),
        str(route.get("routeShortName", "")),
        str(route.get("routeLongName", "")),
    ]).upper()

    if (
        "UDHANA" in text
        or "SACHIN" in text
        or re.search(r"\b11\b", text)
    ):
        matches.append(route)


for route in matches:
    print(json.dumps(route, indent=2))


# ------------------------------------------------------------
# Try every matching route ID against stopcoordinates.
# ------------------------------------------------------------

print("\n4. Testing coordinate endpoint...")

for route in matches:

    route_id = route["routeId"]

    url = BASE + "/Route/stopcoordinates/" + str(route_id)

    print("\nGET", url)

    try:
        response = s.get(url, timeout=20)

        print("HTTP:", response.status_code)

        if response.status_code == 200:

            data = response.json()

            print(
                json.dumps(
                    data,
                    indent=2,
                    ensure_ascii=False
                )[:5000]
            )

    except Exception as e:
        print("ERROR:", e)


# ------------------------------------------------------------
# Also look for KHARWARNAGAR in the mobile stop database.
# ------------------------------------------------------------

print("\n5. Finding KHARWARNAGAR...")

for stop in mobile_stops:

    name = stop.get("stopName", "").upper()

    if "KHARWARNAGAR" in name or "KHARWAR NAGAR" in name:

        print(
            json.dumps(
                stop,
                indent=2,
                ensure_ascii=False
            )
        )


print("\nDONE")