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
- `app/cwar.py` — pure-pandas math for the custom pitcher WAR metric (no `pybaseball` import;
  takes DataFrames in, returns a DataFrame with added columns). Called from
  `pybaseball_client.get_pitcher_cwar`, which does the actual `pb.*` fetching/merging. See
  the README's "cWAR" section for the formula and rationale.

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
- **`pitching_stats_bref`'s `GB/FB` column is actually GB% (a 0-1 rate), not a ratio** — the
  name is misleading. Confirmed by checking known extreme groundball/flyball pitchers against
  the values (e.g. submarine sinkerballer Tyler Rogers tops the leaderboard at 0.64, which
  only makes sense as a rate). `cwar.py` derives FB% as `1 - LD - PU - GB%`; don't re-derive
  it as `GB% / (1 + ratio)` elsewhere, it isn't a ratio.
- **`pitching_stats_bref` returns a 1-based DataFrame index**, not the usual 0-based
  `RangeIndex`. If you compute a pandas `Series` from it and later combine that Series with a
  DataFrame that's been through a `merge()` (which resets the index), pandas will silently
  align by index label and produce `NaN` for everything instead of erroring — this bit `cwar.py`
  during development. `reset_index(drop=True)` right after fetching, before doing any
  Series-producing math, avoids it.

## Conventions

- No ORM/database — every request hits pybaseball (cache permitting) and returns fresh data.
- No auth — this is a local-only single-user app.
- After adding a dependency: `pip install <pkg> && pip freeze > requirements.txt`.
