"""Fast API-facing wrapper for the existing Sitilink ETA scraper.

The repository's main API expects get_eta(stop_id), while the original
ASP.NET scraper exposes scrape_single_stop(). Keep the scraping logic in one
place and adapt its response here.
"""

from surat_brts_eta_menu import scrape_single_stop


def get_eta(stop_id: str):
    """Return the live ETA payload for one Sitilink stop."""
    stop_id = str(stop_id).strip()
    if not stop_id:
        raise ValueError("stop_id is required")

    result = scrape_single_stop(stop_id)
    if result is None:
        raise LookupError(f"Stop {stop_id!r} was not found")

    return result
