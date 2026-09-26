# backend/

FastAPI app that wraps `pybaseball`. Python 3.12, venv at `backend/venv` (not committed).

```bash
source venv/bin/activate
uvicorn app.main:app --reload   # http://localhost:8000
```

## Structure

- `app/main.py` — FastAPI app, CORS (only allows `http://localhost:5173`), mounts the router.
  Also exception handlers so errors come back as JSON with a `detail` message the frontend can
  display, instead of a bare "Internal Server Error" text body it can't parse. Only
  `requests.RequestException` (a network/HTTP failure reaching bref/Savant) is a 502
  "Upstream data fetch failed"; everything else is a 500, so a bug in the app's own math isn't
  misreported as the data source being down.
- `app/pybaseball_client.py` — the only place that imports `pybaseball` or calls `pb.*`.
  Every function returns plain `list[dict]` (or `list[list[dict]]` for standings), never a
  DataFrame — routers should never touch pandas.
- `app/routers/stats.py` — thin HTTP layer. Each route just parses query params and calls a
  `pybaseball_client` function. Keep it that way; put scraping/data logic in the client module.
  Query params are bounds-checked here (`season` 1871–2100, `days_back` ≤ 30, `min_*` ≥ 0) —
  pybaseball fails slowly and confusingly on nonsense inputs, so reject them at the HTTP layer.
- `app/cwar.py` — pure-pandas math for the custom pitcher WAR metric (no `pybaseball` import;
  takes DataFrames in, returns a DataFrame with added columns). Called from
  `pybaseball_client.get_pitcher_cwar`, which does the actual `pb.*` fetching/merging. See
  the README's "cWAR" section for the formula and rationale. Two things here are load-bearing
  and easy to break:
  - `cwar.rfip()` is a **shared** helper — `compute()` and `scripts/tune_cwar_weights.py` both
    call it, so the weights stay fitted on exactly the rate the runtime blends. Don't inline
    that math into either caller.
  - `cwar.batted_ball_adjustment()` is shared the same way, so the tuning script's batted-ball
    checks test the adjustment the runtime actually applies.
  - The batted-ball adjustment is scaled by `(1 - bip_reliability)` on purpose. xERA already
    encodes batted-ball mix (it's built from exit velocity *and* launch angle), so applying
    both at full strength double-counts. The data only weakly backs this (dropping the
    adjustment won 3/4 folds, small gaps), so treat it as a design choice, not a proven one.
    Also know that the fade leaves the adjustment nearly inert past ~35 IP; see the README.
- `scripts/tune_cwar_weights.py` — offline, manually-run, read-only analysis (NOT imported by
  the app). Fits cWAR's rFIP/xERA blend weights against next-season park-adjusted ERA with
  leave-one-season-pair-out validation, and reports the batted-ball diagnostics. Run it before
  changing `FIP_WEIGHT_BASE`/`CONTACT_WEIGHT_BASE`, and paste its constants block only if the
  refit wins consistently across folds (as of the 2021-2025 pairs it does not: the data can't
  distinguish mixes between ~0.53 and ~0.78 rFIP weight). **Score every setting
  scale-invariantly** (its own scale, fitted on training folds): next-season ERA regresses ~40%
  toward the mean, so scoring blends at a fixed 1x scale measures over-dispersion, not the
  mix. An earlier version got this wrong, and it produced a spurious "refit loses 0/4 folds"
  result and sign-flipped batted-ball coefficients. It fetches through the same disk cache the app
  uses; reuse `pybaseball_client._bwar_pitch_for_season` rather than calling `pb.bwar_pitch`
  per season, or you'll refetch a 100k-row file each time.
- `app/guts.py` — static, hand-transcribed per-season FanGraphs "Guts!" constants (wOBA linear
  weights, FIP constant, runs-per-win). pybaseball has no API for these; add a new season's row
  once FanGraphs publishes it.
- `app/park_factors.py`, `app/fwar_batting.py`, `app/fwar_pitching.py` — real fWAR-methodology
  WAR for batters/pitchers, a separate metric from cWAR. See the README's "fWAR" section for
  the formula and its disclosed approximations (park/positional value come from
  Baseball-Reference's daily WAR files; fielding value comes from Statcast OAA/catcher framing;
  baserunning is a real wSB calculation).
- `app/bwar_pitching.py` — real bWAR-methodology (RA9/runs-allowed based) WAR for pitchers, a
  third distinct metric alongside cWAR and fWAR. Shares `park_factors.py` with fWAR (including
  `park_factors.primary_team`, the one-row-per-stint team lookup both modules use). See the
  README's "bWAR" section.
- `app/pitcher_war_chassis.py` — the shared rate-to-WAR conversion (park adjustment, dynamic
  runs-per-win, starter/reliever replacement split) used by all three pitcher WAR modules.
  Each metric supplies its own believed runs-allowed rate (RA9 / FIP-on-RA9 / the cWAR blend);
  the chassis does the rest. Keep it that way — a metric with its own replacement level or
  park convention silently breaks the WAR Compare tab's comparability (cWAR originally had a
  flat 1.13× replacement factor and no park adjustment, which made it read ~35% low league-wide
  and gave Coors pitchers absurdly negative values).
- `app/war_compare.py` — pure merge (no new math) of bWAR/fWAR/cWAR's already-computed output
  onto one row per pitcher, for the frontend's "WAR Compare" tab. Called from
  `pybaseball_client.get_pitcher_war_compare`.
- `app/swar.py` — **prototype**, pure-pandas math for sWAR, a pitch-quality pitcher WAR (a Stuff
  model stacked with a Command model from OpenCommand's inferred catcher targets). Same contract
  as `cwar.py`: no `pybaseball` import, and no model training either — the per-pitch Stuff and
  Command predictions come in from `scripts/swar_prototype.py`. **NOT wired into the app yet**:
  no route, no `pybaseball_client` function, not in WAR Compare. It still goes through
  `pitcher_war_chassis.war_from_rate` like the other three, so it stays comparable. One
  load-bearing detail: `sRA9` is built from pitch physics and per-pitch run values, so it's
  already park-neutral. The chassis divides the rate by PF, so `compute()` pre-multiplies
  `sRA9 × PF` to cancel that. Don't "fix" this by dropping the multiply, or you'll park-adjust
  twice. PF still feeds dynamic runs-per-win.
- `scripts/swar_prototype.py` — offline, manually-run, network-bound prototype (NOT imported by
  the app). Pulls Statcast (pybaseball, weekly chunks, resumable) and the OpenCommand subset
  (Hugging Face), trains the Stuff/Command models, and prints the next-season validation report
  against ERA/FIP/rFIP/cWAR. Caches everything under `backend/data/swar/` (gitignored). Things to
  know:
  - pybaseball's Statcast has no `play_id`, which is OpenCommand's key, so the join is on
    `(game_pk, pitcher, batter, vx0, vy0)` with the velocities rounded to 0.01. The script
    prints the join rate and **hard-stops below 90%**. If it trips, check the join key first;
    don't lower the threshold.
  - Don't run two pulls at once. They write the same chunk/parquet files in the cache dir.
  - Its extra deps (`scikit-learn`, `huggingface_hub`) live in `requirements-prototype.txt`,
    kept out of the app's runtime `requirements.txt`. Don't fold them in with `pip freeze`
    unless sWAR actually ships.
  - OpenCommand is CC BY-NC-SA 4.0 (attribution, non-commercial). Keep the credit in the README
    if any of this ever reaches the app.

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
- `pb.cache.enable()` runs at import time and persists to `~/.pybaseball/cache`. Entries expire
  7 days after they're fetched by default (recorded per-call as an `"expires"` date in each
  `.cache_record.json`). If a test looks stale (e.g. after a trade or a stat correction),
  that's almost always why — delete the relevant file under that directory rather than
  assuming the endpoint is broken.
- **`pitching_stats_bref`'s `GB/FB` column is actually GB% (a 0-1 rate), not a ratio** — the
  name is misleading. Confirmed by checking known extreme groundball/flyball pitchers against
  the values (e.g. submarine sinkerballer Tyler Rogers tops the leaderboard at 0.64, which
  only makes sense as a rate). `cwar.py` derives FB% as `1 - LD - PU - GB%`; don't re-derive
  it as `GB% / (1 + ratio)` elsewhere, it isn't a ratio.
- **`pitching_stats_bref` has `BF` (batters faced)**, which is what makes a real balls-in-play
  count possible: `BIP = BF - SO - BB - HBP - HR` (sacrifices/interference are a rounding error
  at this scale). `cwar.rfip` uses it with `FB%` to get each pitcher's fly-ball count for the
  HR/FB regression. There's no direct batted-ball *count* column — only rates — so this is the
  way to get one.
- **`pitching_stats_bref` returns a 1-based DataFrame index**, not the usual 0-based
  `RangeIndex`. If you compute a pandas `Series` from it and later combine that Series with a
  DataFrame that's been through a `merge()` (which resets the index), pandas will silently
  align by index label and produce `NaN` for everything instead of erroring — this bit `cwar.py`
  during development. `reset_index(drop=True)` right after fetching, before doing any
  Series-producing math, avoids it.
- **`pb.bwar_bat()` / `pb.bwar_pitch()` (Baseball-Reference's daily WAR files, `return_all=True`
  for the full column set) are one row per player-*team-stint*, not per player** — a player
  traded mid-season has multiple rows. Aggregate to one row per `mlb_ID` before merging (see
  `fwar_batting._aggregate_bwar_bat`/`fwar_pitching._primary_team`): sum the run components
  across stints, and take the highest-PA (batting) or highest-IPouts (pitching) stint for the
  player's primary `team_ID`/`lg_ID`. These files are full-MLB-history (100k+ rows) fetched
  whole each call — filter to the target season immediately.
- **Never join park factors (or anything team-specific) on bref's `Tm` column** — it holds
  ambiguous city names for traded players (e.g. `"Los Angeles"`, `"New York"`, and `"Chicago"`
  each map to two different teams/parks). Use the `team_ID` code from the `bwar_bat`/
  `bwar_pitch` merge instead (see `park_factors.py`).
- When computing a league-wide average rate (e.g. league FIP-on-RA9-basis in
  `fwar_pitching.py`), **weight it by IP/PA, not a plain `.mean()`** — an unweighted mean is
  badly skewed by mop-up/one-inning/late-callup rows with extreme small-sample values. This
  caused fWAR to read ~30% too high everywhere until caught during testing; `league_era`/
  `league_ra9` (computed from summed totals) were already correct, `league_fip_r9` wasn't.
- **`pb.statcast_catcher_framing()` is broken** — Baseball Savant retired the URL it scrapes
  (`/catcher_framing?...`) in favor of `/leaderboard/catcher-framing?...`, so pybaseball's own
  wrapper now gets back an HTML page instead of a CSV and fails to parse it.
  `pybaseball_client._get_catcher_framing` fetches the new URL directly instead (with
  `@pb_cache.df_cache()` reused from pybaseball for the same on-disk caching everything else
  gets) rather than going through pybaseball. If a future pybaseball release fixes the built-in
  function, this workaround can be dropped.
- **`pb.statcast_outs_above_average(season, pos)` is one call per position**, not one call for
  the whole league, and doesn't cover catchers at all (raises `ValueError` if you pass the
  catcher position) — `pybaseball_client._get_fielding_oaa` loops positions 3-9 (1B through RF)
  and sums `fielding_runs_prevented` per player across whichever ones they played, the same way
  bref sums `runs_field` across a multi-position player's stints.

## Tests

`tests/` covers the pure-pandas math modules (cwar, fwar, bwar, war_compare, team_ids,
park_factors, guts) plus `_records`/`_fix_mojibake`, using small synthetic DataFrames that
deliberately reproduce the bref quirks above (1-based index, string-typed numbers,
player-team-stint rows, `GB/FB`-is-actually-GB%). No network, runs in ~1s:

```bash
./venv/bin/python -m pytest tests/
```

When you touch a formula module, run these — and if you fix a math bug, add a regression test
the way `test_cwar.test_blend_weights_are_convex` pins the FIP/xERA weight-sum bug (the base
weights 0.70 + 0.25 only sum to 0.95; they must be normalized to a true convex combination or
every blendedERA deflates ~5% and all cWAR values inflate).

## Conventions

- No ORM/database — every request hits pybaseball (cache permitting) and returns fresh data.
- No auth — this is a local-only single-user app.
- After adding a dependency: `pip install <pkg> && pip freeze > requirements.txt`.

## Documenting your own changes

Update this file and the README in the same change that makes them true — don't wait to be
asked, and don't leave it for a follow-up. Concretely:

- New route or module → add it to the README's API route table and to the "Structure" list
  above.
- New non-obvious pybaseball/data-source behavior you had to work around → add it to
  "Data-source gotchas" above, in the same style as the existing entries (what's surprising,
  how it was confirmed, what to do instead).
- New metric or formula (like cWAR/fWAR) → give it its own README section explaining the
  formula, what it deliberately does/doesn't include, and why.
- A bug you found and fixed while building something else → worth a line in the relevant
  gotcha/README section if the next person could plausibly reintroduce it.

Treat a change as incomplete if it needs one of these updates and doesn't have it, the same way
you'd treat it as incomplete without tests in a repo that had them.
