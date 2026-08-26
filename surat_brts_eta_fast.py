"""Fast API-facing adapter for the Sitilink ASP.NET ETA scraper.

The map dataset and the live Sitilink WebForms page do not always use the
same stop ID. Route data can contain an older/alternate stop code that is
absent from the current LiveBusInfo dropdown. Resolve by stop name first.
"""

import json
import re
from difflib import SequenceMatcher
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

            # Route-only IDs are deliberately not promoted to the master stop
            # list. Still allow the ETA endpoint to resolve them by their
            # official route stop name.
            for route in (data.get("routes") or {}).values():
                for route_stop in route.get("stops", []):
                    if str(route_stop.get("sourceStopCode", route_stop.get("stopCode", ""))) == str(stop_id):
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

    for stop in live_stops:
        if _norm(stop.get("name")) == wanted_norm:
            return str(stop["id"])

    for stop in live_stops:
        if _compact(stop.get("name")) == wanted_compact:
            return str(stop["id"])

    best_id = None
    best_score = 0.0
    for stop in live_stops:
        candidate = _norm(stop.get("name"))
        if not candidate:
            continue
        score = SequenceMatcher(None, wanted_norm, candidate).ratio()
        prefix = min(len(wanted_norm), len(candidate), 12)
        prefix_ok = wanted_norm[:prefix] == candidate[:prefix]
        if prefix_ok and score > best_score:
            best_score = score
            best_id = str(stop["id"])

    if best_id is not None and best_score >= 0.94:
        return best_id

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
