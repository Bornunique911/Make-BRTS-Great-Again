#!/usr/bin/env python3
import json
import sys
from pathlib import Path
from flask import Flask, jsonify, render_template
from flask_cors import CORS

app = Flask(__name__, template_folder="templates", static_folder="static")
CORS(app, resources={r"/api/*": {"origins": "*"}})

APP_DIR = Path(__file__).resolve().parent
REPO_DIR = APP_DIR.parent.parent

def load_json_file(candidates, file_desc):
    """Try each candidate path and return loaded JSON, or None."""
    for path in candidates:
        if path.is_file():
            try:
                with path.open(encoding="utf-8") as f:
                    print(f"[LOAD] {file_desc} loaded from {path}", file=sys.stderr)
                    return json.load(f)
            except Exception as e:
                print(f"[ERROR] Failed to parse {path}: {e}", file=sys.stderr)
    print(f"[ERROR] {file_desc} not found in any candidate: {candidates}", file=sys.stderr)
    return None

# Load transit_data.json
TRANSIT_CANDIDATES = [
    REPO_DIR / "transit_data.json",
    APP_DIR / "static" / "transit_data.json",
    APP_DIR / "static" / "assets" / "transit_data.json",
]
TRANSIT = load_json_file(TRANSIT_CANDIDATES, "transit_data.json")
if TRANSIT is None:
    TRANSIT = {"stops": {}, "routes": {}}

# Load mock_eta_data.json
MOCK_CANDIDATES = [
    REPO_DIR / "mock_eta_data.json",
    APP_DIR / "mock_eta_data.json",
]
MOCK_ETA = load_json_file(MOCK_CANDIDATES, "mock_eta_data.json")
if MOCK_ETA is None:
    MOCK_ETA = {}

# Helper
def get_local_route_stops(route_id):
    route = TRANSIT.get("routes", {}).get(str(route_id))
    if not route:
        return []
    result = []
    for stop in route.get("stops", []):
        try:
            lat = float(stop["stopLatitude"])
            lng = float(stop["stopLongitude"])
        except (KeyError, TypeError, ValueError):
            continue
        stop_id = stop.get("liveStopId") or stop.get("stopCode", "")
        result.append({
            "sequence": stop.get("sequenceNumber"),
            "id": str(stop_id),
            "source_id": str(stop.get("stopCode", "")),
            "name": stop.get("stopName", ""),
            "lat": lat,
            "lng": lng,
        })
    return sorted(result, key=lambda x: x["sequence"] if x["sequence"] is not None else 999999)

@app.get("/")
def home():
    return render_template("index.html")

@app.get("/api/health")
def api_health():
    return jsonify({
        "status": "ok",
        "transit_data": {"stops": len(TRANSIT.get("stops", {})), "routes": len(TRANSIT.get("routes", {}))},
        "mock_eta": {"stops": len(MOCK_ETA)},
        "eta_source": "mock",
    })

@app.get("/api/debug/files")
def debug_files():
    """Return which files are present (debugging helper)."""
    return jsonify({
        "transit_file": str(TRANSIT_CANDIDATES[0]) if TRANSIT_CANDIDATES[0].is_file() else "not found",
        "mock_file": str(MOCK_CANDIDATES[0]) if MOCK_CANDIDATES[0].is_file() else "not found",
        "cwd": str(Path.cwd()),
        "app_dir": str(APP_DIR),
        "repo_dir": str(REPO_DIR),
    })

@app.get("/api/stops")
def api_stops():
    stops = TRANSIT.get("stops", {})
    return jsonify({
        "stops": [{"id": str(i), **s} for i, s in stops.items()],
        "count": len(stops),
        "source": "transit_data.json",
    })

@app.get("/api/routes")
def api_routes():
    routes = TRANSIT.get("routes", {})
    return jsonify({
        "routes": [
            {"id": str(i), "name": r.get("name", ""), "stop_count": len(r.get("stops", []))}
            for i, r in routes.items()
        ],
        "count": len(routes),
    })

@app.get("/api/routes/<route_id>/stops")
def api_route_stops(route_id):
    route = TRANSIT.get("routes", {}).get(str(route_id))
    stops = get_local_route_stops(route_id)
    if not route or not stops:
        return jsonify({"error": "Route not found or has no valid coordinates", "route_id": route_id}), 404
    return jsonify({"route_id": route_id, "name": route.get("name", ""), "stops": stops})

@app.get("/api/eta/<stop_id>")
def api_eta(stop_id):
    stop_id = str(stop_id)
    data = MOCK_ETA.get(stop_id)
    if data is None:
        return jsonify({"error": "Stop not found in mock data", "stop_id": stop_id}), 404
    return jsonify(data)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)