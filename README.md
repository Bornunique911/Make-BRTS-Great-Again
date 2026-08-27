# Make-BRTS-Great-Again

Unified Surat BRTS API for live ETAs and local route geometry.

## GitHub Pages

The static frontend starts from `index.html` and works without Flask. Enable
GitHub Pages for the repository's `main` branch and root folder, then open the
published Pages URL. It loads `transit_data.json` directly and provides the
OpenStreetMap route and stop map.

Live ETAs cannot run inside GitHub Pages because Pages is static hosting. To
enable them, deploy `Surat Realtime API.py` to a Python host and open the Pages
URL with its API URL as a query parameter:

```text
https://YOUR-USER.github.io/Make-BRTS-Great-Again/?api=https://YOUR-API.example.com
```

The API host must allow CORS. Without `?api=...`, the map and all static route
features still work and the ETA panel explains how to connect a backend.

The server listens on `http://127.0.0.1:5000`.

Endpoints:

- `GET /api/stops` - all stops from `transit_data.json`
- `GET /api/routes` - all routes
- `GET /api/routes/<route_id>/stops` - ordered route coordinates
- `GET /api/eta/<stop_id>` - live arrivals from SITILINK

The live ETA endpoint keeps the ASP.NET session and hidden form state across
the stop, BRT service, and route postbacks. Responses are cached for 15
seconds. The upstream site currently has an invalid TLS certificate, so the
script deliberately disables certificate verification for that upstream
request.
