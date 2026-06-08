function tempColor(val) {
  if (val > 70) return 'var(--red)'
  if (val > 55) return 'var(--yellow)'
  return 'var(--green)'
}

function label(key) {
  return key
    .replace('cpu_', 'CPU ')
    .replace('nvme_', 'NVMe ')
    .replace('_', ' ')
}

export default function TempsCard({ data }) {
  const entries = data ? Object.entries(data) : []

  return (
    <div className="card">
      <div className="card-title">Temperatures</div>
      {!data || entries.length === 0 ? (
        <div className="loading">No sensor data</div>
      ) : (
        <div className="stat-items" style={{ flexWrap: 'wrap', gap: '10px' }}>
          {entries.map(([k, v]) => (
            <div className="stat-item" key={k} style={{ minWidth: '60px' }}>
              <div className="stat-label">{label(k)}</div>
              <div className="stat-big" style={{ fontSize: '24px', color: tempColor(v) }}>{v}°C</div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
