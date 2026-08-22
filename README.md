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
| `GET /api/stats/pitching/cwar?season=` | Baseball-Reference + Baseball Savant | custom pitcher WAR (see below) |
| `GET /api/savant/batting/exitvelo?season=&min_bbe=` | Baseball Savant | exit velo / barrel rate |
| `GET /api/savant/pitching/exitvelo?season=&min_bbe=` | Baseball Savant | exit velo / barrel rate allowed |
| `GET /api/savant/batting/expected?season=&min_pa=` | Baseball Savant | xBA / xSLG / xwOBA |
| `GET /api/savant/pitching/expected?season=&min_pa=` | Baseball Savant | xBA / xSLG / xwOBA / xERA allowed |
| `GET /api/players/search?last=&first=` | Baseball-Reference | player ID lookup |
| `GET /api/statcast/recent?days_back=` | Baseball Savant | raw pitch-level Statcast rows |

## Frontend features

- Tabs for Standings, Batting, Pitching, cWAR, Exit Velo/Barrels, Expected Stats (the last two
  toggle between batting and pitching leaders).
- Player name search, team filter, and a min-PA/min-IP/min-BBE threshold per tab.
- Click any column header to sort; click again to reverse direction.
- Results are cached client-side per (tab, season, filter) combination, so switching tabs
  back and forth doesn't re-fetch.

## cWAR: a whole-picture pitcher WAR

FanGraphs-style FIP-based WAR deliberately looks at only three "true outcomes" (strikeouts,
walks, home runs) and ignores everything else about a pitcher's performance, on the theory
(DIPS) that what happens to a ball once it's in play is mostly defense and luck. That's true
of raw BABIP, but it throws out real, moderately-repeatable pitcher skill along with the
noise: batted-ball mix (ground ball / fly ball / pop-up rate) and contact quality (exit
velocity allowed) both show real year-to-year signal, just noisier than K%/BB%.

`cWAR` (`backend/app/cwar.py`) blends three things instead of just the FIP three:

1. **FIP core** (~70% weight) — the standard `(13·HR + 3·(BB+HBP) − 2·K)/IP` formula, using a
   FIP constant computed live from that season's league totals rather than a hardcoded one.
2. **Statcast xERA** (~25% weight, from `/api/savant/pitching/expected`) — captures contact
   quality (exit velocity + launch angle allowed) that FIP ignores entirely. This weight
   shrinks toward the FIP core for pitchers with few tracked batted-ball events, since
   contact-quality metrics need a bigger sample to stabilize than K/BB do.
3. **A small batted-ball-mix adjustment** — rewards ground-ball/pop-up tendencies (derived
   from bref's `GB/FB`, `LD`, `PU` columns), which are far more repeatable than raw BABIP and
   otherwise invisible to FIP.

The blended runs-allowed rate is converted to wins above replacement using a league-average
ERA computed live from the same season's data, a standard replacement-level factor (1.13x
league average), and the usual ~10 runs = 1 win approximation. All the weights and
coefficients are named constants at the top of `cwar.py` — they're research-informed starting
points, not statistically fitted, so treat them as tunable.

One data quirk worth knowing if you touch this code: pybaseball labels bref's ground-ball-rate
column `GB/FB`, but it's actually GB% (a rate, 0-1), not a ratio — confirmed by checking known
extreme groundball pitchers (e.g. submarine sinkerballer Tyler Rogers) against the values. Treat
it as GB%, not `GB/(FB)`.

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
