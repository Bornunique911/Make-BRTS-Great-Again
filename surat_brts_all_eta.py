import json
import time
from datetime import datetime

import requests
import urllib3
from bs4 import BeautifulSoup

# IMPORTANT:
# SITILINK's HTTPS certificate is currently expired.
# This disables certificate verification as a temporary
# development/hackathon workaround.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

URL = "https://www.suratsitilink.org/LiveBusInfo.aspx"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/139.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": URL,
}

STOP_SELECT = "ctl00$ContentPlaceHolder1$ddlstops"
SERVICE_SELECT = "ctl00$ContentPlaceHolder1$ddlservicetype"
ROUTE_SELECT = "ctl00$ContentPlaceHolder1$ddlroute"

# BRT is value 1 according to the live page.
BRT_SERVICE_TYPE = "1"


def make_session():
    session = requests.Session()
    session.headers.update(HEADERS)
    return session


def get_form_data(soup):
    """Extract the current ASP.NET Web Forms state."""
    form = soup.find("form")

    if not form:
        raise RuntimeError("Could not find ASP.NET form")

    data = {}

    # Hidden/text/etc. inputs
    for element in form.find_all("input"):
        name = element.get("name")
        if not name:
            continue

        input_type = element.get("type", "text").lower()

        if input_type in {"submit", "button", "image", "reset", "file"}:
            continue

        if input_type in {"checkbox", "radio"}:
            if element.has_attr("checked"):
                data[name] = element.get("value", "on")
        else:
            data[name] = element.get("value", "")

    # Current selected values of all dropdowns
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
    response = session.get(
        URL,
        verify=False,
        timeout=30,
        headers=HEADERS,
    )

    if response.status_code >= 400:
        print(response.text[:5000])
        response.raise_for_status()

    return response


def request_post(session, soup, event_target, updates):
    """
    Perform an ASP.NET dropdown AutoPostBack while preserving
    the complete form state.
    """
    payload = get_form_data(soup)

    payload["__EVENTTARGET"] = event_target
    payload["__EVENTARGUMENT"] = ""

    payload.update(updates)

    response = session.post(
        URL,
        data=payload,
        verify=False,
        timeout=30,
        headers=HEADERS,
    )

    if response.status_code >= 400:
        print(
            f"\nPOST failed: {response.status_code}"
            f" | event={event_target}"
        )
        print(response.text[:5000])
        response.raise_for_status()

    return response


def get_options(soup, select_id):
    """Return [{'id': ..., 'name': ...}] for a select."""
    select = soup.find("select", id=select_id)

    if not select:
        return []

    options = []

    for option in select.find_all("option"):
        value = option.get("value")
        text = option.get_text(" ", strip=True)

        if value is None:
            continue

        options.append({
            "id": value,
            "name": text,
        })

    return options


def get_stops(soup):
    """Extract all stops from the initial page."""
    stops = get_options(soup, "ContentPlaceHolder1_ddlstops")

    # Remove placeholder options.
    return [
        stop
        for stop in stops
        if stop["id"] not in {"", "0", "-1"}
        and stop["name"].lower() not in {
            "please select stop",
            "select stop",
        }
    ]


def select_stop(session, soup, stop_id):
    response = request_post(
        session,
        soup,
        STOP_SELECT,
        {
            STOP_SELECT: stop_id,
        },
    )

    return BeautifulSoup(response.text, "html.parser")


def select_brt(session, soup, stop_id):
    response = request_post(
        session,
        soup,
        SERVICE_SELECT,
        {
            STOP_SELECT: stop_id,
            SERVICE_SELECT: BRT_SERVICE_TYPE,
        },
    )

    return BeautifulSoup(response.text, "html.parser")


def get_routes(soup):
    """
    Get the route options available after selecting
    a stop and BRT service.
    """
    routes = get_options(
        soup,
        "ContentPlaceHolder1_ddlroute",
    )

    return [
        route
        for route in routes
        if route["id"] not in {"", "0", "-1"}
        and route["name"].lower() not in {
            "please select route",
            "select route",
        }
    ]


def get_eta_for_route(session, soup, stop_id, route_id):
    """
    Select a route and extract the ETA table.
    """
    response = request_post(
        session,
        soup,
        ROUTE_SELECT,
        {
            STOP_SELECT: stop_id,
            SERVICE_SELECT: BRT_SERVICE_TYPE,
            ROUTE_SELECT: route_id,
        },
    )

    result_soup = BeautifulSoup(
        response.text,
        "html.parser",
    )

    table_body = result_soup.find(
        "tbody",
        id="ContentPlaceHolder1_ETATableDetail",
    )

    buses = []

    if not table_body:
        return buses

    for row in table_body.find_all("tr"):
        cols = [
            col.get_text(" ", strip=True)
            for col in row.find_all("td")
        ]

        if len(cols) >= 4:
            buses.append({
                "bus": cols[1],
                "destination": cols[2],
                "eta": cols[3],
            })

    return buses


def scrape_all_brt_eta(delay_between_stops=0.2):
    """
    Scrape all BRT stops and all routes exposed for each stop.

    Returns a JSON-serializable dictionary.
    """
    session = make_session()

    print("Loading SITILINK Live Bus Info...")

    response = request_get(session)
    soup = BeautifulSoup(response.text, "html.parser")

    stops = get_stops(soup)

    print(f"Found {len(stops)} stops.")

    results = []
    total_routes = 0

    for index, stop in enumerate(stops, start=1):
        stop_id = stop["id"]
        stop_name = stop["name"]

        print(
            f"\n[{index}/{len(stops)}] "
            f"{stop_name} ({stop_id})"
        )

        try:
            # Fresh session state is important for each stop.
            stop_soup = select_stop(
                session,
                soup,
                stop_id,
            )

            brt_soup = select_brt(
                session,
                stop_soup,
                stop_id,
            )

            routes = get_routes(brt_soup)

            print(f"  Routes found: {len(routes)}")

            stop_result = {
                "stop_id": stop_id,
                "stop_name": stop_name,
                "service_type": "BRT",
                "routes": [],
            }

            for route in routes:
                route_id = route["id"]
                route_name = route["name"]

                print(
                    f"    Route {route_name} "
                    f"(value={route_id})"
                )

                try:
                    buses = get_eta_for_route(
                        session,
                        brt_soup,
                        stop_id,
                        route_id,
                    )

                    stop_result["routes"].append({
                        "route_id": route_id,
                        "route_name": route_name,
                        "buses": buses,
                    })

                    total_routes += 1

                    if buses:
                        for bus in buses:
                            print(
                                f"      🚌 {bus['bus']} → "
                                f"{bus['destination']} | "
                                f"{bus['eta']}"
                            )
                    else:
                        print("      No live buses.")

                except requests.RequestException as exc:
                    print(
                        f"      Route request failed: {exc}"
                    )

            results.append(stop_result)

            time.sleep(delay_between_stops)

        except requests.RequestException as exc:
            print(
                f"  Stop request failed: {exc}"
            )

        except Exception as exc:
            print(
                f"  Unexpected error: {exc}"
            )

    return {
        "generated_at": datetime.now().astimezone().isoformat(),
        "service_type": "BRT",
        "stop_count": len(results),
        "route_count": total_routes,
        "stops": results,
    }


def save_json(data, filename="all_brt_eta.json"):
    with open(
        filename,
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            data,
            file,
            ensure_ascii=False,
            indent=2,
        )

    print(
        f"\nSaved ETA data to: {filename}"
    )


if __name__ == "__main__":
    data = scrape_all_brt_eta()

    save_json(data)

    print(
        f"\nFinished: "
        f"{data['stop_count']} stops, "
        f"{data['route_count']} routes."
    )
