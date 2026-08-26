import { useEffect, useMemo, useState } from 'react'
import './App.css'

const CURRENT_YEAR = new Date().getFullYear()
// Matches the backend's season validation (routers/stats.py); a partially
// typed year ("2", "20", "202") stays out of this range, so no fetch fires
// until the input holds a real season.
const MIN_SEASON = 1871
const MAX_SEASON = CURRENT_YEAR + 1

function isValidSeason(value) {
  return Number.isInteger(value) && value >= MIN_SEASON && value <= MAX_SEASON
}

// Delays propagating a value until the user stops typing, so numeric filter
// inputs that drive server-side fetches (Min BBE / Min PA) don't fire a
// request per keystroke ("100" fetching min=1, then min=10, then min=100).
function useDebouncedValue(value, delayMs = 400) {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), delayMs)
    return () => clearTimeout(t)
  }, [value, delayMs])
  return debounced
}

const TABS = [
  { key: 'standings', label: 'Standings' },
  { key: 'batting', label: 'Batting' },
  { key: 'pitching', label: 'Pitching' },
  { key: 'cwar', label: 'cWAR' },
  { key: 'fwar_batting', label: 'fWAR (Bat)' },
  { key: 'fwar_pitching', label: 'fWAR (Pit)' },
  { key: 'bwar_pitching', label: 'bWAR (Pit)' },
  { key: 'war_compare', label: 'WAR Compare' },
  { key: 'exitvelo', label: 'Exit Velo / Barrels' },
  { key: 'expected', label: 'Expected Stats' },
]

const SIDED_TABS = new Set(['exitvelo', 'expected'])

const ENDPOINTS = {
  standings: () => '/api/standings',
  batting: () => '/api/stats/batting',
  pitching: () => '/api/stats/pitching',
  cwar: () => '/api/stats/pitching/cwar',
  fwar_batting: () => '/api/stats/batting/fwar',
  fwar_pitching: () => '/api/stats/pitching/fwar',
  bwar_pitching: () => '/api/stats/pitching/bwar',
  war_compare: () => '/api/stats/pitching/war-compare',
  exitvelo: (side) => `/api/savant/${side}/exitvelo`,
  expected: (side) => `/api/savant/${side}/expected`,
}

const BATTING_COLS = ['Name', 'Tm', 'G', 'PA', 'AB', 'R', 'H', 'HR', 'RBI', 'SB', 'BA', 'OBP', 'SLG', 'OPS']
const PITCHING_COLS = ['Name', 'Tm', 'W', 'L', 'ERA', 'G', 'GS', 'SV', 'IP', 'SO', 'WHIP', 'SO9']
const CWAR_COLS = ['Name', 'Tm', 'IP', 'ERA', 'FIP', 'rFIP', 'xERA', 'GB%', 'FB%', 'PU%', 'PF', 'cWAR']
const FWAR_BATTING_COLS = ['Name', 'Tm', 'G', 'PA', 'wOBA', 'wRAA', 'BsR', 'Fld', 'Pos', 'fWAR']
const FWAR_PITCHING_COLS = ['Name', 'Tm', 'G', 'GS', 'IP', 'ERA', 'FIP', 'PF', 'fWAR']
const BWAR_PITCHING_COLS = ['Name', 'Tm', 'G', 'GS', 'IP', 'ERA', 'RA9', 'PF', 'bWAR']
const WAR_COMPARE_COLS = ['Name', 'Tm', 'IP', 'bWAR', 'fWAR', 'cWAR', 'WAR_spread']
const EXITVELO_COLS = [
  'last_name, first_name', 'attempts', 'avg_hit_speed', 'max_hit_speed',
  'ev95percent', 'barrels', 'brl_percent', 'brl_pa', 'avg_distance', 'max_distance',
]
const EXPECTED_COLS = {
  batting: ['last_name, first_name', 'pa', 'ba', 'est_ba', 'slg', 'est_slg', 'woba', 'est_woba'],
  pitching: ['last_name, first_name', 'pa', 'bip', 'ba', 'est_ba', 'slg', 'est_slg', 'woba', 'est_woba', 'era', 'xera'],
}

// Per-tab filtering rules: which column holds the player's name (for search),
// which holds the team (for the team dropdown, when the data has one), and
// which numeric column the "min" threshold applies to. `serverParam` means
// the threshold is sent to the backend (it controls the SQL-side qualifier
// pybaseball applies) instead of just hiding rows client-side.
const FILTER_CONFIG = {
  batting: { nameField: 'Name', teamField: 'TmID', minField: 'PA', minLabel: 'Min PA', minDefault: 0 },
  pitching: { nameField: 'Name', teamField: 'TmID', minField: 'IP', minLabel: 'Min IP', minDefault: 0 },
  cwar: { nameField: 'Name', teamField: 'TmID', minField: 'IP', minLabel: 'Min IP', minDefault: 0 },
  fwar_batting: { nameField: 'Name', teamField: 'TmID', minField: 'PA', minLabel: 'Min PA', minDefault: 0 },
  fwar_pitching: { nameField: 'Name', teamField: 'TmID', minField: 'IP', minLabel: 'Min IP', minDefault: 0 },
  bwar_pitching: { nameField: 'Name', teamField: 'TmID', minField: 'IP', minLabel: 'Min IP', minDefault: 0 },
  war_compare: { nameField: 'Name', teamField: 'TmID', minField: 'IP', minLabel: 'Min IP', minDefault: 0 },
  exitvelo: { nameField: 'last_name, first_name', teamField: null, minField: 'attempts', minLabel: 'Min BBE', minDefault: 50, serverParam: 'min_bbe' },
  expected: { nameField: 'last_name, first_name', teamField: null, minField: 'pa', minLabel: 'Min PA', minDefault: 50, serverParam: 'min_pa' },
}

function columnsFor(tab, side) {
  if (tab === 'batting') return BATTING_COLS
  if (tab === 'pitching') return PITCHING_COLS
  if (tab === 'cwar') return CWAR_COLS
  if (tab === 'fwar_batting') return FWAR_BATTING_COLS
  if (tab === 'fwar_pitching') return FWAR_PITCHING_COLS
  if (tab === 'bwar_pitching') return BWAR_PITCHING_COLS
  if (tab === 'war_compare') return WAR_COMPARE_COLS
  if (tab === 'exitvelo') return EXITVELO_COLS
  if (tab === 'expected') return EXPECTED_COLS[side]
  return []
}

function buildCacheKey(tab, side, season, minValue) {
  const cfg = FILTER_CONFIG[tab]
  let key = SIDED_TABS.has(tab) ? `${tab}:${side}` : tab
  key += `:${season}`
  if (cfg?.serverParam) key += `:${minValue}`
  return key
}

function buildUrl(tab, side, season, minValue) {
  const cfg = FILTER_CONFIG[tab]
  const params = new URLSearchParams({ season })
  if (cfg?.serverParam) params.set(cfg.serverParam, minValue)
  return `${ENDPOINTS[tab](side)}?${params.toString()}`
}

function compareValues(a, b) {
  const an = Number(a)
  const bn = Number(b)
  if (a !== null && b !== null && a !== '' && b !== '' && !Number.isNaN(an) && !Number.isNaN(bn)) {
    return an - bn
  }
  return String(a ?? '').localeCompare(String(b ?? ''))
}

function StandingsTable({ divisions }) {
  if (divisions === undefined) return <p>Loading standings...</p>
  if (!divisions.length) return <p>No standings data returned for this season.</p>
  return (
    <div className="standings-grid">
      {divisions.map((division, i) => (
        <div key={i} className="table-scroll">
          <table className="stats-table">
            <thead>
              <tr>
                {division[0] && Object.keys(division[0]).map((col) => <th key={col}>{col}</th>)}
              </tr>
            </thead>
            <tbody>
              {division.map((row, j) => (
                <tr key={j}>
                  {Object.values(row).map((val, k) => <td key={k}>{String(val)}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
    </div>
  )
}

// JSON drops trailing zeros (round(2.50, 2) serializes as 2.5), which makes
// WAR-style columns render raggedly next to each other -- fix the display
// width here rather than trying to force it through JSON serialization.
const COLUMN_DECIMALS = { fWAR: 1, cWAR: 2, bWAR: 1, WAR_spread: 2, wOBA: 3, wRAA: 1, BsR: 1, Fld: 1, Pos: 1, FIP: 2, rFIP: 2, RA9: 2, PF: 3 }

function formatCell(col, val) {
  if (val === null || val === undefined || val === '') return ''
  const decimals = COLUMN_DECIMALS[col]
  return decimals !== undefined && Number.isFinite(Number(val)) ? Number(val).toFixed(decimals) : String(val)
}

function StatsTable({ rows, columns, sortField, sortDir, onSort }) {
  if (!rows) return <p>Loading...</p>
  if (!rows.length) return <p>No players match the current filters.</p>
  return (
    <div className="table-scroll">
      <table className="stats-table">
        <thead>
          <tr>
            {columns.map((col) => (
              <th key={col} className="sortable" onClick={() => onSort(col)}>
                {col}
                {sortField === col && <span className="sort-arrow">{sortDir === 'asc' ? ' ▲' : ' ▼'}</span>}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i}>{columns.map((col) => <td key={col}>{formatCell(col, row[col])}</td>)}</tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function App() {
  const [tab, setTab] = useState('standings')
  const [side, setSide] = useState('batting')
  // The raw input string and the committed season are separate so a
  // half-typed year never becomes a fetch (see isValidSeason).
  const [seasonInput, setSeasonInput] = useState(String(CURRENT_YEAR))
  const [season, setSeason] = useState(CURRENT_YEAR)
  const [cache, setCache] = useState({})
  const [errors, setErrors] = useState({})

  const [search, setSearch] = useState('')
  const [teamFilter, setTeamFilter] = useState('ALL')
  const [minValue, setMinValue] = useState(0)
  const [sort, setSort] = useState({ field: null, dir: 'asc' })

  const cfg = FILTER_CONFIG[tab]

  // Reset filters when switching tabs, since columns/fields differ per tab.
  useEffect(() => {
    setSearch('')
    setTeamFilter('ALL')
    setSort({ field: null, dir: 'asc' })
    setMinValue(cfg?.minDefault ?? 0)
  }, [tab])

  const handleSeasonInput = (value) => {
    setSeasonInput(value)
    const parsed = Number(value)
    if (isValidSeason(parsed)) setSeason(parsed)
  }

  // Server-side min filters (Savant tabs) refetch on change, so debounce
  // them; client-side ones filter locally and can stay live.
  const debouncedMin = useDebouncedValue(minValue)
  const fetchMin = cfg?.serverParam ? debouncedMin : minValue

  const cacheKey = buildCacheKey(tab, side, season, fetchMin)

  useEffect(() => {
    if (cache[cacheKey] !== undefined || errors[cacheKey] !== undefined) return
    fetch(buildUrl(tab, side, season, fetchMin))
      .then(async (r) => {
        if (!r.ok) {
          let detail = `HTTP ${r.status}`
          try {
            const body = await r.json()
            if (body?.detail) detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
          } catch { /* non-JSON error body; keep the status line */ }
          throw new Error(detail)
        }
        return r.json()
      })
      .then((data) => setCache((prev) => ({ ...prev, [cacheKey]: data })))
      .catch((e) => setErrors((prev) => ({ ...prev, [cacheKey]: String(e.message ?? e) })))
  }, [cacheKey, tab, side, season, fetchMin])

  const data = cache[cacheKey]
  const error = errors[cacheKey]
  // Clearing a key's error re-arms the fetch effect above for that key.
  const retry = () => setErrors((prev) => {
    const next = { ...prev }
    delete next[cacheKey]
    return next
  })

  // Players traded mid-season get one bref row with a combined "Arizona,Cincinnati"
  // style team string, so team options/filtering need to split on comma rather
  // than treat that string as a single team.
  const teamOptions = useMemo(() => {
    if (!data || !cfg?.teamField) return []
    const teams = data.flatMap((r) => String(r[cfg.teamField] ?? '').split(',').map((t) => t.trim()))
    return [...new Set(teams.filter(Boolean))].sort()
  }, [data, cfg])

  const displayRows = useMemo(() => {
    if (!data || tab === 'standings') return data
    let rows = data
    if (search.trim()) {
      const q = search.trim().toLowerCase()
      rows = rows.filter((r) => String(r[cfg.nameField] ?? '').toLowerCase().includes(q))
    }
    if (cfg.teamField && teamFilter !== 'ALL') {
      rows = rows.filter((r) => String(r[cfg.teamField] ?? '').split(',').map((t) => t.trim()).includes(teamFilter))
    }
    if (!cfg.serverParam && minValue) {
      rows = rows.filter((r) => Number(r[cfg.minField]) >= Number(minValue))
    }
    if (sort.field) {
      rows = [...rows].sort((a, b) => {
        const cmp = compareValues(a[sort.field], b[sort.field])
        return sort.dir === 'asc' ? cmp : -cmp
      })
    }
    return rows
  }, [data, tab, cfg, search, teamFilter, minValue, sort])

  const handleSort = (field) => {
    setSort((prev) => {
      if (prev.field === field) {
        return { field, dir: prev.dir === 'asc' ? 'desc' : 'asc' }
      }
      // First click on a numeric column sorts descending (leaders first);
      // text columns (names, teams) still start ascending.
      const sample = (Array.isArray(data) ? data : []).find(
        (r) => r[field] !== null && r[field] !== undefined && r[field] !== ''
      )?.[field]
      const numeric = sample !== undefined && !Number.isNaN(Number(sample))
      return { field, dir: numeric ? 'desc' : 'asc' }
    })
  }

  return (
    <div className="app">
      <h1>⚾ Baseball Stats</h1>
      <div className="controls">
        <label>
          Season:{' '}
          <input
            type="number"
            min={MIN_SEASON}
            max={MAX_SEASON}
            value={seasonInput}
            onChange={(e) => handleSeasonInput(e.target.value)}
          />
        </label>
        <nav className="tabs">
          {TABS.map((t) => (
            <button key={t.key} className={tab === t.key ? 'active' : ''} onClick={() => setTab(t.key)}>
              {t.label}
            </button>
          ))}
        </nav>
        {SIDED_TABS.has(tab) && (
          <nav className="tabs">
            {['batting', 'pitching'].map((s) => (
              <button key={s} className={side === s ? 'active' : ''} onClick={() => setSide(s)}>
                {s}
              </button>
            ))}
          </nav>
        )}
      </div>

      {tab !== 'standings' && (
        <div className="filters">
          <input
            type="text"
            placeholder="Search player..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          {cfg.teamField && (
            <select value={teamFilter} onChange={(e) => setTeamFilter(e.target.value)}>
              <option value="ALL">All teams</option>
              {teamOptions.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
          )}
          <label>
            {cfg.minLabel}:{' '}
            <input
              type="number"
              value={minValue}
              onChange={(e) => setMinValue(Number(e.target.value))}
            />
          </label>
          {displayRows && <span className="result-count">{displayRows.length} players</span>}
        </div>
      )}

      {error ? (
        <p className="error">
          Error: {error} <button onClick={retry}>Retry</button>
        </p>
      ) : tab === 'standings' ? (
        <StandingsTable divisions={data} />
      ) : (
        <StatsTable
          rows={displayRows}
          columns={columnsFor(tab, side)}
          sortField={sort.field}
          sortDir={sort.dir}
          onSort={handleSort}
        />
      )}
    </div>
  )
}

export default App
