# Baseball Stats App

Local baseball analytics dashboard. FastAPI backend wraps [pybaseball](https://github.com/jldbc/pybaseball)
(FanGraphs, Baseball Savant, Baseball Reference) with on-disk caching; a React (Vite) frontend
displays standings and batting/pitching leaderboards.

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
    main.py              FastAPI app + CORS
    pybaseball_client.py wraps pybaseball calls, enables its disk cache
    routers/stats.py     /api/standings, /api/stats/batting, /api/stats/pitching, ...
frontend/
  src/App.jsx            standings / batting / pitching tabs
```

## Notes

- pybaseball scrapes public stats sites; it's not truly live pitch-by-pitch data, but
  `statcast_recent` and season leaderboards refresh as MLB updates them.
- pybaseball's own cache (`pb.cache.enable()`) avoids re-scraping identical requests.
