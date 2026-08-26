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

**Backend tests** (pure-pandas math modules, no network):

```bash
cd backend
./venv/bin/python -m pytest tests/
```

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
| `GET /api/stats/batting/fwar?season=` | Baseball-Reference | fWAR-methodology batter WAR (see below) |
| `GET /api/stats/pitching/fwar?season=` | Baseball-Reference | fWAR-methodology pitcher WAR (see below) |
| `GET /api/stats/pitching/bwar?season=` | Baseball-Reference | bWAR-methodology (RA9-based) pitcher WAR (see below) |
| `GET /api/stats/pitching/war-compare?season=` | Baseball-Reference + Baseball Savant | bWAR/fWAR/cWAR merged for comparison |
| `GET /api/savant/batting/exitvelo?season=&min_bbe=` | Baseball Savant | exit velo / barrel rate |
| `GET /api/savant/pitching/exitvelo?season=&min_bbe=` | Baseball Savant | exit velo / barrel rate allowed |
| `GET /api/savant/batting/expected?season=&min_pa=` | Baseball Savant | xBA / xSLG / xwOBA |
| `GET /api/savant/pitching/expected?season=&min_pa=` | Baseball Savant | xBA / xSLG / xwOBA / xERA allowed |
| `GET /api/players/search?last=&first=` | Baseball-Reference | player ID lookup |
| `GET /api/statcast/recent?days_back=` | Baseball Savant | raw pitch-level Statcast rows |

## Frontend features

- Tabs for Standings, Batting, Pitching, cWAR, fWAR (Bat), fWAR (Pit), bWAR (Pit), WAR Compare,
  Exit Velo/Barrels, Expected Stats (the last two toggle between batting and pitching leaders).
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

1. **rFIP core** (~74% weight) — the standard `(13·HR + 3·(BB+HBP) − 2·K)/IP` formula, using a
   FIP constant computed live from that season's league totals rather than a hardcoded one,
   but with the HR term **regressed toward league HR/FB** first (see below).
2. **Statcast xERA** (~26% weight, from `/api/savant/pitching/expected`) — captures contact
   quality (exit velocity + launch angle allowed) that FIP ignores entirely. This weight
   shrinks toward the rFIP core for pitchers with few tracked batted-ball events, since
   contact-quality metrics need a bigger sample to stabilize than K/BB do.
3. **A small batted-ball-mix adjustment** — rewards ground-ball/pop-up tendencies (derived
   from bref's `GB/FB`, `LD`, `PU` columns), which are far more repeatable than raw BABIP and
   otherwise invisible to FIP. It applies **only in proportion to how little xERA is trusted**
   for that pitcher (see below).

The rFIP/xERA weights are normalized to a true convex combination (they always sum to exactly
1, preserving the ~70:25 ratio) — blendedERA is an ERA-scale estimate, so a weight sum below 1
would deflate it outright rather than reweight it.

### rFIP: regressing the noisiest FIP input

HR/FB is by far the least repeatable of FIP's three inputs — the entire reason xFIP exists —
so a pitcher's HR total is regressed toward what his own fly-ball count would produce at the
league HR/FB rate. Fly balls come from `FB% × (BF − SO − BB − HBP − HR)`; full trust needs
`FB_FULL_TRUST = 400` fly balls, so a 180-IP starter (~200 FB) keeps about half his own HR
total and borrows the rest from league rate. Unlike xFIP, which discards the actual HR total
outright, this keeps real signal for pitchers with the sample to back it. The regression is
league-total-preserving, so it only moves home runs *between* pitchers — league rFIP still
lands on league ERA. Both `FIP` and `rFIP` are returned so you can see the gap; in 2026 the
biggest movers are ~±0.9 runs (e.g. Jameson Taillon 6.55 → 5.30).

### Park adjustment applies to rFIP only

The blended rate is converted to WAR on the **same chassis fWAR and bWAR use**
(`backend/app/pitcher_war_chassis.py`): scaled to an RA9 basis and run through the dynamic
runs-per-win and starter/reliever replacement-level split. That shared chassis matters —
before it, cWAR's flat `1.13× league average` replacement level was roughly a *reliever's*
replacement gap applied to everyone, shortchanging starters ~half their replacement credit and
leaving league-total cWAR ~35% below the other two metrics; and with no park adjustment at all,
every Coors pitcher graded out terribly.

cWAR differs from fWAR/bWAR in *where* the park adjustment lands: only the rFIP component is
divided by the park factor, not the whole blend. rFIP is built from actual outcomes (home runs
that Coors' altitude helped carry out), so it's park-inflated; xERA is derived from exit
velocity and launch angle — batted-ball *inputs* the park barely touches — so dividing it too
would hand extreme-park pitchers a second credit for the same effect. cWAR therefore hands the
chassis an already-park-adjusted rate with a neutral PF, while fWAR/bWAR correctly let the
chassis divide their fully outcome-based rates. Net effect on WAR Compare: the spread between
the three metrics now reflects only rate-stat philosophy (RA9 vs FIP vs the blend), never a
mismatched replacement level or park convention.

### Why the batted-ball adjustment fades as xERA takes over

xERA is built from exit velocity **and launch angle**, so it already encodes batted-ball mix.
Applying the mix adjustment on top of a full-strength xERA double-counts, and the data says so:
regressing next-season park-adjusted ERA over the 2021–2025 season pairs, the unconditional
adjustment was *worse* than none on 4 of 4 folds, monotonically (weighted RMSE 1.0359 none /
1.0424 half / 1.0519 full). The adjustment is therefore scaled by `(1 − bip_reliability)`, so
it fades out exactly as xERA takes over and survives only where xERA is absent or weakly
trusted — pitchers with little tracked batted-ball data, whom that test could not cover.

### Tuning the weights

`backend/scripts/tune_cwar_weights.py` fits the blend weights empirically — regressing
next-season park-adjusted ERA on that season's rFIP and xERA, with leave-one-season-pair-out
validation. Run it by hand (it's network-bound and read-only; first run is slow, later runs hit
pybaseball's disk cache):

```bash
cd backend
./venv/bin/python -m scripts.tune_cwar_weights
```

It prints fitted weights, per-fold RMSE/MAE against the current weights and FIP-only /
xERA-only / 50-50 baselines, the batted-ball diagnostics above, and a paste-ready constants
block **only if** the refit beats the current weights consistently across folds.

As of the 2021–2025 pairs it does not: the empirical refit (0.66/0.34) lost on 0/4 folds and
swung across folds (0.53–0.78), so the hand-picked 70/25 pair stands — now as a *validated*
choice rather than an untested one. The batted-ball coefficients are deliberately not co-fit
(xERA already carries that signal, and GB/FB/PU are structurally collinear); they're reported
as a residual diagnostic instead.

One data quirk worth knowing if you touch this code: pybaseball labels bref's ground-ball-rate
column `GB/FB`, but it's actually GB% (a rate, 0-1), not a ratio — confirmed by checking known
extreme groundball pitchers (e.g. submarine sinkerballer Tyler Rogers) against the values. Treat
it as GB%, not `GB/(FB)`.

## fWAR: FanGraphs-methodology WAR, for batters and pitchers

Unlike cWAR (a deliberately different metric, above), `fWAR` follows FanGraphs' own published
WAR formulas (`backend/app/fwar_batting.py`, `backend/app/fwar_pitching.py`):

- **Batting**: wOBA is built from Baseball-Reference counting stats and that season's Guts
  linear weights (`backend/app/guts.py`), then converted to wRAA, adjusted for park and league,
  and combined with baserunning, fielding, and positional value plus a replacement-level bump
  — the same shape as [FanGraphs' position-player WAR](https://library.fangraphs.com/war/war-position-players/).
- **Pitching**: FIP (using a real per-season FIP constant, not a self-derived one) is scaled to
  a runs-allowed basis, park-adjusted, and converted to wins using FanGraphs' dynamic
  runs-per-win and starter/reliever-specific replacement level — following
  [FanGraphs' pitcher WAR formula](https://library.fangraphs.com/war/calculating-war-pitchers/).

**Where the numbers come from, and why this is an approximation, not a bit-exact replica:**

- The wOBA linear weights, FIP constant, and runs-per-win in `guts.py` are hand-transcribed
  from FanGraphs' [Guts! page](https://www.fangraphs.com/guts.aspx?type=cn) — pybaseball has no
  API for these, so they need updating there once FanGraphs publishes a new season's numbers.
- Park factors and positional value come from Baseball-Reference's daily WAR files
  (`pb.bwar_bat` / `pb.bwar_pitch`) rather than FanGraphs' own park factors or per-1350-innings
  positional table — those aren't available through pybaseball, but bref publishes its own
  version of each, updated daily in-season, which this app reuses as a close public stand-in.
- **Fielding value** uses real Statcast metrics, not bref's: Outs Above Average
  (`fielding_runs_prevented`, from Baseball Savant's OAA leaderboard, summed across every
  position a player fielded) for everyone except catchers, and catcher framing runs (`rv_tot`,
  from Baseball Savant's [catcher framing leaderboard](https://baseballsavant.mlb.com/leaderboard/catcher-framing))
  for catchers — Statcast doesn't compute OAA for the catcher position. Neither is FanGraphs'
  own UZR (a proprietary Sports Info Solutions metric with no free source anywhere), but both
  are real, modern, radar/tracking-based public metrics, arguably a better fielding signal than
  bref's own defensive-runs estimate. Anyone Statcast doesn't cover (e.g. a pitcher's rare plate
  appearance) falls back to bref's `runs_field`.
- **Baserunning value (BsR)** is wSB, computed here from FanGraphs' actual public formula (SB/CS
  times this season's Guts run values, above a league-average baserunner's expected value with
  the same opportunities), plus bref's `runs_dp` (double-play avoidance, a distinct component).
  FanGraphs' own BsR also includes UBR — credit for taking extra bases, tagging up, etc. — which
  needs proprietary video-review data with no public source; that piece is omitted rather than
  approximated, since bref's all-in-one baserunning number bundles SB value together with UBR in
  a way that can't be cleanly separated, and reusing it alongside our own wSB would double-count
  the stolen-base portion.
- Two further simplifications on the pitching side: standard FIP is used instead of FanGraphs'
  "ifFIP" (folds in infield fly balls, which bref only exposes as a rate, not a raw count), and
  the reliever leverage-index regression and final league-wide calibration correction are both
  omitted (game leverage index isn't available via pybaseball, and the correction doesn't
  change player-to-player rankings).

In practice these land close to FanGraphs' own published fWAR for a given season, but expect
small differences rather than an exact match.

## bWAR: Baseball-Reference-methodology WAR, for pitchers

`bWAR` (`backend/app/bwar_pitching.py`) follows Baseball-Reference's own published pitcher WAR
formula in spirit (https://www.baseball-reference.com/about/war_explained_pitch.shtml). The
key difference from `fWAR` above: bWAR rates a pitcher on his own actual runs allowed (RA9),
not an FIP estimate. Baseball-Reference deliberately credits/blames a pitcher for everything
that happened while he was in the game -- including BABIP, sequencing, and defense -- rather
than isolating FIP's three "true outcomes" (K, BB, HR). Structurally it reuses the same shape
as `fWAR`'s pitching formula (park adjustment, dynamic runs-per-win, starter/reliever
replacement-level split), just with RA9 in place of FIP-on-a-runs-allowed-basis:

1. **RA9** — `9 · R / IP`, the pitcher's own actual runs (not earned runs) allowed per 9 innings.
2. **Park adjustment** — RA9 is divided by the same Baseball-Reference-derived park factor
   (`park_factors.py`) fWAR uses, then compared against the league's flat average RA9.
3. **Dynamic runs-per-win and replacement level** — identical formulas to fWAR's pitching side:
   a pitcher's own innings/game shift how many runs one win is worth for him, and the
   starter/reliever replacement-level split reuses FanGraphs' published win-percentage gap
   (`.12`/`.03` WPG), since a bref-specific equivalent isn't published anywhere pybaseball
   exposes.

**Disclosed simplifications:** bref's real formula also adjusts for the strength of the batters
a pitcher actually faced and the quality of the defense playing behind him (`RA9opp`, `RA9def`,
`RA9role`) — neither is available via pybaseball, so this compares park-adjusted RA9 against a
flat league average instead of an opponent/defense-adjusted one. As with fWAR, expect this to
land close to (not bit-exact with) Baseball-Reference's own published bWAR.

Note: `pb.bwar_pitch()` already exposes bref's own precomputed `WAR` column directly — this app
deliberately recomputes the metric from raw components instead of passing that through, the
same choice fWAR makes relative to FanGraphs' own numbers, so the formula is inspectable and
consistent with how the other two metrics are built.

## WAR Compare: bWAR vs. fWAR vs. cWAR side by side

The "WAR Compare" tab (`GET /api/stats/pitching/war-compare`, `backend/app/war_compare.py`)
merges all three pitcher WAR metrics onto one row per pitcher — Name, Tm, IP, `bWAR`, `fWAR`,
`cWAR`, plus a `WAR_spread` column (the gap between the highest and lowest of the three) to
make players the methodologies disagree on easy to spot. It's a pure merge of each metric's
already-computed output — no new math, and none of `bwar_pitching.py`/`fwar_pitching.py`/
`cwar.py` themselves are touched by it.

## Why not FanGraphs directly?

`pybaseball.batting_stats` / `pitching_stats` scrape FanGraphs' leaderboard pages, which are
now behind Cloudflare bot protection — plain scraping gets a 403, and the harder version of
the challenge is an interactive CAPTCHA that isn't worth automating around. This app uses
Baseball-Reference (`batting_stats_bref` / `pitching_stats_bref`) for season leaderboards
instead, which pybaseball can still reach directly. That's also why `fWAR` above is a
same-methodology reproduction built from bref + Guts constants rather than FanGraphs' own
published number — there's no way to pull their number directly.

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
