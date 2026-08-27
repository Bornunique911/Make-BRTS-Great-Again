#!/usr/bin/env python3
"""
Generate mock ETA data from extracted_stops.txt.
Run: python generate_mock_from_stoplist.py
"""

import json
import re
import random
from pathlib import Path
from difflib import SequenceMatcher

STOPLIST_FILE = Path("extracted_stops.txt")
if not STOPLIST_FILE.exists():
    raise FileNotFoundError("extracted_stops.txt not found")

TRANSIT_FILE = Path("transit_data.json")
if not TRANSIT_FILE.exists():
    raise FileNotFoundError("transit_data.json not found")

with open(TRANSIT_FILE, "r", encoding="utf-8") as f:
    transit = json.load(f)

official_stops = transit.get("stops", {})
official_routes = transit.get("routes", {})

# ---- Normalization and matching ----
def normalize_name(name):
    name = name.strip().upper()
    name = re.sub(r"\s+", " ", name)
    name = re.sub(r"[^A-Z0-9\s]", "", name)
    return name

def clean_schedule_name(name):
    name = name.strip().upper()
    suffixes = ["BRTS", "TERMINAL", "GAM", "TER", "BRID", "ROAD", "JUNCTION",
                "CIRCLE", "PARK", "SCHOOL", "TEMPLE", "HOSPITAL", "STATION",
                "DEPOT", "BRIDGE", "SOCIETY", "NAGAR", "CHOWK", "MANDIR"]
    for suf in suffixes:
        name = re.sub(rf"\b{suf}\b", "", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name

# Build lookup
stop_name_to_id = {}
for sid, info in official_stops.items():
    name = info.get("name", "").strip()
    if not name:
        continue
    norm = normalize_name(name)
    if norm not in stop_name_to_id:
        stop_name_to_id[norm] = sid
    cleaned = clean_schedule_name(name)
    cleaned_norm = normalize_name(cleaned)
    if cleaned_norm and cleaned_norm not in stop_name_to_id:
        stop_name_to_id[cleaned_norm] = sid
    compact = norm.replace(" ", "")
    if compact and compact not in stop_name_to_id:
        stop_name_to_id[compact] = sid

official_keys = list(stop_name_to_id.keys())

def fuzzy_match_stop_name(schedule_name, threshold=0.75):
    schedule_norm = normalize_name(schedule_name)
    if schedule_norm in stop_name_to_id:
        return schedule_norm, stop_name_to_id[schedule_norm]
    schedule_cleaned = clean_schedule_name(schedule_name)
    schedule_cleaned_norm = normalize_name(schedule_cleaned)
    if schedule_cleaned_norm in stop_name_to_id:
        return schedule_cleaned_norm, stop_name_to_id[schedule_cleaned_norm]
    compact = schedule_norm.replace(" ", "")
    if compact in stop_name_to_id:
        return compact, stop_name_to_id[compact]
    best = None
    best_score = 0
    for key in official_keys:
        score = SequenceMatcher(None, schedule_norm, key).ratio()
        if score > best_score:
            best_score = score
            best = key
    if best and best_score >= threshold:
        return best, stop_name_to_id[best]
    for key in official_keys:
        if schedule_norm in key or key in schedule_norm:
            return key, stop_name_to_id[key]
    return None, None

# ---- Read stop names from extracted_stops.txt ----
with open(STOPLIST_FILE, "r", encoding="utf-8") as f:
    schedule_stops = [line.strip() for line in f if line.strip()]

print(f"Loaded {len(schedule_stops)} stop names from extracted_stops.txt")

# ---- Match each to official ID ----
matched_stops = {}  # official_id -> list of schedule names (we only need one per stop)
unmatched = []

for stop_name in schedule_stops:
    matched_key, matched_id = fuzzy_match_stop_name(stop_name)
    if matched_id:
        if matched_id not in matched_stops:
            matched_stops[matched_id] = stop_name
    else:
        unmatched.append(stop_name)

print(f"Matched {len(matched_stops)} stops to official IDs")
if unmatched:
    print(f"Unmatched: {len(unmatched)} stops")
    # Print first 20 for inspection
    for s in unmatched[:20]:
        print(f"  {s}")

# ---- Generate mock ETA data ----
bus_codes = ["ABCD", "EFGH", "IJKL", "MNOP", "QRST", "UVWX", "YZAB", "CDEF", "GHIJ", "KLMN"]
destinations = ["City Centre", "Airport", "Railway Station", "Bus Stand",
                "Hospital", "Market", "University", "Industrial Estate"]
route_ids = list(official_routes.keys())

mock_data = {}
for stop_id, stop_name in matched_stops.items():
    # Assign 1–4 random routes
    num_routes = random.randint(1, min(4, len(route_ids)))
    selected_routes = random.sample(route_ids, num_routes)
    route_buses = []
    for route_id in selected_routes:
        num_buses = random.randint(0, 3)
        buses = []
        for _ in range(num_buses):
            eta_min = random.randint(1, 15)
            buses.append({
                "bus": f"GJ-05-{random.choice(bus_codes)}-4004",
                "destination": random.choice(destinations),
                "eta": f"{eta_min} min"
            })
        if buses:
            route_buses.append({
                "route_id": route_id,
                "route_name": official_routes.get(route_id, {}).get("name", f"Route {route_id}"),
                "buses": buses
            })
    mock_data[stop_id] = {
        "stop_id": stop_id,
        "stop_name": official_stops.get(stop_id, {}).get("name", stop_name),
        "routes": route_buses
    }

output_path = Path("mock_eta_data.json")
with open(output_path, "w", encoding="utf-8") as f:
    json.dump(mock_data, f, indent=2, ensure_ascii=False)

print(f"✅ Generated mock ETA data for {len(mock_data)} stops -> {output_path}")