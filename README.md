# Baseball Stats App

Local baseball analytics dashboard. FastAPI backend wraps [pybaseball](https://github.com/jldbc/pybaseball)
(Baseball-Reference + Baseball Savant) with on-disk caching; a React (Vite) frontend shows
standings and batting/pitching/Statcast leaderboards with player search, team filtering, and
sortable columns.

## Run it

**Backend** (http://localhost:8000):

```bash
cd backend
source venv/bin/activate
uvicorn app.main:app --reload
```

**Frontend** (http://localhost:5173):

```bash
cd frontend
npm run dev
```

The frontend dev server proxies `/api/*` to the backend, so just open http://localhost:5173.

## Project layout

```
backend/
  app/
    main.py                 FastAPI app + CORS
    pybaseball_client.py    wraps pybaseball calls, enables its disk cache
    routers/stats.py        API routes (see below)
frontend/
  src/App.jsx               tabs: standings / batting / pitching / exit velo / expected stats
  src/App.css
```

## API routes

| Route | Source | Notes |
|---|---|---|
| `GET /api/standings?season=` | Baseball-Reference | division standings |
| `GET /api/stats/batting?season=` | Baseball-Reference | full-season batting leaderboard |
| `GET /api/stats/pitching?season=` | Baseball-Reference | full-season pitching leaderboard |
| `GET /api/savant/batting/exitvelo?season=&min_bbe=` | Baseball Savant | exit velo / barrel rate |
| `GET /api/savant/pitching/exitvelo?season=&min_bbe=` | Baseball Savant | exit velo / barrel rate allowed |
| `GET /api/savant/batting/expected?season=&min_pa=` | Baseball Savant | xBA / xSLG / xwOBA |
| `GET /api/savant/pitching/expected?season=&min_pa=` | Baseball Savant | xBA / xSLG / xwOBA / xERA allowed |
| `GET /api/players/search?last=&first=` | Baseball-Reference | player ID lookup |
| `GET /api/statcast/recent?days_back=` | Baseball Savant | raw pitch-level Statcast rows |

## Frontend features

- Tabs for Standings, Batting, Pitching, Exit Velo/Barrels, Expected Stats (the last two
  toggle between batting and pitching leaders).
- Player name search, team filter, and a min-PA/min-IP/min-BBE threshold per tab.
- Click any column header to sort; click again to reverse direction.
- Results are cached client-side per (tab, season, filter) combination, so switching tabs
  back and forth doesn't re-fetch.

## Why not FanGraphs?

`pybaseball.batting_stats` / `pitching_stats` scrape FanGraphs' leaderboard pages, which are
now behind Cloudflare bot protection — plain scraping gets a 403, and the harder version of
the challenge is an interactive CAPTCHA that isn't worth automating around. This app uses
Baseball-Reference (`batting_stats_bref` / `pitching_stats_bref`) for season leaderboards
instead, which pybaseball can still reach directly. If you want FanGraphs-specific numbers
(fWAR, their pitch-arsenal stats), the practical option is exporting a CSV from FanGraphs
yourself (their leaderboard pages have an "Export Data" button) and loading it manually —
not something this app automates.

## Data quirks worth knowing

- Baseball-Reference gives players traded mid-season a single row with a comma-joined team
  string (e.g. `"Arizona,Seattle"`). The frontend's team filter splits on comma so it still
  matches those players; keep that in mind if you query the API directly.
- Some scraped names come through as literal `\xNN` byte-escape sequences instead of real
  UTF-8 (e.g. `Acu\xc3\xb1a`); `pybaseball_client._fix_mojibake` repairs these before the API
  returns them.
- NaN values from pandas aren't valid JSON; `pybaseball_client._records` round-trips through
  `df.to_json()` so they come back as `null` instead of crashing the response.
- pybaseball's own cache (`pb.cache.enable()`) avoids re-scraping identical requests, but it's
  a disk cache with no TTL — delete `~/.pybaseball/cache` if you need to force-refresh
  mid-season data.
