import { useEffect, useState } from 'react'
import './App.css'

const CURRENT_YEAR = new Date().getFullYear()

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
  const [season, setSeason] = useState(CURRENT_YEAR)
  const [standings, setStandings] = useState(null)
  const [batting, setBatting] = useState(null)
  const [pitching, setPitching] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    setError(null)
    if (tab === 'standings' && standings === null) {
      fetch(`/api/standings?season=${season}`)
        .then((r) => r.json())
        .then(setStandings)
        .catch((e) => setError(String(e)))
    }
    if (tab === 'batting' && batting === null) {
      fetch(`/api/stats/batting?season=${season}`)
        .then((r) => r.json())
        .then(setBatting)
        .catch((e) => setError(String(e)))
    }
    if (tab === 'pitching' && pitching === null) {
      fetch(`/api/stats/pitching?season=${season}`)
        .then((r) => r.json())
        .then(setPitching)
        .catch((e) => setError(String(e)))
    }
  }, [tab, season])

  const battingCols = ['Name', 'Tm', 'G', 'PA', 'AB', 'R', 'H', 'HR', 'RBI', 'SB', 'BA', 'OBP', 'SLG', 'OPS']
  const pitchingCols = ['Name', 'Tm', 'W', 'L', 'ERA', 'G', 'GS', 'SV', 'IP', 'SO', 'WHIP', 'SO9']

  return (
    <div className="app">
      <h1>⚾ Baseball Stats</h1>
      <div className="controls">
        <label>
          Season:{' '}
          <input
            type="number"
            value={season}
            onChange={(e) => {
              setSeason(Number(e.target.value))
              setStandings(null)
              setBatting(null)
              setPitching(null)
            }}
          />
        </label>
        <nav className="tabs">
          {['standings', 'batting', 'pitching'].map((t) => (
            <button key={t} className={tab === t ? 'active' : ''} onClick={() => setTab(t)}>
              {t}
            </button>
          ))}
        </nav>
      </div>

      {error && <p className="error">Error: {error}</p>}

      {tab === 'standings' && <StandingsTable divisions={standings} />}
      {tab === 'batting' && <StatsTable rows={batting} columns={battingCols} />}
      {tab === 'pitching' && <StatsTable rows={pitching} columns={pitchingCols} />}
    </div>
  )
}

export default App
