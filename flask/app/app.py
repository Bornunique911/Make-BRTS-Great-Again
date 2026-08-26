import time
import threading
import json
from pathlib import Path
import requests
import urllib3

from bs4 import BeautifulSoup
from flask import Flask, jsonify, render_template

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

app = Flask(
    __name__,
    template_folder="templates",
    static_folder="static"
)

cache = {}
cache_lock = threading.Lock()

# ============================================================
# TRANSIT DATA
# ============================================================

APP_DIR = Path(__file__).resolve().parent

# Expected project layout:
# <repo>/transit_data.json
# <repo>/flask/app/app.py
#
# We also check a couple of fallback locations so the API does not silently
# load an empty dataset if the working tree is slightly different.
TRANSIT_CANDIDATES = [
    APP_DIR.parent / "app" / "static" / "transit_data.json",
]

def _find_transit_file():
    seen = set()

    for candidate in TRANSIT_CANDIDATES:
        candidate = candidate.resolve()

        if candidate in seen:
            continue

        seen.add(candidate)

        if candidate.is_file():
            return candidate

    checked = "\n".join(f"  - {p.resolve()}" for p in TRANSIT_CANDIDATES)

    raise FileNotFoundError(
        "transit_data.json not found. Checked:\n" + checked
    )


TRANSIT_FILE = _find_transit_file()


def load_transit():
    with TRANSIT_FILE.open(encoding="utf-8") as stream:
        data = json.load(stream)

    if not isinstance(data, dict):
        raise ValueError(
            f"{TRANSIT_FILE} must contain a JSON object."
        )

    # Primary expected structure:
    # {
    #   "stops": {...},
    #   "routes": {...}
    # }
    stops = data.get("stops", {})
    routes = data.get("routes", {})

    if not isinstance(stops, dict):
        raise ValueError(
            f"{TRANSIT_FILE}: 'stops' must be an object/dict."
        )

    if not isinstance(routes, dict):
        raise ValueError(
            f"{TRANSIT_FILE}: 'routes' must be an object/dict."
        )

    print(
        f"[DATA] Loaded {len(stops)} stops and {len(routes)} routes "
        f"from {TRANSIT_FILE}"
    )

    if not stops and not routes:
        raise ValueError(
            f"{TRANSIT_FILE} contains empty 'stops' and 'routes'."
        )

    return {
        "stops": stops,
        "routes": routes,
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
        return render_template("index.html")


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



@app.get("/api/debug/data")
def api_debug_data():
    return jsonify({
        "transit_file": str(TRANSIT_FILE),
        "transit_file_exists": TRANSIT_FILE.exists(),
        "stops": len(TRANSIT["stops"]),
        "routes": len(TRANSIT["routes"]),
        "cwd": str(Path.cwd()),
        "app_file": str(Path(__file__).resolve()),
    })


@app.get("/api/health")
def api_health():
    return jsonify({
        "status": "ok",
        "transit_data": {
            "stops": len(TRANSIT["stops"]),
            "routes": len(TRANSIT["routes"]),
        },
        "live_eta": "available",
    })


@app.get("/api/stops")
def api_stops():
    stops = [
        {"id": str(stop_id), **stop}
        for stop_id, stop in TRANSIT["stops"].items()
    ]

    return jsonify({
        "stops": stops,
        "count": len(stops),
        "source": "transit_data.json",
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
    print(
        f"Transit data: {len(TRANSIT['stops'])} stops, "
        f"{len(TRANSIT['routes'])} routes"
    )
    print(
        f"Transit file: {TRANSIT_FILE}"
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