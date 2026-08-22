import { useEffect, useState } from 'react'
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

function columnsFor(tab, side) {
  if (tab === 'batting') return BATTING_COLS
  if (tab === 'pitching') return PITCHING_COLS
  if (tab === 'exitvelo') return EXITVELO_COLS
  if (tab === 'expected') return EXPECTED_COLS[side]
  return []
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

function StatsTable({ rows, columns }) {
  if (!rows) return <p>Loading...</p>
  if (!rows.length) return <p>No qualifying players found.</p>
  return (
    <div className="table-scroll">
      <table className="stats-table">
        <thead>
          <tr>{columns.map((col) => <th key={col}>{col}</th>)}</tr>
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

  const cacheKey = `${tab}:${side}:${season}`

  useEffect(() => {
    if (cache[cacheKey] !== undefined) return
    setError(null)
    const endpoint = ENDPOINTS[tab](side)
    fetch(`${endpoint}?season=${season}`)
      .then((r) => r.json())
      .then((data) => setCache((prev) => ({ ...prev, [cacheKey]: data })))
      .catch((e) => setError(String(e)))
  }, [cacheKey, tab, side, season])

  const data = cache[cacheKey]

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

      {error && <p className="error">Error: {error}</p>}

      {tab === 'standings' ? (
        <StandingsTable divisions={data} />
      ) : (
        <StatsTable rows={data} columns={columnsFor(tab, side)} />
      )}
    </div>
  )
}

export default App
