import time
import threading
import requests
import urllib3

from bs4 import BeautifulSoup
from flask import Flask, jsonify

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ============================================================
# CONFIG
# ============================================================

URL = "https://www.suratsitilink.org/LiveBusInfo.aspx"

CACHE_SECONDS = 15

# IMPORTANT:
# This is the value that worked in your original scraper.
BRT_SERVICE_TYPE = "3"

STOP_SELECT = "ctl00$ContentPlaceHolder1$ddlstop"
SERVICE_SELECT = "ctl00$ContentPlaceHolder1$ddlservicetype"
ROUTE_SELECT = "ctl00$ContentPlaceHolder1$ddlroute"

ETA_TABLE_ID = "ContentPlaceHolder1_ETATableDetail"

app = Flask(__name__)

cache = {}
cache_lock = threading.Lock()


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

    return jsonify({
        "name":
            "Surat Realtime API",

        "status":
            "running",

        "example":
            "/api/eta/2523",

        "cache_seconds":
            CACHE_SECONDS
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