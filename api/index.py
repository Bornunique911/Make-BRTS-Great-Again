"""Vercel entrypoint for the Surat BRTS Flask application."""

import sys
from pathlib import Path

# Keep the existing Flask application in flask/app, but expose it through
# Vercel's standard Python serverless-function entrypoint.
APP_DIR = Path(__file__).resolve().parent.parent / "flask" / "app"
sys.path.insert(0, str(APP_DIR))

from app import app  # noqa: E402,F401
