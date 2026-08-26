"""Fast API-facing adapter for the Sitilink ASP.NET ETA scraper.

The map dataset and the live Sitilink WebForms page do not always use the
same stop ID.  In particular, route data can contain an older/alternate stop
code that is absent from the current LiveBusInfo dropdown.  Resolve the map
stop to the current live ID by name before scraping.
"""

import json
import re
from difflib import SequenceMatcher
from pathlib import Path

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
            stop = (data.get("stops") or {}).get(str(stop_id), {})
            if isinstance(stop, dict):
                return stop.get("name") or stop.get("stopName") or ""
        except Exception:
            pass
    return ""


def _resolve_live_id(official_id, live_stops):
    official_id = str(official_id).strip()
    live_by_id = {str(s["id"]): s for s in live_stops}

    # Prefer the real live ID when it is already current.
    if official_id in live_by_id:
        return official_id

    wanted = _load_stop_name(official_id)
    wanted_norm = _norm(wanted)
    wanted_compact = _compact(wanted)

    if not wanted_norm:
        raise LookupError(
            f"Official stop {official_id} is not in the current Sitilink stop list"
        )

    # Exact normalized name.
    for stop in live_stops:
        if _norm(stop.get("name")) == wanted_norm:
            return str(stop["id"])

    # Handle harmless spacing/punctuation differences such as
    # "PRABHU DARSHAN SOCIETY" vs "PRABHUDARSHAN SOCIETY".
    for stop in live_stops:
        if _compact(stop.get("name")) == wanted_compact:
            return str(stop["id"])

    # Finally use a conservative fuzzy match. Require both a high score and
    # a meaningful shared prefix so an unrelated station is never selected.
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

    # Discover the current live IDs once, then resolve the map's ID/name to it.
    session = make_session()
    response = request_get(session)
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(response.text, "html.parser")
    live_stops = get_stops(soup)

    live_id = _resolve_live_id(stop_id, live_stops)
    result = scrape_single_stop(live_id)
    if result is None:
        raise LookupError(f"Live Sitilink stop {live_id!r} was not found")

    # Keep the map's original ID while exposing the actual live ID for
    # debugging and future caching.
    result["requested_stop_id"] = stop_id
    result["live_stop_id"] = str(live_id)
    return result
