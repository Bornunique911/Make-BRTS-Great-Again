#!/usr/bin/env python3
import json
import sys
from pathlib import Path
from flask import Flask, jsonify, render_template
from flask_cors import CORS

app = Flask(__name__, template_folder="templates", static_folder="static")
CORS(app, resources={r"/api/*": {"origins": "*"}})

# ----- Directories (Vercel: /var/task) -----
APP_DIR = Path(__file__).resolve().parent          # /var/task/flask/app
REPO_DIR = APP_DIR.parent.parent                   # /var/task

# ----- Candidate paths for data files -----
TRANSIT_CANDIDATES = [
    REPO_DIR / "transit_data.json",
    REPO_DIR / "static" / "transit_data.json",
    APP_DIR / "static" / "transit_data.json",
    APP_DIR / "static" / "assets" / "transit_data.json",
]

MOCK_CANDIDATES = [
    REPO_DIR / "mock_eta_data.json",
    APP_DIR / "mock_eta_data.json",
]

def load_json_safe(candidates, desc):
    """
    Try each path; return loaded JSON dict, or None if none found or parse fails.
    Logs to stderr for Vercel logs.
    """
    for p in candidates:
        if p.is_file():
            try:
                with p.open(encoding="utf-8") as f:
                    data = json.load(f)
                    print(f"[INFO] Loaded {desc} from {p}", file=sys.stderr)
                    return data
            except Exception as e:
                print(f"[ERROR] Failed to parse {p}: {e}", file=sys.stderr)
    print(f"[ERROR] {desc} not found in any candidate: {candidates}", file=sys.stderr)
    return None

# ----- Load data with fallback -----
TRANSIT = load_json_safe(TRANSIT_CANDIDATES, "transit_data.json") or {"stops": {}, "routes": {}}
MOCK_ETA = load_json_safe(MOCK_CANDIDATES, "mock_eta_data.json") or {}

# Log sample keys for debugging
if MOCK_ETA:
    sample = list(MOCK_ETA.keys())[:5]
    print(f"[INFO] Loaded mock data for {len(MOCK_ETA)} stops. Sample keys: {sample}", file=sys.stderr)
else:
    print("[WARN] No mock data loaded – ETAs will return 404.", file=sys.stderr)

# ----- Helper for route stops -----
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

# ----- API Endpoints -----
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
        "transit_loaded": len(TRANSIT.get("stops", {})) > 0,
        "mock_loaded": len(MOCK_ETA) > 0,
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