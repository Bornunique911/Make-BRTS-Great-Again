# Make-BRTS-Great-Again

A prototype web application for Surat BRTS that visualises routes and stops on an interactive map, and demonstrates a mock ETA feature using pre‑generated data.

> **Hackathon notice:** This project is built for the `Build What Moves India` (https://buildwhatmovesindia.com/brief) hackathon. It does **not** access, scrape, or interfere with any live government system. All ETAs are **mock data** for demonstration purposes.

## Live demo

- **GitHub Pages (static frontend):**  
  `https://bornunique911.github.io/Make-BRTS-Great-Again/`
- **Vercel (Flask backend + frontend):**  
  `https://brtsinfo.vercel.app/`

---
## Features

- Interactive map with all BRTS stops and routes (OpenStreetMap).
- Search stops and filter by route.
- Station count: **1,100 official BRTS stops** (from the official catalogue).
- Mock ETA display when you click a stop – shows realistic bus numbers and arrival times.
- Dark / midnight theme that adapts to the time of day.

---
## How it works

### Data sources

- **Stop and route geometry:** static `transit_data.json` (committed to the repository). This file was built once from official open data.
- **ETAs:** static `mock_eta_data.json` – generated from official route schedules (PDF) and committed to the repository. **No live requests are ever made** to `suratsitilink.org` or any other government system.

---
### Deployment

- **GitHub Pages** serves the static frontend (`index.html`) directly from the `demo_website` branch.
- **Vercel** runs a small Flask app that serves the same `index.html` and provides a simple API:
  - `/api/stops` – returns all stops.
  - `/api/routes` – returns all routes.
  - `/api/routes/<route_id>/stops` – returns ordered stops for a route.
  - `/api/eta/<stop_id>` – returns mock ETA data for a given stop.

---
### Using the API

If you want to run the Flask backend locally:

```bash
cd flask/app
pip install -r requirements.txt
python app.py
```
The server will start at http://localhost:5000. Open that URL in your browser – the map and mock ETAs will work.

---
### Technology stack
Frontend: HTML, CSS, JavaScript, Leaflet (OpenStreetMap), SunCalc.

Backend: Python, Flask, Flask-Cors.

Hosting: GitHub Pages (static) + Vercel (Flask).

Data: Static JSON files (no live scraping).

---
### Contributing
This is a hackathon prototype – feedback and suggestions are welcome. Please open an issue or submit a pull request.
