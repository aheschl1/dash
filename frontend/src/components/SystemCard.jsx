function pctColor(pct) {
  if (pct > 85) return 'var(--red)'
  if (pct > 60) return 'var(--yellow)'
  return 'var(--green)'
}

function barCls(pct) {
  if (pct > 85) return 'bar-high'
  if (pct > 60) return 'bar-mid'
  return 'bar-low'
}

export default function SystemCard({ data }) {
  return (
    <div className="card">
      <div className="card-title">System{data ? ` — up ${data.uptime}` : ''}</div>
      {!data ? (
        <div className="loading">Loading…</div>
      ) : (
        <div className="stat-items">
          <div className="stat-item">
            <div className="stat-label">CPU</div>
            <div className="stat-big" style={{ color: pctColor(data.cpu_pct) }}>{data.cpu_pct.toFixed(1)}%</div>
            <div className="bar-wrap"><div className={`bar-fill ${barCls(data.cpu_pct)}`} style={{ width: `${data.cpu_pct}%` }} /></div>
          </div>
          <div className="stat-item">
            <div className="stat-label">RAM</div>
            <div className="stat-big" style={{ color: pctColor(data.ram_pct) }}>{data.ram_pct.toFixed(0)}%</div>
            <div className="bar-wrap"><div className={`bar-fill ${barCls(data.ram_pct)}`} style={{ width: `${data.ram_pct}%` }} /></div>
            <div className="stat-sub">{data.ram_used_gb} / {data.ram_total_gb} GB</div>
          </div>
          <div className="stat-item">
            <div className="stat-label">Disk</div>
            <div className="stat-big" style={{ color: pctColor(data.disk_pct) }}>{data.disk_pct.toFixed(0)}%</div>
            <div className="bar-wrap"><div className={`bar-fill ${barCls(data.disk_pct)}`} style={{ width: `${data.disk_pct}%` }} /></div>
            <div className="stat-sub">{data.disk_used_gb} / {data.disk_total_gb} GB</div>
          </div>
        </div>
      )}
    </div>
  )
}
