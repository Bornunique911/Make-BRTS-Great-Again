import requests
from bs4 import BeautifulSoup

def scrape_surat_eta(stop_id="2523", service_type="3", route_id="3"):
    url = "https://www.suratsitilink.org/LiveBusInfo.aspx"
    
    # Start a session so we don't lose our connection state (like you lose focus studying)
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    })

    def get_asp_vars(soup):
        """Extracts Microsoft's hidden security tokens."""
        return {
            "__VIEWSTATE": soup.find(id="__VIEWSTATE")["value"] if soup.find(id="__VIEWSTATE") else "",
            "__VIEWSTATEGENERATOR": soup.find(id="__VIEWSTATEGENERATOR")["value"] if soup.find(id="__VIEWSTATEGENERATOR") else "",
            "__EVENTVALIDATION": soup.find(id="__EVENTVALIDATION")["value"] if soup.find(id="__EVENTVALIDATION") else ""
        }

    # STEP 1: Fetch the empty page to get the initial tokens
    print("1. Infiltrating the portal...")
    res = session.get(url)
    soup = BeautifulSoup(res.text, "html.parser")
    asp_vars = get_asp_vars(soup)

    # STEP 2: Fake selecting the Stop (Kharwar Nagar = 2523)
    print(f"2. Locking onto Stop ID: {stop_id}...")
    payload = asp_vars.copy()
    payload.update({
        "__EVENTTARGET": "ctl00$ContentPlaceHolder1$ddlstops",
        "__EVENTARGUMENT": "",
        "ctl00$ContentPlaceHolder1$ddlstops": stop_id
    })
    res = session.post(url, data=payload)
    soup = BeautifulSoup(res.text, "html.parser")
    asp_vars = get_asp_vars(soup)

    # STEP 3: Fake selecting the Service Type (BRTS = 3)
    print(f"3. Setting Service Type to: {service_type}...")
    payload = asp_vars.copy()
    payload.update({
        "__EVENTTARGET": "ctl00$ContentPlaceHolder1$ddlservicetype",
        "__EVENTARGUMENT": "",
        "ctl00$ContentPlaceHolder1$ddlstops": stop_id,
        "ctl00$ContentPlaceHolder1$ddlservicetype": service_type
    })
    res = session.post(url, data=payload)
    soup = BeautifulSoup(res.text, "html.parser")
    asp_vars = get_asp_vars(soup)

    # STEP 4: Fake selecting the Route (Route 11 = 3)
    print(f"4. Requesting ETAs for Route ID: {route_id}...\n")
    payload = asp_vars.copy()
    payload.update({
        "__EVENTTARGET": "ctl00$ContentPlaceHolder1$ddlroute",
        "__EVENTARGUMENT": "",
        "ctl00$ContentPlaceHolder1$ddlstops": stop_id,
        "ctl00$ContentPlaceHolder1$ddlservicetype": service_type,
        "ctl00$ContentPlaceHolder1$ddlroute": route_id
    })
    res = session.post(url, data=payload)
    soup = BeautifulSoup(res.text, "html.parser")

    # STEP 5: Extract the ETA Table Data
    print("-" * 30)
    print("LIVE ARRIVALS EXTRACTED:")
    print("-" * 30)
    
    table_body = soup.find("tbody", id="ContentPlaceHolder1_ETATableDetail")
    if table_body:
        rows = table_body.find_all("tr")
        if not rows:
            print("No buses currently arriving for this route. They are either parked or off-duty.")
        for row in rows:
            cols = [col.text.strip() for col in row.find_all("td")]
            if len(cols) >= 4:
                print(f"🚌 Bus: {cols[1]} | To: {cols[2]} | Arriving In: {cols[3]}")
    else:
        print("Error: Could not locate the ETA table in the HTML. The parameters might be invalid.")

# Execute the bypass
if __name__ == "__main__":
    scrape_surat_eta()