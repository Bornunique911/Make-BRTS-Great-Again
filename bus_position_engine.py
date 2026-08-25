import json
import math
import re
from datetime import datetime, timezone

TRANSIT_FILE = "transit_data.json"


def load_transit():
    with open(TRANSIT_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


TRANSIT = load_transit()


def normalize(text):
    """Normalize names so different systems can be matched."""
    text = str(text or "").upper()

    text = text.replace("BRTS", "")
    text = text.replace("BUS RAPID TRANSIT", "")

    text = re.sub(r"[^A-Z0-9]+", " ", text)

    return " ".join(text.split())


def distance(a_lat, a_lng, b_lat, b_lng):
    """
    Approximate distance between two coordinates in km.
    """
    R = 6371

    lat1 = math.radians(a_lat)
    lat2 = math.radians(b_lat)

    dlat = math.radians(b_lat - a_lat)
    dlng = math.radians(b_lng - a_lng)

    x = (
        math.sin(dlat / 2) ** 2
        +
        math.cos(lat1)
        * math.cos(lat2)
        * math.sin(dlng / 2) ** 2
    )

    return 2 * R * math.asin(math.sqrt(x))


def interpolate(lat1, lng1, lat2, lng2, fraction):
    """
    Linear interpolation between two GPS coordinates.
    """
    fraction = max(0.0, min(1.0, fraction))

    return (
        lat1 + (lat2 - lat1) * fraction,
        lng1 + (lng2 - lng1) * fraction,
    )


def find_route(route_number, destination=None):
    """
    Find a route in transit_data.json.

    Examples:
        11 -> 11U / 11D
        12 -> 12U / 12D
    """

    route_number = str(route_number).strip()

    candidates = []

    for route_id, route in TRANSIT["routes"].items():

        # Extract numeric portion.
        match = re.match(r"(\d+)", route_id)

        if not match:
            continue

        number = match.group(1)

        if number != route_number:
            continue

        candidates.append(
            (route_id, route)
        )

    if not candidates:
        return None

    # Try destination matching.
    if destination:

        destination_norm = normalize(destination)

        for route_id, route in candidates:

            route_norm = normalize(
                route.get("name", "")
            )

            if (
                destination_norm
                and destination_norm in route_norm
            ):
                return route_id, route

    # If we can't determine direction,
    # return the first candidate.
    return candidates[0]


def get_route_stops(route_id):
    route = TRANSIT["routes"].get(route_id)

    if not route:
        return []

    stops = []

    for stop in route.get("stops", []):

        try:
            lat = float(
                stop["stopLatitude"]
            )

            lng = float(
                stop["stopLongitude"]
            )

        except (
            KeyError,
            ValueError,
            TypeError,
        ):
            continue

        stops.append({
            "sequence": stop.get(
                "sequenceNumber"
            ),
            "id": str(
                stop.get("stopCode", "")
            ),
            "name": stop.get(
                "stopName", ""
            ),
            "lat": lat,
            "lng": lng,
        })

    stops.sort(
        key=lambda x: (
            x["sequence"]
            if x["sequence"] is not None
            else 999999
        )
    )

    return stops


def estimate_position(
    route_id,
    current_stop_index,
    next_stop_index,
    minutes_remaining,
    next_stop_eta_minutes,
):
    """
    Estimate position between two stops.

    current_stop_index:
        Stop where the bus was last observed.

    next_stop_index:
        Next stop.

    minutes_remaining:
        Estimated minutes until next stop.

    next_stop_eta_minutes:
        Total travel time estimated between
        current and next stop.
    """

    stops = get_route_stops(route_id)

    if not stops:
        return None

    if current_stop_index >= len(stops):
        return None

    if next_stop_index >= len(stops):
        return None

    current = stops[current_stop_index]
    nxt = stops[next_stop_index]

    if next_stop_eta_minutes <= 0:
        fraction = 0.5
    else:
        elapsed = (
            next_stop_eta_minutes
            - minutes_remaining
        )

        fraction = (
            elapsed
            / next_stop_eta_minutes
        )

    lat, lng = interpolate(
        current["lat"],
        current["lng"],
        nxt["lat"],
        nxt["lng"],
        fraction,
    )

    return {
        "latitude": lat,
        "longitude": lng,
        "estimated": True,
        "from_stop": current["name"],
        "to_stop": nxt["name"],
        "progress": fraction,
    }