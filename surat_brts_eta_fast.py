"""API-facing adapter for the Sitilink ASP.NET ETA scraper.

The map dataset and the LiveBusInfo.aspx dropdown do not always use the same
stop ID. Resolve by exact official stop name and, when a name is duplicated,
try the exact-name live candidates until one actually exposes BRT routes.
Never use fuzzy matching for ETA.
"""

import json
import re
from pathlib import Path

from bs4 import BeautifulSoup
from surat_brts_eta_menu import get_stops, make_session, request_get, scrape_single_stop

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
                    source_id = route_stop.get("sourceStopCode", route_stop.get("stopCode", ""))
                    if str(source_id) == str(stop_id):
                        return route_stop.get("stopName") or ""
        except Exception:
            continue
    return ""


def _live_candidates(official_id, live_stops):
    """Return only exact-ID/exact-name live candidates, strongest first."""
    official_id = str(official_id).strip()
    live_by_id = {str(s["id"]): s for s in live_stops}
    if official_id in live_by_id:
        return [live_by_id[official_id]]

    wanted = _load_stop_name(official_id)
    if not wanted:
        return []

    wanted_norm = _norm(wanted)
    wanted_compact = _compact(wanted)

    exact = [s for s in live_stops if _norm(s.get("name")) == wanted_norm]
    if exact:
        return exact

    return [s for s in live_stops if _compact(s.get("name")) == wanted_compact]


def get_eta(stop_id: str):
    """Return live BRT ETA data for a map stop ID."""
    stop_id = str(stop_id).strip()
    if not stop_id:
        raise ValueError("stop_id is required")

    session = make_session()
    response = request_get(session)
    soup = BeautifulSoup(response.text, "html.parser")
    live_stops = get_stops(soup)

    candidates = _live_candidates(stop_id, live_stops)
    if not candidates:
        wanted = _load_stop_name(stop_id)
        raise LookupError(
            f"Could not map official stop {stop_id} ({wanted}) "
            f"to the live Sitilink stop list ({len(live_stops)} stops)"
        )

    # A live stop can exist more than once under the same name. The first
    # candidate may be a non-BRT stop, so try only exact-name candidates until
    # the official BRT dropdown actually exposes routes.
    failures = []
    for candidate in candidates:
        live_id = str(candidate["id"])
        try:
            result = scrape_single_stop(live_id)
        except Exception as exc:
            failures.append(f"{live_id}: {exc}")
            continue

        if result is None:
            failures.append(f"{live_id}: stop not found")
            continue

        if result.get("routes"):
            result["requested_stop_id"] = stop_id
            result["live_stop_id"] = live_id
            return result

        failures.append(f"{live_id}: no BRT routes")

    wanted = _load_stop_name(stop_id)
    detail = "; ".join(failures[:6])
    raise LookupError(
        f"No BRT routes found for official stop {stop_id} ({wanted}). "
        f"Tried {len(candidates)} exact live-stop candidate(s)"
        + (f": {detail}" if detail else "")
    )
