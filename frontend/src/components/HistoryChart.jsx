import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
} from 'recharts'

const WINDOWS = [
  { label: '5m', minutes: 5 },
  { label: '10m', minutes: 10 },
  { label: '30m', minutes: 30 },
  { label: '1h', minutes: 60 },
  { label: '6h', minutes: 360 },
  { label: '24h', minutes: 1440 },
]

function fmt(ts) {
  const d = new Date(ts * 1000)
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

export default function HistoryChart({ data, window: win, onWindowChange }) {
  const current = WINDOWS.find((w) => w.minutes === win) || WINDOWS[WINDOWS.length - 1]
  const points = data
    ? data.map((r) => ({
        time: fmt(r.ts),
        'CPU %': r.cpu_pct != null ? +r.cpu_pct.toFixed(1) : null,
        'RAM %': r.ram_pct != null ? +r.ram_pct.toFixed(1) : null,
        'GPU Util %': r.gpu_util != null ? +r.gpu_util.toFixed(1) : null,
        'GPU Temp °C': r.gpu_temp != null ? +r.gpu_temp.toFixed(1) : null,
      }))
    : []

  return (
    <div className="card">
      <div className="chart-head">
        <div className="card-title" style={{ marginBottom: 0 }}>History — last {current.label}</div>
        <div className="line-selector">
          {WINDOWS.map((w) => (
            <button
              key={w.minutes}
              className={`line-btn${w.minutes === current.minutes ? ' active' : ''}`}
              onClick={() => onWindowChange(w.minutes)}
            >
              {w.label}
            </button>
          ))}
        </div>
      </div>
      {points.length < 2 ? (
        <div className="loading" style={{ padding: '32px 0', textAlign: 'center' }}>
          Collecting data… check back in a minute.
        </div>
      ) : (
        <ResponsiveContainer width="100%" height={220}>
          <LineChart data={points} margin={{ top: 4, right: 16, bottom: 0, left: -16 }}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis dataKey="time" tick={{ fontSize: 10 }} interval="preserveStartEnd" />
            <YAxis domain={[0, 100]} tick={{ fontSize: 10 }} />
            <Tooltip
              contentStyle={{ background: '#1c2128', border: '1px solid #30363d', borderRadius: 6, fontSize: 12 }}
              labelStyle={{ color: '#8b949e' }}
            />
            <Legend wrapperStyle={{ fontSize: 12, color: '#8b949e' }} />
            <Line type="monotone" dataKey="CPU %" stroke="#58a6ff" dot={false} strokeWidth={1.5} connectNulls />
            <Line type="monotone" dataKey="RAM %" stroke="#3fb950" dot={false} strokeWidth={1.5} connectNulls />
            <Line type="monotone" dataKey="GPU Util %" stroke="#d29922" dot={false} strokeWidth={1.5} connectNulls />
            <Line type="monotone" dataKey="GPU Temp °C" stroke="#f85149" dot={false} strokeWidth={1.5} connectNulls />
          </LineChart>
        </ResponsiveContainer>
      )}
    </div>
  )
}
