#!/usr/bin/env python3
"""
Inspect the actual public/mobile API endpoints embedded in the Surat
Sitilink APK.

Discovered from the supplied APK:
  https://suratcitytransportapp.co.in/
  Stop/list
  Route/list/
  Route/stoplist/
  Route/stopcoordinates/
  Route/plan/start/
  /nearBy
  /searchRoute

The app also contains Bearer-token refresh/sign-in logic, so a 401/403
is evidence that the endpoint exists but requires app authentication.

Run:
    python3 sitilink_app_api_probe.py
"""

import json
import requests

BASE = "https://suratcitytransportapp.co.in/"

session = requests.Session()
session.headers.update({
    "User-Agent": "Dart/2.19 (dart:io)",
    "Accept": "application/json",
    "Content-Type": "application/json",
})

def show(label, response):
    text = response.text.replace("\n", " ")
    print(f"\n[{response.status_code}] {label}")
    print("URL:", response.url)
    print("Content-Type:", response.headers.get("content-type"))
    print("Body:", text[:1200])

def get(path, params=None):
    try:
        r = session.get(BASE + path, params=params, timeout=15)
        show(f"GET {path}", r)
    except Exception as e:
        print(f"\n[ERROR] GET {path}: {e}")

def post(path, payload):
    try:
        r = session.post(BASE + path, json=payload, timeout=15)
        show(f"POST {path} {payload}", r)
    except Exception as e:
        print(f"\n[ERROR] POST {path}: {e}")

print("=" * 70)
print("SURAT SITILINK APK API DISCOVERY")
print("=" * 70)

# Static/master-data endpoints.
for path in [
    "Stop/list",
    "Route/list/",
    "Route/stoplist/",
]:
    get(path)

# Route 3 is the route we already know from the live ETA scraper:
# (11) UDHANA-SACHIN G.I.D.C
for route_id in ["3", "11"]:
    get("Route/stopcoordinates/", {"routeId": route_id})
    get("Route/stopcoordinates/" + route_id)

    post("Route/stopcoordinates/", {"routeId": route_id})
    post("Route/stopcoordinates/", {"routeID": route_id})

    get("Route/stoplist/", {"routeId": route_id})
    get("Route/stoplist/" + route_id)

# Nearby endpoint: try the parameter names suggested by the app's
# location/map models. These are read-only probes.
for params in [
    {"latitude": 21.1702, "longitude": 72.8311},
    {"lat": 21.1702, "lng": 72.8311},
    {"latitude": 21.1702, "longitude": 72.8311, "radius": 5000},
]:
    get("nearBy", params)

print("\n" + "=" * 70)
print("INTERPRETATION")
print("=" * 70)
print("200 = endpoint is directly usable.")
print("401/403 = endpoint exists but app authentication is required.")
print("404 = this particular URL/method/shape is wrong.")
print("The APK definitely contains Route/stopcoordinates/, so a 404 on")
print("one guessed method does NOT mean the coordinate API doesn't exist.")
