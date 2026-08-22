import { useEffect, useMemo, useState } from 'react'
import './App.css'

const CURRENT_YEAR = new Date().getFullYear()

const TABS = [
  { key: 'standings', label: 'Standings' },
  { key: 'batting', label: 'Batting' },
  { key: 'pitching', label: 'Pitching' },
  { key: 'exitvelo', label: 'Exit Velo / Barrels' },
  { key: 'expected', label: 'Expected Stats' },
]

const SIDED_TABS = new Set(['exitvelo', 'expected'])

const ENDPOINTS = {
  standings: () => '/api/standings',
  batting: () => '/api/stats/batting',
  pitching: () => '/api/stats/pitching',
  exitvelo: (side) => `/api/savant/${side}/exitvelo`,
  expected: (side) => `/api/savant/${side}/expected`,
}

const BATTING_COLS = ['Name', 'Tm', 'G', 'PA', 'AB', 'R', 'H', 'HR', 'RBI', 'SB', 'BA', 'OBP', 'SLG', 'OPS']
const PITCHING_COLS = ['Name', 'Tm', 'W', 'L', 'ERA', 'G', 'GS', 'SV', 'IP', 'SO', 'WHIP', 'SO9']
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
  batting: { nameField: 'Name', teamField: 'Tm', minField: 'PA', minLabel: 'Min PA', minDefault: 0 },
  pitching: { nameField: 'Name', teamField: 'Tm', minField: 'IP', minLabel: 'Min IP', minDefault: 0 },
  exitvelo: { nameField: 'last_name, first_name', teamField: null, minField: 'attempts', minLabel: 'Min BBE', minDefault: 50, serverParam: 'min_bbe' },
  expected: { nameField: 'last_name, first_name', teamField: null, minField: 'pa', minLabel: 'Min PA', minDefault: 50, serverParam: 'min_pa' },
}

function columnsFor(tab, side) {
  if (tab === 'batting') return BATTING_COLS
  if (tab === 'pitching') return PITCHING_COLS
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
  if (!divisions?.length) return <p>Loading standings...</p>
  return (
    <div className="standings-grid">
      {divisions.map((division, i) => (
        <table key={i} className="stats-table">
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
      ))}
    </div>
  )
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
            <tr key={i}>{columns.map((col) => <td key={col}>{String(row[col] ?? '')}</td>)}</tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function App() {
  const [tab, setTab] = useState('standings')
  const [side, setSide] = useState('batting')
  const [season, setSeason] = useState(CURRENT_YEAR)
  const [cache, setCache] = useState({})
  const [error, setError] = useState(null)

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

  const cacheKey = buildCacheKey(tab, side, season, minValue)

  useEffect(() => {
    if (cache[cacheKey] !== undefined) return
    setError(null)
    fetch(buildUrl(tab, side, season, minValue))
      .then((r) => r.json())
      .then((data) => setCache((prev) => ({ ...prev, [cacheKey]: data })))
      .catch((e) => setError(String(e)))
  }, [cacheKey, tab, side, season, minValue])

  const data = cache[cacheKey]

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
    setSort((prev) => ({
      field,
      dir: prev.field === field && prev.dir === 'asc' ? 'desc' : 'asc',
    }))
  }

  return (
    <div className="app">
      <h1>⚾ Baseball Stats</h1>
      <div className="controls">
        <label>
          Season:{' '}
          <input
            type="number"
            value={season}
            onChange={(e) => setSeason(Number(e.target.value))}
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

      {error && <p className="error">Error: {error}</p>}

      {tab === 'standings' ? (
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
