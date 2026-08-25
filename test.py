import requests
from bs4 import BeautifulSoup

url = "https://www.suratsitilink.org/LiveBusInfo.aspx"

# Use a Session to reuse the underlying TCP connection (massive speed boost)
session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Content-Type": "application/x-www-form-urlencoded"
})

print("1. Stealing initial tokens...")
get_resp = session.get(url)
soup = BeautifulSoup(get_resp.text, 'html.parser')

viewstate = soup.find("input", {"id": "__VIEWSTATE"})["value"]
eventvalidation = soup.find("input", {"id": "__EVENTVALIDATION"})["value"]
# Grab generator if it exists
viewstategen = soup.find("input", {"id": "__VIEWSTATEGENERATOR"})
viewstategen = viewstategen["value"] if viewstategen else ""

print("2. Firing the God Payload...")
# NOTE: You MUST replace these exact 'ctl00$...' names with the actual 
# 'name' attributes of the dropdowns from the HTML source code!
payload = {
    "__EVENTTARGET": "ctl00$ContentPlaceHolder1$ddlroute", # The dropdown that triggers the table
    "__EVENTARGUMENT": "",
    "__VIEWSTATE": viewstate,
    "__VIEWSTATEGENERATOR": viewstategen,
    "__EVENTVALIDATION": eventvalidation,
    
    # Inject your choices all at once
    "ctl00$ContentPlaceHolder1$ddlstop": "2523",   # Stop ID
    "ctl00$ContentPlaceHolder1$ddlservice": "1",   # Service Type
    "ctl00$ContentPlaceHolder1$ddlroute": "3"      # Route ID
}

post_resp = session.post(url, data=payload)

if "Live Arrivals" in post_resp.text or "table" in post_resp.text:
    print("🔥 Bypass Successful!")
    # Parse the table here with BeautifulSoup
else:
    print("❌ Bypass Failed. ASP.NET EventValidation blocked it.")