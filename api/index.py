#!/usr/bin/env python3
"""Vercel entrypoint for the Surat BRTS Flask application."""

import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent / "flask" / "app"
REPO_DIR = APP_DIR.parent.parent
sys.path.insert(0, str(APP_DIR))
sys.path.insert(0, str(REPO_DIR))

from importlib import import_module  # noqa: E402

app = import_module("app").app


# The Flask app contains an older ETA implementation.  Vercel must use the
# same tested adapter as the FastAPI/local backend so that map stop IDs are
# resolved against the current Sitilink LiveBusInfo stop list consistently.
from flask import jsonify  # noqa: E402
from surat_brts_eta_fast import get_eta  # noqa: E402


# Remove the old /api/eta/<stop_id> rule before registering the corrected one.
for _rule in list(app.url_map.iter_rules()):
    if _rule.rule == "/api/eta/<stop_id>":
        app.url_map._rules.remove(_rule)
        endpoint = _rule.endpoint
        endpoint_rules = app.url_map._rules_by_endpoint.get(endpoint)
        if endpoint_rules and _rule in endpoint_rules:
            endpoint_rules.remove(_rule)
        app.view_functions.pop(endpoint, None)


@app.get("/api/eta/<stop_id>")
def corrected_api_eta(stop_id):
    try:
        return jsonify(get_eta(stop_id))
    except LookupError as exc:
        return jsonify({"error": str(exc)}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({
            "error": "SITILINK ETA request failed",
            "details": str(exc),
        }), 502
