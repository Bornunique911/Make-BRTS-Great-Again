"""Vercel entrypoint for the Surat BRTS Flask application."""

import json
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path

from flask import jsonify

APP_DIR = Path(__file__).resolve().parent.parent / "flask" / "app"
REPO_DIR = APP_DIR.parent.parent
sys.path.insert(0, str(APP_DIR))

import app as flask_app  # noqa: E402

app = flask_app.app


def _normalise(value):
    value = str(value or "").upper()
    value = value.replace("&", " AND ")
    value = re.sub(r"\bBRTS\b", "", value)
    value = re.sub(r"[^A-Z0-9]+", " ", value)
    return " ".join(value.split())


def _load_master_stops():
    """Load the 1095-stop master list used by the live Sitilink API."""
    candidates = [
        REPO_DIR / "stops_master.json",
        APP_DIR / "stops_master.json",
    ]
    for path in candidates:
        if path.is_file():
            with path.open(encoding="utf-8") as f:
                data = json.load(f)
            rows = data.get("d", []) if isinstance(data, dict) else []
            return rows
    return []


_MASTER_STOPS = _load_master_stops()
_MASTER_BY_NAME = {}
for row in _MASTER_STOPS:
    name = _normalise(row.get("stopname"))
    stop_id = row.get("stopid")
    if name and stop_id is not None:
        _MASTER_BY_NAME.setdefault(name, str(stop_id))


def _official_stop_name(stop_id):
    stop = flask_app.TRANSIT.get("stops", {}).get(str(stop_id), {})
    if not isinstance(stop, dict):
        return ""
    return stop.get("stopName") or stop.get("stopname") or stop.get("name") or stop.get("stop_name") or ""


def _resolve_live_id(official_id):
    official_id = str(official_id)

    # First try the ID directly. This preserves the original behaviour for
    # stops where both datasets happen to use the same identifier.
    session = flask_app.make_session()
    response = flask_app.request_get(session)
    soup = flask_app.BeautifulSoup(response.text, "html.parser")
    live_stops = flask_app.get_stops(soup)
    live_ids = {str(s["id"]): s for s in live_stops}
    if official_id in live_ids:
        return official_id, live_ids[official_id].get("name", "")

    official_name = _official_stop_name(official_id)
    wanted = _normalise(official_name)
    if not wanted:
        raise ValueError(f"Official stop {official_id} has no stop name in transit_data.json")

    # The repository's stops_master.json was built from the official
    # Sitilink stop API and contains the live stop IDs. Prefer this exact
    # name mapping before attempting any fuzzy matching against HTML.
    master_id = _MASTER_BY_NAME.get(wanted)
    if master_id:
        live = live_ids.get(master_id)
        if live:
            return master_id, live.get("name", official_name)

    # Exact normalised name against the live dropdown.
    for live in live_stops:
        if _normalise(live.get("name")) == wanted:
            return str(live["id"]), live.get("name", official_name)

    # Conservative fuzzy fallback. Never accept a weak match.
    best = None
    best_score = 0.0
    for live in live_stops:
        candidate = _normalise(live.get("name"))
        if not candidate:
            continue
        score = SequenceMatcher(None, wanted, candidate).ratio()
        if score > best_score:
            best_score = score
            best = live
    if best is not None and best_score >= 0.92:
        return str(best["id"]), best.get("name", official_name)

    raise ValueError(
        f"Could not map official stop {official_id} ({official_name}) "
        f"to the live Sitilink stop list ({len(live_stops)} stops)"
    )


def _api_eta_proxy(stop_id):
    try:
        live_id, live_name = _resolve_live_id(stop_id)
        data = flask_app.get_cached_stop(live_id)
        data = dict(data)
        data["stop_id"] = str(stop_id)
        data["live_stop_id"] = str(live_id)
        data["stop_name"] = _official_stop_name(stop_id) or live_name
        return jsonify(data)
    except Exception as exc:
        print(f"[ETA] official={stop_id} error={exc!r}")
        return jsonify({
            "error": "Failed to fetch ETA",
            "stop_id": str(stop_id),
            "details": str(exc),
        }), 500


# Keep the original Flask application, map, routes, station coordinates and
# static files untouched. Only translate the frontend's official stop ID to
# the live Sitilink ID at the Vercel boundary.
if "api_eta" in app.view_functions:
    app.view_functions["api_eta"] = _api_eta_proxy
