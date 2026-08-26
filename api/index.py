#!/usr/bin/env python3
"""Vercel entrypoint for the Surat BRTS Flask application."""

import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent / "flask" / "app"
REPO_DIR = APP_DIR.parent.parent
sys.path.insert(0, str(APP_DIR))
sys.path.insert(0, str(REPO_DIR))

from importlib import import_module

# Import the Flask app and its robust ETA resolver
app_module = import_module("app")
app = app_module.app
get_cached_stop = app_module.get_cached_stop

# Remove the old /api/eta/<stop_id> rule if it exists
from flask import jsonify

for _rule in list(app.url_map.iter_rules()):
    if _rule.rule == "/api/eta/<stop_id>":
        app.url_map._rules.remove(_rule)
        endpoint = _rule.endpoint
        endpoint_rules = app.url_map._rules_by_endpoint.get(endpoint)
        if endpoint_rules and _rule in endpoint_rules:
            endpoint_rules.remove(_rule)
        app.view_functions.pop(endpoint, None)

# Register new endpoint using app.py's robust resolver
@app.get("/api/eta/<stop_id>")
def corrected_api_eta(stop_id):
    try:
        return jsonify(get_cached_stop(stop_id))
    except LookupError as exc:
        return jsonify({"error": str(exc)}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({
            "error": "SITILINK ETA request failed",
            "details": str(exc),
        }), 502