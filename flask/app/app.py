import json
import re
import threading
import time
from difflib import SequenceMatcher
from pathlib import Path

import requests
import urllib3
from bs4 import BeautifulSoup
from flask import Flask, jsonify, render_template

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

URL = "https://www.suratsitilink.org/LiveBusInfo.aspx"
CACHE_SECONDS = 15
REQUEST_TIMEOUT = 30
BRT_SERVICE_TYPE = "1"
STOP_SELECT = "ctl00$ContentPlaceHolder1$ddlstops"
SERVICE_SELECT = "ctl00$ContentPlaceHolder1$ddlservicetype"
ROUTE_SELECT = "ctl00$ContentPlaceHolder1$ddlroute"
ETA_TABLE_ID = "ContentPlaceHolder1_ETATableDetail"

app = Flask(__name__, template_folder="templates", static_folder="static")
cache = {}
cache_lock = threading.Lock()
APP_DIR = Path(__file__).resolve().parent
REPO_DIR = APP_DIR.parent.parent

# IMPORTANT: use the original transit dataset first. The copy under assets is
# only a browser-facing duplicate and must never replace the canonical data.
TRANSIT_CANDIDATES = [
    APP_DIR / "static" / "transit_data.json",
    APP_DIR / "static" / "assets" / "transit_data.json",
    REPO_DIR / "transit_data.json",
]


def _find_transit_file():
    for path in TRANSIT_CANDIDATES:
        path = path.resolve()
        if path.is_file():
            return path
    raise FileNotFoundError("transit_data.json not found. Checked:\n" + "\n".join(f"- {p.resolve()}" for p in TRANSIT_CANDIDATES))


TRANSIT_FILE = _find_transit_file()
with TRANSIT_FILE.open(encoding="utf-8") as f:
    TRANSIT = json.load(f)
if not isinstance(TRANSIT.get("stops"), dict) or not isinstance(TRANSIT.get("routes"), dict):
    raise ValueError(f"Invalid transit dataset: {TRANSIT_FILE}")
print(f"[DATA] Loaded {len(TRANSIT['stops'])} stops and {len(TRANSIT['routes'])} routes from {TRANSIT_FILE}")


def _norm(value):
    value = str(value or "").upper().replace("&", " AND ")
    value = re.sub(r"\bBRTS\b", "", value)
    value = re.sub(r"[^A-Z0-9]+", " ", value)
    return " ".join(value.split())


def _stop_name(stop_id):
    stop = TRANSIT["stops"].get(str(stop_id), {})
    if not isinstance(stop, dict):
        return ""
    return stop.get("stopName") or stop.get("stopname") or stop.get("name") or stop.get("stop_name") or ""


def _load_master_stops():
    for path in (REPO_DIR / "stops_master.json", APP_DIR / "stops_master.json"):
        if path.is_file():
            try:
                with path.open(encoding="utf-8") as f:
                    data = json.load(f)
                rows = data.get("d", []) if isinstance(data, dict) else []
                if isinstance(rows, list):
                    return rows
            except Exception as exc:
                print("[STOP MAP] master list load failed:", repr(exc))
    return []


MASTER_STOPS = _load_master_stops()
MASTER_BY_NAME = {}
for row in MASTER_STOPS:
    name = _norm(row.get("stopname"))
    sid = row.get("stopid")
    if name and sid is not None:
        MASTER_BY_NAME.setdefault(name, str(sid))
print(f"[STOP MAP] loaded {len(MASTER_STOPS)} master stops")


def make_session():
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/139.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": URL,
        "Connection": "keep-alive",
    })
    return s


def get_form_data(soup):
    data = {}
    form = soup.find("form")
    if not form:
        return data
    for element in form.find_all(["input", "select", "textarea"]):
        name = element.get("name")
        if not name:
            continue
        if element.name == "input":
            typ = element.get("type", "text").lower()
            if typ in {"submit", "button", "image", "file"}:
                continue
            if typ in {"checkbox", "radio"} and not element.has_attr("checked"):
                continue
            data[name] = element.get("value", "")
        elif element.name == "select":
            selected = element.find("option", selected=True)
            if selected is not None:
                data[name] = selected.get("value", "")
        else:
            data[name] = element.get_text(strip=True)
    return data


def request_get(session):
    response = session.get(URL, timeout=REQUEST_TIMEOUT, verify=False)
    response.raise_for_status()
    return response


def request_post(session, soup, event_target, values):
    payload = get_form_data(soup)
    payload["__EVENTTARGET"] = event_target
    payload["__EVENTARGUMENT"] = ""
    payload.update(values)
    response = session.post(URL, data=payload, timeout=REQUEST_TIMEOUT, verify=False)
    response.raise_for_status()
    return response


def find_select(soup, name):
    return soup.find("select", attrs={"name": name})


def get_stops(soup):
    select = find_select(soup, STOP_SELECT)
    if not select:
        return []
    result = []
    for option in select.find_all("option"):
        value = option.get("value")
        text = option.get_text(" ", strip=True)
        if value and value not in {"0", "-1"}:
            result.append({"id": value, "name": text})
    return result


def get_routes(soup):
    select = find_select(soup, ROUTE_SELECT)
    if not select:
        return []
    result = []
    for option in select.find_all("option"):
        value = option.get("value")
        text = option.get_text(" ", strip=True)
        if value and value not in {"0", "-1"} and "select" not in text.lower():
            result.append({"id": value, "name": text})
    return result


def resolve_live_stop_id(official_id, live_stops):
    official_id = str(official_id)
    live_by_id = {str(x["id"]): x for x in live_stops}
    if official_id in live_by_id:
        return official_id, live_by_id[official_id]["name"]

    official_name = _stop_name(official_id)
    wanted = _norm(official_name)
    if not wanted:
        raise ValueError(f"Official stop {official_id} has no name in transit_data.json")

    # stops_master.json is the authoritative bridge between the official
    # transit dataset and the Sitilink stop IDs. Use it before fuzzy matching.
    master_id = MASTER_BY_NAME.get(wanted)
    if master_id and master_id in live_by_id:
        return master_id, live_by_id[master_id]["name"]

    # Exact normalized name against the actual HTML dropdown.
    for stop in live_stops:
        if _norm(stop.get("name")) == wanted:
            return str(stop["id"]), stop["name"]

    # Only accept a very strong fuzzy match. This prevents wrong stations.
    best = None
    best_score = 0.0
    for stop in live_stops:
        candidate = _norm(stop.get("name"))
        if not candidate:
            continue
        score = SequenceMatcher(None, wanted, candidate).ratio()
        if score > best_score:
            best_score, best = score, stop
    if best is not None and best_score >= 0.92:
        return str(best["id"]), best["name"]

    raise ValueError(f"Could not map official stop {official_id} ({official_name}) to the live Sitilink stop list ({len(live_stops)} stops)")


def parse_eta_table(soup):
    table = soup.find("tbody", id=ETA_TABLE_ID)
    if not table:
        table = soup.find("table", id=ETA_TABLE_ID)
        if table:
            table = table.find("tbody")
    if not table:
        return []
    buses = []
    for row in table.find_all("tr"):
        cols = [c.get_text(" ", strip=True) for c in row.find_all("td")]
        if len(cols) >= 4 and cols[1]:
            buses.append({"bus": cols[1], "destination": cols[2], "eta": cols[3]})
    return buses


def scrape_stop(official_stop_id):
    session = make_session()
    initial = request_get(session)
    soup = BeautifulSoup(initial.text, "html.parser")
    live_stops = get_stops(soup)
    print(f"[DEBUG] discovered {len(live_stops)} live stops")

    live_stop_id, live_stop_name = resolve_live_stop_id(official_stop_id, live_stops)
    print(f"[DEBUG] official stop {official_stop_id} -> live stop {live_stop_id} ({live_stop_name})")

    response = request_post(session, soup, STOP_SELECT, {STOP_SELECT: live_stop_id})
    stop_soup = BeautifulSoup(response.text, "html.parser")

    response = request_post(session, stop_soup, SERVICE_SELECT, {
        STOP_SELECT: live_stop_id,
        SERVICE_SELECT: BRT_SERVICE_TYPE,
    })
    brt_soup = BeautifulSoup(response.text, "html.parser")
    routes = get_routes(brt_soup)
    if not routes:
        raise RuntimeError("No BRT routes found after selecting the stop")

    result_routes = []
    for route in routes:
        response = request_post(session, brt_soup, ROUTE_SELECT, {
            STOP_SELECT: live_stop_id,
            SERVICE_SELECT: BRT_SERVICE_TYPE,
            ROUTE_SELECT: route["id"],
        })
        page = BeautifulSoup(response.text, "html.parser")
        result_routes.append({
            "route_id": route["id"],
            "route_name": route["name"],
            "buses": parse_eta_table(page),
        })

    return {
        "stop_id": str(official_stop_id),
        "live_stop_id": str(live_stop_id),
        "stop_name": _stop_name(official_stop_id) or live_stop_name,
        "service_type": "BRT",
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "routes": result_routes,
    }


def get_cached_stop(stop_id):
    key = str(stop_id)
    now = time.time()
    with cache_lock:
        item = cache.get(key)
        if item and now - item["cached_at"] < CACHE_SECONDS:
            result = dict(item["data"])
            result["cache"] = {"hit": True, "age_seconds": round(now - item["cached_at"], 2), "ttl_seconds": CACHE_SECONDS}
            return result
    data = scrape_stop(key)
    with cache_lock:
        cache[key] = {"cached_at": time.time(), "data": data}
    result = dict(data)
    result["cache"] = {"hit": False, "age_seconds": 0, "ttl_seconds": CACHE_SECONDS}
    return result


def get_local_route_stops(route_id):
    route = TRANSIT["routes"].get(str(route_id))
    if not route:
        return []
    result = []
    for stop in route.get("stops", []):
        try:
            lat = float(stop["stopLatitude"])
            lng = float(stop["stopLongitude"])
        except (KeyError, TypeError, ValueError):
            continue
        result.append({
            "sequence": stop.get("sequenceNumber"),
            "id": str(stop.get("stopCode", "")),
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
    return jsonify({"status": "ok", "transit_data": {"stops": len(TRANSIT["stops"]), "routes": len(TRANSIT["routes"])}, "live_eta": "available"})


@app.get("/api/debug/data")
def api_debug_data():
    return jsonify({"transit_file": str(TRANSIT_FILE), "transit_file_exists": TRANSIT_FILE.exists(), "stops": len(TRANSIT["stops"]), "routes": len(TRANSIT["routes"]), "master_stops": len(MASTER_STOPS), "cwd": str(Path.cwd()), "app_file": str(Path(__file__).resolve())})


@app.get("/api/stops")
def api_stops():
    return jsonify({"stops": [{"id": str(i), **s} for i, s in TRANSIT["stops"].items()], "count": len(TRANSIT["stops"]), "source": "transit_data.json"})


@app.get("/api/routes")
def api_routes():
    return jsonify({"routes": [{"id": str(i), "name": r.get("name", ""), "stop_count": len(r.get("stops", []))} for i, r in TRANSIT["routes"].items()], "count": len(TRANSIT["routes"])})


@app.get("/api/routes/<route_id>/stops")
def api_route_stops(route_id):
    route = TRANSIT["routes"].get(str(route_id))
    stops = get_local_route_stops(route_id)
    if not route or not stops:
        return jsonify({"error": "Route not found or has no valid coordinates", "route_id": route_id}), 404
    return jsonify({"route_id": route_id, "name": route.get("name", ""), "stops": stops})


@app.get("/api/eta/<stop_id>")
def api_eta(stop_id):
    try:
        return jsonify(get_cached_stop(stop_id))
    except Exception as exc:
        print("[ETA ERROR]", repr(exc))
        return jsonify({"error": "Failed to fetch ETA", "stop_id": str(stop_id), "details": str(exc)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
