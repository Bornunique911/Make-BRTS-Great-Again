#!/usr/bin/env python3
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
from flask_cors import CORS

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
CORS(app, resources={r"/api/*": {"origins": "*"}})

cache = {}
cache_lock = threading.Lock()
APP_DIR = Path(__file__).resolve().parent
REPO_DIR = APP_DIR.parent.parent

TRANSIT_CANDIDATES = [
    APP_DIR / "static" / "transit_data.json",
    APP_DIR / "static" / "assets" / "transit_data.json",
    REPO_DIR / "transit_data.json",
]


def _find_transit_file():
    for path in TRANSIT_CANDIDATES:
        if path.is_file():
            return path.resolve()
    raise FileNotFoundError("transit_data.json not found")


TRANSIT_FILE = _find_transit_file()
with TRANSIT_FILE.open(encoding="utf-8") as f:
    TRANSIT = json.load(f)

if not isinstance(TRANSIT.get("stops"), dict) or not isinstance(TRANSIT.get("routes"), dict):
    raise ValueError(f"Invalid transit dataset: {TRANSIT_FILE}")

print(
    f"[DATA] Loaded {len(TRANSIT['stops'])} stops and "
    f"{len(TRANSIT['routes'])} routes from {TRANSIT_FILE}"
)


def _norm(value):
    value = str(value or "").upper().replace("&", " AND ")
    value = re.sub(r"\bBRTS\b", "", value)
    value = re.sub(r"[^A-Z0-9]+", " ", value)
    return " ".join(value.split())


def _compact(value):
    return _norm(value).replace(" ", "")


def _tokens(value):
    return set(_norm(value).split())


def _stop_name(stop_id):
    stop = TRANSIT["stops"].get(str(stop_id), {})
    if isinstance(stop, dict):
        return stop.get("name") or stop.get("stopName") or ""
    return ""


def _load_master_stops():
    for path in (REPO_DIR / "stops_master.json", APP_DIR / "stops_master.json"):
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            rows = data.get("d", []) if isinstance(data, dict) else []
            if isinstance(rows, list):
                return rows
        except Exception as exc:
            print("[STOP MAP] master list load failed:", repr(exc))
    return []


MASTER_STOPS = _load_master_stops()
MASTER_BY_NAME = {}
MASTER_BY_COMPACT = {}
for row in MASTER_STOPS:
    sid = row.get("stopid")
    name = row.get("stopname")
    if sid is None or not name:
        continue
    MASTER_BY_NAME.setdefault(_norm(name), []).append(str(sid))
    MASTER_BY_COMPACT.setdefault(_compact(name), []).append(str(sid))

print(f"[STOP MAP] loaded {len(MASTER_STOPS)} master stops")


def make_session():
    s = requests.Session()
    s.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "Chrome/139.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": URL,
        "Connection": "keep-alive",
    })
    return s


def get_form_data(soup):
    form = soup.find("form")
    if not form:
        raise RuntimeError("ASP.NET form not found")

    data = {}
    for element in form.find_all(["input", "select", "textarea"]):
        name = element.get("name")
        if not name:
            continue

        if element.name == "input":
            typ = element.get("type", "text").lower()
            if typ in {"submit", "button", "image", "file", "reset"}:
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
    response = session.get(
        URL, timeout=REQUEST_TIMEOUT, verify=False, headers=session.headers
    )
    response.raise_for_status()
    return response


def request_post(session, soup, event_target, values):
    payload = get_form_data(soup)
    payload["__EVENTTARGET"] = event_target
    payload["__EVENTARGUMENT"] = ""
    payload.update(values)
    response = session.post(
        URL,
        data=payload,
        timeout=REQUEST_TIMEOUT,
        verify=False,
        headers=session.headers,
    )
    response.raise_for_status()
    return response


def find_select(soup, name):
    return soup.find("select", attrs={"name": name})


def get_stops(soup):
    select = find_select(soup, STOP_SELECT)
    if not select:
        return []
    return [
        {"id": option.get("value"), "name": option.get_text(" ", strip=True)}
        for option in select.find_all("option")
        if option.get("value") not in {None, "", "0", "-1"}
    ]


def get_routes(soup):
    select = find_select(soup, ROUTE_SELECT)
    if not select:
        return []
    return [
        {"id": option.get("value"), "name": option.get_text(" ", strip=True)}
        for option in select.find_all("option")
        if option.get("value") not in {None, "", "0", "-1"}
        and "select" not in option.get_text(" ", strip=True).lower()
    ]


def resolve_live_stop_id(official_id, live_stops):
    """Resolve a map stop to a current LiveBusInfo stop without guessing."""
    official_id = str(official_id)
    live_by_id = {str(x["id"]): x for x in live_stops}

    if official_id in live_by_id:
        return official_id, live_by_id[official_id]["name"]

    official_name = _stop_name(official_id)
    wanted = _norm(official_name)
    compact = _compact(official_name)

    if not wanted:
        # Route-only IDs may still exist in route data.
        for route in TRANSIT["routes"].values():
            for route_stop in route.get("stops", []):
                source_id = str(route_stop.get("sourceStopCode", route_stop.get("stopCode", "")))
                if source_id == official_id:
                    official_name = route_stop.get("stopName", "")
                    wanted = _norm(official_name)
                    compact = _compact(official_name)
                    break
            if wanted:
                break

    if not wanted:
        raise ValueError(f"Official stop {official_id} has no known name")

    # 1. Exact normalized name. Only accept a unique match.
    exact = [s for s in live_stops if _norm(s.get("name")) == wanted]
    if len(exact) == 1:
        return str(exact[0]["id"]), exact[0]["name"]

    # 2. Exact compact name handles spacing/punctuation differences.
    compact_matches = [s for s in live_stops if _compact(s.get("name")) == compact]
    if len(compact_matches) == 1:
        return str(compact_matches[0]["id"]), compact_matches[0]["name"]

    # 3. If the old master list knows an exact name -> live ID, use it only
    # when that ID is actually present in the current live dropdown.
    for candidate_id in MASTER_BY_NAME.get(wanted, []) + MASTER_BY_COMPACT.get(compact, []):
        if candidate_id in live_by_id:
            return candidate_id, live_by_id[candidate_id]["name"]

    # 4. Token overlap for harmless abbreviations. Reject ambiguous results.
    wanted_tokens = _tokens(official_name)
    ranked = []
    for stop in live_stops:
        candidate_tokens = _tokens(stop.get("name"))
        if not candidate_tokens:
            continue
        intersection = len(wanted_tokens & candidate_tokens)
        union = len(wanted_tokens | candidate_tokens)
        jaccard = intersection / union if union else 0.0
        sequence = SequenceMatcher(None, wanted, _norm(stop.get("name"))).ratio()
        if intersection >= 2 and jaccard >= 0.75:
            ranked.append((jaccard, sequence, stop))

    ranked.sort(key=lambda x: (x[0], x[1]), reverse=True)
    if ranked:
        best = ranked[0]
        tied = [x for x in ranked if abs(x[0] - best[0]) < 0.02 and abs(x[1] - best[1]) < 0.02]
        if len(tied) == 1 and best[0] >= 0.75:
            return str(best[2]["id"]), best[2]["name"]

    raise ValueError(
        f"Could not map official stop {official_id} ({official_name}) "
        f"to the live Sitilink stop list ({len(live_stops)} stops)"
    )


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


def select_stop_and_brt(session, initial_soup, live_stop_id):
    response = request_post(session, initial_soup, STOP_SELECT, {STOP_SELECT: live_stop_id})
    stop_soup = BeautifulSoup(response.text, "html.parser")
    response = request_post(
        session,
        stop_soup,
        SERVICE_SELECT,
        {STOP_SELECT: live_stop_id, SERVICE_SELECT: BRT_SERVICE_TYPE},
    )
    return BeautifulSoup(response.text, "html.parser")


def route_postback(session, brt_soup, live_stop_id, route_id):
    response = request_post(
        session,
        brt_soup,
        ROUTE_SELECT,
        {
            STOP_SELECT: live_stop_id,
            SERVICE_SELECT: BRT_SERVICE_TYPE,
            ROUTE_SELECT: route_id,
        },
    )
    return BeautifulSoup(response.text, "html.parser")


def scrape_stop(official_stop_id):
    session = make_session()
    initial = request_get(session)
    initial_soup = BeautifulSoup(initial.text, "html.parser")
    live_stops = get_stops(initial_soup)
    print(f"[DEBUG] discovered {len(live_stops)} live stops")

    live_stop_id, live_stop_name = resolve_live_stop_id(official_stop_id, live_stops)
    print(f"[DEBUG] {official_stop_id} -> {live_stop_id} ({live_stop_name})")

    brt_soup = select_stop_and_brt(session, initial_soup, live_stop_id)
    routes = get_routes(brt_soup)
    if not routes:
        raise RuntimeError("No BRT routes found after selecting the stop")

    result_routes = []
    for route in routes:
        route_id = str(route["id"])
        page = route_postback(session, brt_soup, live_stop_id, route_id)
        buses = parse_eta_table(page)

        selected = page.find("option", selected=True)
        selected_value = selected.get("value") if selected else None
        if selected_value != route_id and not buses:
            retry_brt = select_stop_and_brt(session, initial_soup, live_stop_id)
            page = route_postback(session, retry_brt, live_stop_id, route_id)
            buses = parse_eta_table(page)

        result_routes.append({
            "route_id": route_id,
            "route_name": route["name"],
            "buses": buses,
        })

    return {
        "stop_id": str(official_stop_id),
        "live_stop_id": str(live_stop_id),
        "stop_name": _stop_name(official_stop_id) or live_stop_name,
        "service_type": "BRT",
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "route_count": len(result_routes),
        "live_bus_count": sum(len(r["buses"]) for r in result_routes),
        "routes": result_routes,
    }


def get_cached_stop(stop_id):
    key = str(stop_id)
    now = time.time()
    with cache_lock:
        item = cache.get(key)
        if item and now - item["cached_at"] < CACHE_SECONDS:
            result = dict(item["data"])
            result["cache"] = {
                "hit": True,
                "age_seconds": round(now - item["cached_at"], 2),
                "ttl_seconds": CACHE_SECONDS,
            }
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
        # Prefer the resolved current live ID when the route dataset has one.
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
        "transit_data": {"stops": len(TRANSIT["stops"]), "routes": len(TRANSIT["routes"])},
        "live_eta": "available",
    })


@app.get("/api/debug/data")
def api_debug_data():
    return jsonify({
        "transit_file": str(TRANSIT_FILE),
        "transit_file_exists": TRANSIT_FILE.exists(),
        "stops": len(TRANSIT["stops"]),
        "routes": len(TRANSIT["routes"]),
        "master_stops": len(MASTER_STOPS),
        "cwd": str(Path.cwd()),
        "app_file": str(Path(__file__).resolve()),
    })


@app.get("/api/stops")
def api_stops():
    return jsonify({
        "stops": [{"id": str(i), **s} for i, s in TRANSIT["stops"].items()],
        "count": len(TRANSIT["stops"]),
        "source": "transit_data.json",
    })


@app.get("/api/routes")
def api_routes():
    return jsonify({
        "routes": [
            {"id": str(i), "name": r.get("name", ""), "stop_count": len(r.get("stops", []))}
            for i, r in TRANSIT["routes"].items()
        ],
        "count": len(TRANSIT["routes"]),
    })


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
    except ValueError as exc:
        return jsonify({"error": "Invalid stop", "stop_id": str(stop_id), "details": str(exc)}), 400
    except requests.RequestException as exc:
        print("[ETA NETWORK ERROR]", repr(exc))
        return jsonify({"error": "SITILINK request failed", "stop_id": str(stop_id), "details": str(exc)}), 502
    except Exception as exc:
        print("[ETA ERROR]", repr(exc))
        return jsonify({"error": "Failed to fetch ETA", "stop_id": str(stop_id), "details": str(exc)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
