# frontend/

React + Vite, plain JS (no TypeScript). Single-page dashboard, currently all in one component.

```bash
npm run dev   # http://localhost:5173, proxies /api/* to http://localhost:8000 (see vite.config.js)
```

The backend must be running separately — this dev server only proxies API calls, it doesn't
start the backend for you.

## Structure

Everything lives in `src/App.jsx` — there's no router, no component library, no state
management beyond `useState`/`useMemo`. Keep it that way until it's actually painful; splitting
into multiple files/components prematurely would just add indirection for a single-page app.

The tab system is table-driven, not a switch statement. To add a new tab you touch four things
in `App.jsx`, all near the top of the file:

1. `TABS` — label + key shown in the nav.
2. `ENDPOINTS` — maps the tab key to a function returning its API path.
3. `columnsFor()` (and the `*_COLS` constants it reads from) — which fields to render, in order.
4. `FILTER_CONFIG` — only needed if the tab should support search/team-filter/min-value
   filtering (see below). Skip it (like `standings`) if the tab's shape doesn't fit a flat
   list of player rows.

If the new tab has a batting/pitching split (like Exit Velo / Expected Stats), add its key to
`SIDED_TABS` too — that's what makes the batting/pitching toggle row appear.

The `cwar` tab is a good reference for a plain (non-sided) filtered tab backed by a computed
backend field rather than a raw pybaseball passthrough: it hits `/api/stats/pitching/cwar`
(see `backend/app/cwar.py` for the actual metric) and otherwise reuses the same
`FILTER_CONFIG`/`columnsFor` pattern as `pitching`, just with its own `CWAR_COLS`.

## Filtering/search/sort

`FILTER_CONFIG[tab]` describes, per tab: which field holds the player's name (`nameField`,
used by the search box), which field holds the team (`teamField`, used by the team dropdown —
omit for data that has no team column, like the Savant leaderboards), and which numeric field
the "min" input applies to (`minField`/`minLabel`/`minDefault`).

Two different filtering strategies live behind that same config:

- **Client-side** (no `serverParam`): the full dataset is already fetched, and the min-value
  filter just hides rows locally. This is what Batting/Pitching use — bref doesn't take a
  qualifier param, so there's nothing to send the server.
- **Server-side** (`serverParam: 'min_bbe'` etc.): the value is sent as an actual query param
  and becomes part of the cache key (see `buildCacheKey`), because it changes what the backend
  returns, not just what's displayed. This is what the Savant tabs use, since pybaseball's
  leaderboard functions accept `minBBE`/`minPA` server-side.

Get this wrong (e.g. adding a `serverParam` field the backend route doesn't accept, or leaving
it off when the backend does support it) and filtering will silently do nothing or hit the
wrong cached response — check `backend/app/routers/stats.py` for what each route actually
accepts before wiring up a new filter.

One non-obvious data quirk the team filter already handles: Baseball-Reference gives players
traded mid-season a single row with a comma-joined team string (e.g. `"Arizona,Seattle"`).
Team options and the team filter both split on comma — if you add another team-scoped filter,
reuse that pattern rather than comparing the raw field for equality.

## Styling

Plain CSS in `App.css`, dark theme, no CSS variables/framework — colors are hardcoded
(`#242424` background, `#646cff` accent, `#444` borders). Match those values for new UI rather
than introducing a new palette.

## No tests currently

Verify changes by running both dev servers and checking in a browser — there's no test suite
or CI to lean on yet.

## Documenting your own changes

Update this file and the README in the same change that makes them true — don't wait to be
asked. New tab → add it to the README's tab list. New filtering strategy, styling convention,
or non-obvious quirk (like the traded-player comma-joined team string) → add it here, in the
same style as the existing entries. Treat a change as incomplete if it needs one of these
updates and doesn't have it.
