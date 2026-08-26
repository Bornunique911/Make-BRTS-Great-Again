#!/usr/bin/env python3
"""Vercel entrypoint for the Surat BRTS FastAPI application."""
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from surat_brts_eta_fast import get_eta

app = FastAPI(title="Surat BRTS Live ETA API")

# The frontend is hosted on GitHub Pages, so browser requests must be
# explicitly allowed. Keep this permissive for the hackathon deployment.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def read_root():
    return {"status": "Sitilink API is running!"}


@app.get("/api/eta/{stop_id}")
def fetch_bus_eta(stop_id: str):
    try:
        eta_data = get_eta(stop_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        # Do not expose the upstream scraper internals to the browser.
        raise HTTPException(
            status_code=502,
            detail=f"Live Sitilink ETA is temporarily unavailable: {exc}",
        ) from exc

    # get_eta() returns the complete scrape_single_stop payload. Returning
    # it directly keeps the frontend compatible with routes[].buses[].
    return eta_data
