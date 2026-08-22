# backend/

FastAPI app that wraps `pybaseball`. Python 3.12, venv at `backend/venv` (not committed).

```bash
source venv/bin/activate
uvicorn app.main:app --reload   # http://localhost:8000
```

## Structure

- `app/main.py` — FastAPI app, CORS (only allows `http://localhost:5173`), mounts the router.
- `app/pybaseball_client.py` — the only place that imports `pybaseball` or calls `pb.*`.
  Every function returns plain `list[dict]` (or `list[list[dict]]` for standings), never a
  DataFrame — routers should never touch pandas.
- `app/routers/stats.py` — thin HTTP layer. Each route just parses query params and calls a
  `pybaseball_client` function. Keep it that way; put scraping/data logic in the client module.

## Adding a new stat endpoint

1. Add a function to `pybaseball_client.py` that calls the relevant `pb.*` function and
   returns `_records(df)` (see below — do not call `.to_dict()` directly).
2. Add a route to `routers/stats.py` that calls it.
3. If it's a Baseball Savant leaderboard with a qualifier (`minBBE`, `minPA`, etc.), expose
   that as a query param — the frontend's Exit Velo / Expected Stats tabs use these as real
   server-side filters, not just cosmetic ones.

## Data-source gotchas (read before changing `pybaseball_client.py`)

- **Never use `pb.batting_stats` / `pb.pitching_stats` (FanGraphs).** FanGraphs' leaderboard
  scrape is behind Cloudflare bot protection — plain requests 403, and driving a real browser
  hits an interactive Turnstile CAPTCHA instead. Use `batting_stats_bref` / `pitching_stats_bref`
  (Baseball-Reference) for season leaderboards, and `statcast_*` functions (Baseball Savant)
  for advanced metrics — neither is blocked.
- **Always route DataFrames through `_records(df)`**, not `df.to_dict(orient="records")`
  directly. Two real bugs are fixed there:
  - `df.to_dict()` leaves raw `NaN` in place, which Starlette's default JSON encoder rejects
    (`ValueError: Out of range float values are not JSON compliant`). `_records` goes through
    `df.to_json()` first, which correctly emits `null`.
  - Baseball-Reference scraping sometimes yields names as literal backslash-escape text —
    `"Acu\\xc3\\xb1a"` as actual characters, not real UTF-8 — instead of `"Acuña"`.
    `_fix_mojibake` repairs this. If you add a new bref-backed endpoint and see mangled
    accented names, this is why; don't special-case it per-endpoint, `_records` already
    applies it to every string field.
- `pb.cache.enable()` runs at import time and persists to `~/.pybaseball/cache` with no TTL.
  If a test looks stale (e.g. after a trade or a stat correction), that's almost always why —
  delete the relevant file under that directory rather than assuming the endpoint is broken.

## Conventions

- No ORM/database — every request hits pybaseball (cache permitting) and returns fresh data.
- No auth — this is a local-only single-user app.
- After adding a dependency: `pip install <pkg> && pip freeze > requirements.txt`.
