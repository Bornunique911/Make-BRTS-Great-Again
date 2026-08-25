import time
import threading
import json
from pathlib import Path
import requests
import urllib3

from bs4 import BeautifulSoup
from flask import Flask, jsonify, render_template_string

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ============================================================
# CONFIG
# ============================================================

URL = "https://www.suratsitilink.org/LiveBusInfo.aspx"

CACHE_SECONDS = 15

# IMPORTANT:
# The current live page exposes BRT as value 1.
BRT_SERVICE_TYPE = "1"

STOP_SELECT = "ctl00$ContentPlaceHolder1$ddlstops"
SERVICE_SELECT = "ctl00$ContentPlaceHolder1$ddlservicetype"
ROUTE_SELECT = "ctl00$ContentPlaceHolder1$ddlroute"

ETA_TABLE_ID = "ContentPlaceHolder1_ETATableDetail"

app = Flask(__name__)

cache = {}
cache_lock = threading.Lock()

TRANSIT_FILE = Path(__file__).with_name("transit_data.json")


def load_transit():
    if not TRANSIT_FILE.exists():
        return {"stops": {}, "routes": {}}

    with TRANSIT_FILE.open(encoding="utf-8") as stream:
        data = json.load(stream)

    return {
        "stops": data.get("stops", {}),
        "routes": data.get("routes", {}),
    }


TRANSIT = load_transit()


# ============================================================
# SESSION
# ============================================================

def make_session():

    session = requests.Session()

    session.headers.update({
        "User-Agent":
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/139.0 Safari/537.36",

        "Accept":
            "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",

        "Accept-Language":
            "en-US,en;q=0.9",

        "Referer":
            URL
    })

    return session


# ============================================================
# ASP.NET HELPERS
# ============================================================

def get_form_data(soup):

    """
    Extract ALL ASP.NET form fields.

    This is important.

    We don't only send __VIEWSTATE.
    We preserve the entire evolving WebForms state.
    """

    data = {}

    form = soup.find("form")

    if not form:
        return data

    for element in form.find_all(["input", "select", "textarea"]):

        name = element.get("name")

        if not name:
            continue

        tag = element.name

        if tag == "input":

            input_type = element.get(
                "type",
                "text"
            ).lower()

            if input_type in [
                "submit",
                "button",
                "image",
                "file"
            ]:
                continue

            if input_type in [
                "checkbox",
                "radio"
            ] and not element.has_attr("checked"):
                continue

            data[name] = element.get(
                "value",
                ""
            )

        elif tag == "select":

            selected = element.find(
                "option",
                selected=True
            )

            if selected:

                data[name] = selected.get(
                    "value",
                    ""
                )

        else:

            data[name] = element.get_text(
                strip=True
            )

    return data


def request_get(session):

    response = session.get(
        URL,
        timeout=20,
        verify=False
    )

    response.raise_for_status()

    return response


def request_post(
    session,
    soup,
    event_target,
    extra_values
):

    payload = get_form_data(soup)

    payload["__EVENTTARGET"] = event_target
    payload["__EVENTARGUMENT"] = ""

    # Override the controls involved in this postback.
    for key, value in extra_values.items():
        payload[key] = value

    response = session.post(
        URL,
        data=payload,
        timeout=20,
        verify=False
    )

    response.raise_for_status()

    return response


# ============================================================
# DROPDOWN PARSERS
# ============================================================

def find_select(soup, name):

    return soup.find(
        "select",
        attrs={"name": name}
    )


def get_stops(soup):

    select = find_select(
        soup,
        STOP_SELECT
    )

    if not select:
        return []

    result = []

    for option in select.find_all("option"):

        value = option.get("value")

        text = option.get_text(
            " ",
            strip=True
        )

        if not value:
            continue

        if value in [
            "",
            "0",
            "-1"
        ]:
            continue

        result.append({
            "id": value,
            "name": text
        })

    return result


def get_routes(soup):

    select = find_select(
        soup,
        ROUTE_SELECT
    )

    if not select:
        return []

    result = []

    for option in select.find_all("option"):

        value = option.get("value")

        text = option.get_text(
            " ",
            strip=True
        )

        if not value:
            continue

        if value in [
            "",
            "0",
            "-1"
        ]:
            continue

        # Ignore placeholder.
        if "select" in text.lower():
            continue

        result.append({
            "id": value,
            "name": text
        })

    return result


# ============================================================
# ETA TABLE
# ============================================================

def parse_eta_table(soup):

    table = soup.find(
        "tbody",
        id=ETA_TABLE_ID
    )

    if not table:

        # Some versions of the page may put the ID
        # on the table itself.

        table = soup.find(
            "table",
            id=ETA_TABLE_ID
        )

        if table:
            table = table.find("tbody")

    buses = []

    if not table:
        return buses

    for row in table.find_all("tr"):

        cols = [
            c.get_text(
                " ",
                strip=True
            )
            for c in row.find_all("td")
        ]

        if len(cols) < 4:
            continue

        bus = cols[1]
        destination = cols[2]
        eta = cols[3]

        if not bus:
            continue

        buses.append({
            "bus": bus,
            "destination": destination,
            "eta": eta
        })

    return buses


# ============================================================
# MAIN SCRAPER
# ============================================================

def scrape_stop(stop_id):

    session = make_session()

    # --------------------------------------------------------
    # STEP 0
    # Initial GET
    # --------------------------------------------------------

    initial = request_get(session)

    soup = BeautifulSoup(
        initial.text,
        "html.parser"
    )

    stops = get_stops(soup)

    print(
        f"[DEBUG] discovered {len(stops)} stops"
    )

    stop_map = {
        x["id"]: x
        for x in stops
    }

    if stop_id not in stop_map:

        raise ValueError(
            f"Stop {stop_id} not found. "
            f"Discovered {len(stops)} stops."
        )

    stop_name = stop_map[
        stop_id
    ]["name"]

    print(
        f"[DEBUG] Stop: {stop_id} = {stop_name}"
    )

    # --------------------------------------------------------
    # STEP 1
    # Select stop
    # --------------------------------------------------------

    response = request_post(
        session,
        soup,
        STOP_SELECT,
        {
            STOP_SELECT: stop_id
        }
    )

    stop_soup = BeautifulSoup(
        response.text,
        "html.parser"
    )

    print(
        "[DEBUG] Stop postback complete"
    )

    # --------------------------------------------------------
    # STEP 2
    # Select BRT service
    # --------------------------------------------------------

    response = request_post(
        session,
        stop_soup,
        SERVICE_SELECT,
        {
            STOP_SELECT: stop_id,
            SERVICE_SELECT: BRT_SERVICE_TYPE
        }
    )

    brt_soup = BeautifulSoup(
        response.text,
        "html.parser"
    )

    print(
        "[DEBUG] BRT postback complete"
    )

    # --------------------------------------------------------
    # STEP 3
    # Discover routes
    # --------------------------------------------------------

    routes = get_routes(
        brt_soup
    )

    print(
        f"[DEBUG] routes found: {len(routes)}"
    )

    if not routes:

        raise RuntimeError(
            "No routes found after BRT selection."
        )

    result_routes = []

    # --------------------------------------------------------
    # STEP 4
    # Select every route
    # --------------------------------------------------------

    for route in routes:

        print(
            f"[DEBUG] route {route['id']} "
            f"{route['name']}"
        )

        response = request_post(
            session,
            brt_soup,
            ROUTE_SELECT,
            {
                STOP_SELECT: stop_id,
                SERVICE_SELECT: BRT_SERVICE_TYPE,
                ROUTE_SELECT: route["id"]
            }
        )

        page = BeautifulSoup(
            response.text,
            "html.parser"
        )

        buses = parse_eta_table(
            page
        )

        if buses:

            print(
                f"       LIVE BUSES: {len(buses)}"
            )

            for bus in buses:

                print(
                    f"       BUS {bus['bus']} "
                    f"-> {bus['destination']} "
                    f"ETA {bus['eta']}"
                )

        result_routes.append({
            "route_id": route["id"],
            "route_name": route["name"],
            "buses": buses
        })

    return {
        "stop_id": stop_id,
        "stop_name": stop_name,
        "service_type": "BRT",
        "updated_at": time.strftime(
            "%Y-%m-%dT%H:%M:%SZ",
            time.gmtime()
        ),
        "routes": result_routes
    }


# ============================================================
# CACHE
# ============================================================

def get_cached_stop(stop_id):

    now = time.time()

    with cache_lock:

        item = cache.get(
            stop_id
        )

        if item:

            age = now - item["cached_at"]

            if age < CACHE_SECONDS:

                result = dict(
                    item["data"]
                )

                result["cache"] = {
                    "hit": True,
                    "age_seconds": round(
                        age,
                        2
                    ),
                    "ttl_seconds":
                        CACHE_SECONDS
                }

                return result

    # --------------------------------------------------------
    # Cache miss
    # --------------------------------------------------------

    data = scrape_stop(
        stop_id
    )

    with cache_lock:

        cache[stop_id] = {
            "cached_at": time.time(),
            "data": data
        }

    result = dict(data)

    result["cache"] = {
        "hit": False,
        "age_seconds": 0,
        "ttl_seconds": CACHE_SECONDS
    }

    return result


# ============================================================
# API
# ============================================================

@app.get("/")
def home():
        return render_template_string(r"""
<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Surat BRTS | Live map</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=Space+Grotesk:wght@500;600;700&display=swap" rel="stylesheet">
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
    <style>
        :root { --ink: #17212b; --muted: #6b7785; --paper: #f5f7f8; --line: #dce3e8; --red: #e4573d; --teal: #087f8c; --yellow: #f6c85f; }
        * { box-sizing: border-box; }
        html, body { height: 100%; margin: 0; }
        body { color: var(--ink); background: var(--paper); font: 14px "DM Sans", sans-serif; overflow: hidden; }
        .app { display: grid; grid-template-columns: 350px 1fr; height: 100%; }
        .sidebar { z-index: 1000; display: flex; flex-direction: column; gap: 24px; padding: 28px 24px; background: #fff; border-right: 1px solid var(--line); overflow-y: auto; }
        .brand { display: flex; align-items: center; gap: 12px; }
        .brand-mark { width: 42px; height: 42px; display: grid; place-items: center; color: #fff; background: var(--red); border-radius: 12px 12px 12px 3px; font: 700 20px "Space Grotesk", sans-serif; }
        h1, h2, p { margin: 0; } h1 { font: 700 21px "Space Grotesk", sans-serif; letter-spacing: 0; } h2 { font: 600 17px "Space Grotesk", sans-serif; }
        .eyebrow { margin-top: 3px; color: var(--muted); font-size: 11px; font-weight: 700; letter-spacing: 1.1px; text-transform: uppercase; }
        .intro { padding: 18px; background: #eef6f5; border-left: 4px solid var(--teal); }
        .intro strong { display: block; margin-bottom: 6px; font: 600 15px "Space Grotesk", sans-serif; } .intro span { color: #53636b; line-height: 1.5; }
        label { display: block; margin-bottom: 8px; color: var(--muted); font-size: 12px; font-weight: 700; letter-spacing: .4px; text-transform: uppercase; }
        select, input { width: 100%; height: 44px; padding: 0 12px; color: var(--ink); background: #fff; border: 1px solid var(--line); border-radius: 7px; font: inherit; outline: none; }
        select:focus, input:focus { border-color: var(--teal); box-shadow: 0 0 0 3px #087f8c1c; }
        .search { position: relative; } .search input { padding-left: 38px; } .search:before { content: "⌕"; position: absolute; z-index: 1; top: 8px; left: 13px; color: var(--muted); font-size: 23px; }
        .stats { display: grid; grid-template-columns: 1fr 1fr; gap: 9px; } .stat { padding: 13px; background: var(--paper); border: 1px solid var(--line); border-radius: 7px; } .stat b { display: block; font: 700 20px "Space Grotesk", sans-serif; } .stat span { color: var(--muted); font-size: 11px; }
        .eta { margin-top: auto; padding-top: 20px; border-top: 1px solid var(--line); } .eta-title { display: flex; align-items: center; justify-content: space-between; gap: 12px; } .eta-name { margin-top: 5px; color: var(--muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .eta-list { display: grid; gap: 8px; margin-top: 14px; max-height: 170px; overflow-y: auto; } .eta-row { display: grid; grid-template-columns: 45px 1fr auto; gap: 8px; align-items: center; padding: 10px; background: #fff8e7; border: 1px solid #f2dfab; border-radius: 6px; } .eta-row b { font-size: 12px; } .eta-row small { color: var(--muted); } .eta-time { color: #ae6a12; font-weight: 700; }
        .map-wrap { position: relative; min-width: 0; } #map { width: 100%; height: 100%; background: #dfe8e6; }
        .map-top { position: absolute; z-index: 900; top: 22px; left: 22px; right: 22px; display: flex; align-items: center; justify-content: space-between; pointer-events: none; } .map-top > * { pointer-events: auto; }
        .map-pill { padding: 10px 14px; background: #ffffffed; border: 1px solid #fff; border-radius: 7px; box-shadow: 0 4px 18px #18283218; backdrop-filter: blur(8px); } .map-pill b { font: 600 14px "Space Grotesk", sans-serif; } .map-pill span { margin-left: 8px; color: var(--muted); font-size: 12px; }
        .locate { width: 40px; height: 40px; color: var(--ink); background: #fff; border: 0; border-radius: 7px; box-shadow: 0 4px 18px #18283218; font-size: 20px; cursor: pointer; } .locate:hover { color: var(--teal); }
        .leaflet-popup-content-wrapper { border-radius: 7px; } .popup-name { font-weight: 700; } .popup-id { color: var(--muted); font-size: 11px; }
        .stop-dot { width: 12px; height: 12px; background: var(--yellow); border: 3px solid #fff; border-radius: 50%; box-shadow: 0 1px 5px #18283255; } .stop-dot.route { background: var(--red); }
        .empty { color: var(--muted); font-size: 13px; line-height: 1.5; }
        @media (max-width: 760px) { body { overflow: auto; } .app { display: block; height: auto; } .sidebar { min-height: 420px; padding: 20px; } .map-wrap { height: 58vh; min-height: 420px; } .eta { margin-top: 0; } .map-top { top: 14px; left: 14px; right: 14px; } }
    </style>
</head>
<body>
    <main class="app">
        <aside class="sidebar">
            <header class="brand"><div class="brand-mark">S</div><div><h1>Surat BRTS</h1><p class="eyebrow">Live network map</p></div></header>
            <div class="intro"><strong>Move through the city.</strong><span>Choose a route or search for a stop to see the network and live arrivals.</span></div>
            <section><label for="route">Route</label><select id="route"><option value="">All routes</option></select></section>
            <section class="search"><label for="stop-search">Find a stop</label><input id="stop-search" type="search" placeholder="Search by stop name" autocomplete="off"></section>
            <div class="stats"><div class="stat"><b id="route-count">--</b><span>routes mapped</span></div><div class="stat"><b id="stop-count">--</b><span>stops mapped</span></div></div>
            <section class="eta"><div class="eta-title"><h2>Next arrivals</h2><span id="eta-status" class="eyebrow">Select a stop</span></div><p id="eta-name" class="eta-name">Click any stop on the map</p><div id="eta-list" class="eta-list"><p class="empty">Live timings appear here when you choose a stop.</p></div></section>
        </aside>
        <section class="map-wrap"><div id="map"></div><div class="map-top"><div class="map-pill"><b id="map-label">Surat transit</b><span id="map-subtitle">OpenStreetMap</span></div><button class="locate" id="locate" title="Use my location" aria-label="Use my location">◎</button></div></section>
    </main>
    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
    <script>
        const surat = [21.1702, 72.8311];
        const map = L.map('map', { zoomControl: false }).setView(surat, 12);
        L.control.zoom({ position: 'bottomright' }).addTo(map);
        L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19, attribution: '&copy; OpenStreetMap contributors' }).addTo(map);
        const routeSelect = document.getElementById('route');
        const search = document.getElementById('stop-search');
        const stopLayer = L.layerGroup().addTo(map);
        let allStops = [], routeStops = [], routeLine = null, selectedStop = null;
        const stopIcon = (route) => L.divIcon({ className: '', html: `<div class="stop-dot ${route ? 'route' : ''}"></div>`, iconSize: [12, 12], iconAnchor: [6, 6] });
        const cleanRouteName = (name) => name.replace(/^\\([^)]*\\)\s*/, '');
        const drawStops = (stops, routeMode = false) => { stopLayer.clearLayers(); stops.filter(s => s.lat != null && s.lng != null).forEach(stop => { const marker = L.marker([stop.lat, stop.lng], { icon: stopIcon(routeMode) }).addTo(stopLayer); marker.bindPopup(`<div class="popup-name">${stop.name}</div><div class="popup-id">Stop ${stop.id}</div>`); marker.on('click', () => loadEta(stop)); }); };
        const loadEta = async (stop) => { selectedStop = stop; document.getElementById('eta-name').textContent = stop.name; document.getElementById('eta-status').textContent = 'Loading'; document.getElementById('eta-list').innerHTML = '<p class="empty">Contacting SITILINK...</p>'; try { const response = await fetch(`/api/eta/${encodeURIComponent(stop.id)}`); const data = await response.json(); if (!response.ok) throw new Error(data.details || data.error); const buses = data.routes.flatMap(route => route.buses.map(bus => ({ ...bus, route: route.route_name }))); document.getElementById('eta-status').textContent = `${buses.length} bus${buses.length === 1 ? '' : 'es'}`; document.getElementById('eta-list').innerHTML = buses.length ? buses.map(bus => `<div class="eta-row"><b>${bus.bus}</b><small>${bus.destination || bus.route}</small><span class="eta-time">${bus.eta}</span></div>`).join('') : '<p class="empty">No live arrivals reported for this stop.</p>'; } catch (error) { document.getElementById('eta-status').textContent = 'Unavailable'; document.getElementById('eta-list').innerHTML = `<p class="empty">${error.message || 'Live timings are unavailable right now.'}</p>`; } };
        const showRoute = async (routeId) => { if (routeLine) { map.removeLayer(routeLine); routeLine = null; } if (!routeId) { routeStops = []; drawStops(allStops); map.setView(surat, 12); document.getElementById('map-label').textContent = 'Surat transit'; document.getElementById('map-subtitle').textContent = 'OpenStreetMap'; return; } const response = await fetch(`/api/routes/${encodeURIComponent(routeId)}/stops`); const data = await response.json(); routeStops = data.stops || []; const points = routeStops.map(s => [s.lat, s.lng]); routeLine = L.polyline(points, { color: '#e4573d', weight: 5, opacity: .85 }).addTo(map); drawStops(routeStops, true); if (points.length) map.fitBounds(routeLine.getBounds(), { padding: [45, 45] }); document.getElementById('map-label').textContent = `Route ${routeId}`; document.getElementById('map-subtitle').textContent = cleanRouteName(data.name || ''); };
        const loadNetwork = async () => { const [stopsResponse, routesResponse] = await Promise.all([fetch('/api/stops'), fetch('/api/routes')]); const stopsData = await stopsResponse.json(); const routesData = await routesResponse.json(); allStops = stopsData.stops.filter(s => s.lat != null && s.lng != null); document.getElementById('stop-count').textContent = allStops.length.toLocaleString(); document.getElementById('route-count').textContent = routesData.count.toLocaleString(); routesData.routes.forEach(route => { const option = document.createElement('option'); option.value = route.id; option.textContent = `${route.id} · ${cleanRouteName(route.name)}`; routeSelect.appendChild(option); }); drawStops(allStops); };
        routeSelect.addEventListener('change', () => showRoute(routeSelect.value));
        search.addEventListener('input', () => { const term = search.value.trim().toLowerCase(); const source = routeStops.length ? routeStops : allStops; const matches = term ? source.filter(s => s.name.toLowerCase().includes(term)) : source; drawStops(matches, routeStops.length > 0); if (matches.length === 1) { map.setView([matches[0].lat, matches[0].lng], 15); loadEta(matches[0]); } });
        document.getElementById('locate').addEventListener('click', () => { if (!navigator.geolocation) return; navigator.geolocation.getCurrentPosition(position => map.setView([position.coords.latitude, position.coords.longitude], 15)); });
        loadNetwork().catch(error => { document.getElementById('eta-list').innerHTML = `<p class="empty">Could not load transit data: ${error.message}</p>`; });
    </script>
</body>
</html>
""")


def get_local_route_stops(route_id):
    route = TRANSIT["routes"].get(str(route_id))
    if not route:
        return []

    stops = []
    for stop in route.get("stops", []):
        try:
            latitude = float(stop["stopLatitude"])
            longitude = float(stop["stopLongitude"])
        except (KeyError, TypeError, ValueError):
            continue

        stops.append({
            "sequence": stop.get("sequenceNumber"),
            "id": str(stop.get("stopCode", "")),
            "name": stop.get("stopName", ""),
            "lat": latitude,
            "lng": longitude,
        })

    return sorted(
        stops,
        key=lambda stop: stop["sequence"]
        if stop["sequence"] is not None else 999999,
    )


@app.get("/api/stops")
def api_stops():
    stops = [
        {"id": str(stop_id), **stop}
        for stop_id, stop in TRANSIT["stops"].items()
    ]

    if stops:
        return jsonify({
            "stops": stops,
            "count": len(stops),
            "source": "transit_data.json",
        })

    session = make_session()
    soup = BeautifulSoup(request_get(session).text, "html.parser")
    stops = get_stops(soup)
    return jsonify({
        "stops": stops,
        "count": len(stops),
        "source": "SITILINK live page",
    })


@app.get("/api/routes")
def api_routes():
    routes = [
        {
            "id": str(route_id),
            "name": route.get("name", ""),
            "stop_count": len(route.get("stops", [])),
        }
        for route_id, route in TRANSIT["routes"].items()
    ]
    return jsonify({"routes": routes, "count": len(routes)})


@app.get("/api/routes/<route_id>/stops")
def api_route_stops(route_id):
    stops = get_local_route_stops(route_id)
    route = TRANSIT["routes"].get(str(route_id))
    if not route or not stops:
        return jsonify({
            "error": "Route not found or has no valid coordinates",
            "route_id": route_id,
        }), 404

    return jsonify({
        "route_id": route_id,
        "name": route.get("name", ""),
        "stops": stops,
    })


@app.get("/api/eta/<stop_id>")
def api_eta(stop_id):

    try:

        data = get_cached_stop(
            stop_id
        )

        return jsonify(
            data
        )

    except Exception as exc:

        print(
            "[ERROR]",
            repr(exc)
        )

        return jsonify({
            "error":
                "Failed to fetch ETA",

            "stop_id":
                stop_id,

            "details":
                str(exc)
        }), 500


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    print("=" * 60)
    print("SURAT REALTIME API")
    print("=" * 60)
    print()
    print(
        "API: http://127.0.0.1:5000"
    )
    print(
        "Example: "
        "http://127.0.0.1:5000/api/eta/2523"
    )
    print(
        f"Cache: {CACHE_SECONDS} seconds"
    )
    print()
    print(
        "SSL verification is disabled because"
    )
    print(
        "the SITILINK HTTPS certificate is expired."
    )
    print("=" * 60)

    app.run(
        host="0.0.0.0",
        port=5000,
        debug=False
    )