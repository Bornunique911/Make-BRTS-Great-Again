"""Fast API-facing adapter for the Sitilink ASP.NET ETA scraper.

Map stop IDs and the live Sitilink WebForms stop IDs can differ. Resolve an
alternate map ID only through an exact official stop name match; never use
fuzzy matching because a wrong match returns another station's ETA.
"""

import json
import re
from pathlib import Path

from bs4 import BeautifulSoup
from surat_brts_eta_menu import get_stops, request_get, make_session, scrape_single_stop


DATA_CANDIDATES = [
    Path(__file__).with_name("transit_data.json"),
    Path(__file__).parent / "transit_data.json",
]


def _norm(value):
    value = str(value or "").upper().replace("&", " AND ")
    value = re.sub(r"\bBRTS\b", "", value)
    value = re.sub(r"[^A-Z0-9]+", " ", value)
    return " ".join(value.split())


def _compact(value):
    return _norm(value).replace(" ", "")


def _load_stop_name(stop_id):
    for path in DATA_CANDIDATES:
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            stops = data.get("stops") or {}
            stop = stops.get(str(stop_id))
            if isinstance(stop, dict):
                name = stop.get("name") or stop.get("stopName") or ""
                if name:
                    return name

            for route in (data.get("routes") or {}).values():
                for route_stop in route.get("stops", []):
                    if str(route_stop.get("stopCode", "")) == str(stop_id):
                        return route_stop.get("stopName") or ""
        except Exception:
            continue
    return ""


def _resolve_live_id(official_id, live_stops):
    official_id = str(official_id).strip()
    live_by_id = {str(s["id"]): s for s in live_stops}

    if official_id in live_by_id:
        return official_id

    wanted = _load_stop_name(official_id)
    wanted_norm = _norm(wanted)
    wanted_compact = _compact(wanted)

    if not wanted_norm:
        raise LookupError(
            f"Official stop {official_id} is not in the current Sitilink stop list"
        )

    exact = [
        stop for stop in live_stops
        if _norm(stop.get("name")) == wanted_norm
    ]
    if len(exact) == 1:
        return str(exact[0]["id"])

    compact = [
        stop for stop in live_stops
        if _compact(stop.get("name")) == wanted_compact
    ]
    if len(compact) == 1:
        return str(compact[0]["id"])

    raise LookupError(
        f"Could not map official stop {official_id} ({wanted}) "
        f"to the current Sitilink stop list ({len(live_stops)} stops)"
    )


def get_eta(stop_id: str):
    """Return live BRT ETA data for a map stop ID."""
    stop_id = str(stop_id).strip()
    if not stop_id:
        raise ValueError("stop_id is required")

    session = make_session()
    response = request_get(session)
    soup = BeautifulSoup(response.text, "html.parser")
    live_stops = get_stops(soup)

    live_id = _resolve_live_id(stop_id, live_stops)
    result = scrape_single_stop(live_id)
    if result is None:
        raise LookupError(f"Live Sitilink stop {live_id!r} was not found")

    result["requested_stop_id"] = stop_id
    result["live_stop_id"] = str(live_id)
    return result
