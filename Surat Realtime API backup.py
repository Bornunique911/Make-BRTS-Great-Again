#!/usr/bin/env python3
"""
Surat BRTS Live ETA API

What this does:
- Talks to the official SITILINK LiveBusInfo.aspx page.
- Correctly maintains ASP.NET WebForms state across:
    Stop -> Service -> Route
- Exposes clean JSON to your frontend.
- Caches each stop for 15 seconds so repeated users don't
  repeatedly hit the SITILINK server.

Install:
    python3 -m pip install requests beautifulsoup4 flask flask-cors

Run:
    python3 surat_realtime_api.py

Then open:
    http://127.0.0.1:5000/api/stops
    http://127.0.0.1:5000/api/eta/2523

IMPORTANT:
The SITILINK HTTPS certificate is currently problematic, so this
development script disables certificate verification. Do not copy
that setting blindly into a production system.
"""

import json
import threading
import time
from datetime import datetime, timezone

import requests
import urllib3
from bs4 import BeautifulSoup
from flask import Flask, jsonify
from flask_cors import CORS

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

URL = "https://www.suratsitilink.org/LiveBusInfo.aspx"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/139.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": URL,
}

STOP_SELECT = "ctl00$ContentPlaceHolder1$ddlstops"
SERVICE_SELECT = "ctl00$ContentPlaceHolder1$ddlservicetype"
ROUTE_SELECT = "ctl00$ContentPlaceHolder1$ddlroute"

# The current live page exposes BRT as value 1.
BRT_SERVICE_TYPE = "1"

CACHE_SECONDS = 15

app = Flask(__name__)
CORS(app)

cache = {}
cache_lock = threading.Lock()


def make_session():
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


def get_form_data(soup):
    """Extract the complete current ASP.NET WebForms form state."""
    form = soup.find("form")
    if not form:
        raise RuntimeError("ASP.NET form not found")

    data = {}

    for element in form.find_all("input"):
        name = element.get("name")
        if not name:
            continue

        typ = element.get("type", "text").lower()

        if typ in {"submit", "button", "image", "reset", "file"}:
            continue

        if typ in {"checkbox", "radio"}:
            if element.has_attr("checked"):
                data[name] = element.get("value", "on")
        else:
            data[name] = element.get("value", "")

    for select in form.find_all("select"):
        name = select.get("name")
        if not name:
            continue

        selected = select.find("option", selected=True)
        if selected:
            data[name] = selected.get("value", "")
        else:
            first = select.find("option")
            if first:
                data[name] = first.get("value", "")

    return data


def request_get(session):
    r = session.get(
        URL,
        verify=False,
        timeout=20,
        headers=HEADERS,
    )
    r.raise_for_status()
    return r


def request_post(session, soup, event_target, updates):
    payload = get_form_data(soup)
    payload["__EVENTTARGET"] = event_target
    payload["__EVENTARGUMENT"] = ""
    payload.update(updates)

    r = session.post(
        URL,
        data=payload,
        verify=False,
        timeout=20,
        headers=HEADERS,
    )
    r.raise_for_status()
    return r


def get_options(soup, select_id):
    select = soup.find("select", id=select_id)
    if not select:
        return []

    result = []

    for option in select.find_all("option"):
        value = option.get("value")
        text = option.get_text(" ", strip=True)

        if value is None:
            continue

        result.append({
            "id": value,
            "name": text,
        })

    return result


def get_stops(soup):
    options = get_options(
        soup,
        "ContentPlaceHolder1_ddlstops",
    )

    return [
        x for x in options
        if x["id"] not in {"", "0", "-1"}
        and x["name"].lower() not in {
            "please select stop",
            "select stop",
        }
    ]


def get_routes(soup):
    options = get_options(
        soup,
        "ContentPlaceHolder1_ddlroute",
    )

    return [
        x for x in options
        if x["id"] not in {"", "0", "-1"}
        and x["name"].lower() not in {
            "please select route",
            "select route",
        }
    ]


def scrape_stop(stop_id):
    """
    Reproduce the proven working sequence:
        GET -> stop -> BRT -> route(s)
    """
    session = make_session()

    initial = request_get(session)
    soup = BeautifulSoup(initial.text, "html.parser")

    stops = get_stops(soup)
    stop_map = {x["id"]: x for x in stops}

    if stop_id not in stop_map:
        raise ValueError(f"Unknown stop ID: {stop_id}")

    stop_name = stop_map[stop_id]["name"]

    # 1. Stop postback
    response = request_post(
        session,
        soup,
        STOP_SELECT,
        {
            STOP_SELECT: stop_id,
        },
    )
    stop_soup = BeautifulSoup(response.text, "html.parser")

    # 2. BRT service postback
    response = request_post(
        session,
        stop_soup,
        SERVICE_SELECT,
        {
            STOP_SELECT: stop_id,
            SERVICE_SELECT: BRT_SERVICE_TYPE,
        },
    )
    brt_soup = BeautifulSoup(response.text, "html.parser")

    routes = get_routes(brt_soup)
    result_routes = []

    # 3. Route postback for every route exposed for this stop
    for route in routes:
        response = request_post(
            session,
            brt_soup,
            ROUTE_SELECT,
            {
                STOP_SELECT: stop_id,
                SERVICE_SELECT: BRT_SERVICE_TYPE,
                ROUTE_SELECT: route["id"],
            },
        )

        page = BeautifulSoup(response.text, "html.parser")

        table = page.find(
            "tbody",
            id="ContentPlaceHolder1_ETATableDetail",
        )

        buses = []

        if table:
            for row in table.find_all("tr"):
                cols = [
                    c.get_text(" ", strip=True)
                    for c in row.find_all("td")
                ]

                if len(cols) >= 4:
                    buses.append({
                        "bus": cols[1],
                        "destination": cols[2],
                        "eta": cols[3],
                    })

        result_routes.append({
            "route_id": route["id"],
            "route_name": route["name"],
            "buses": buses,
        })

    return {
        "stop_id": stop_id,
        "stop_name": stop_name,
        "service_type": "BRT",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "routes": result_routes,
    }


def get_cached_stop(stop_id):
    now = time.time()

    with cache_lock:
        item = cache.get(stop_id)

        if item and now - item["cached_at"] < CACHE_SECONDS:
            result = dict(item["data"])
            result["cache"] = {
                "hit": True,
                "age_seconds": round(now - item["cached_at"], 2),
                "ttl_seconds": CACHE_SECONDS,
            }
            return result

    data = scrape_stop(stop_id)

    with cache_lock:
        cache[stop_id] = {
            "cached_at": time.time(),
            "data": data,
        }

    result = dict(data)
    result["cache"] = {
        "hit": False,
        "age_seconds": 0,
        "ttl_seconds": CACHE_SECONDS,
    }

    return result


@app.get("/")
def index():
    return jsonify({
        "name": "Surat BRTS Realtime ETA API",
        "status": "running",
        "endpoints": {
            "stops": "/api/stops",
            "eta_example": "/api/eta/2523",
        },
    })


@app.get("/api/stops")
def api_stops():
    session = make_session()

    response = request_get(session)
    soup = BeautifulSoup(response.text, "html.parser")

    return jsonify({
        "stops": get_stops(soup),
        "count": len(get_stops(soup)),
    })


@app.get("/api/eta/<stop_id>")
def api_eta(stop_id):
    try:
        return jsonify(get_cached_stop(stop_id))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404
    except requests.RequestException as exc:
        return jsonify({
            "error": "SITILINK request failed",
            "details": str(exc),
        }), 502
    except Exception as exc:
        return jsonify({
            "error": "Internal error",
            "details": str(exc),
        }), 500


if __name__ == "__main__":
    print("=" * 60)
    print(" SURAT BRTS REALTIME ETA API")
    print("=" * 60)
    print("API: http://127.0.0.1:5000")
    print("Example: http://127.0.0.1:5000/api/eta/2523")
    print(f"Cache: {CACHE_SECONDS} seconds")
    print()
    print("This provides LIVE ETA data, not GPS coordinates.")
    print("=" * 60)

    app.run(
        host="0.0.0.0",
        port=5000,
        debug=False,
        threaded=True,
    )
