function pctColor(pct) {
  if (pct > 85) return 'var(--red)'
  if (pct > 60) return 'var(--yellow)'
  return 'var(--green)'
}

export default function ProcessesCard({ data }) {
  return (
    <div className="card">
      <div className="card-title">Top Processes</div>
      {!data ? (
        <div className="loading">Loading…</div>
      ) : data.length === 0 ? (
        <div className="empty">No process data</div>
      ) : (
        <div className="table-wrap">
          <table className="compact-table">
            <thead>
              <tr>
                <th>Name</th>
                <th>PID</th>
                <th>CPU%</th>
                <th>MEM%</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {data.map((p) => (
                <tr key={p.pid}>
                  <td className="td-name">{p.name}</td>
                  <td style={{ fontFamily: 'monospace', color: 'var(--text-muted)' }}>{p.pid}</td>
                  <td style={{ fontFamily: 'monospace', fontWeight: 600, color: pctColor(p.cpu_pct) }}>{p.cpu_pct.toFixed(1)}%</td>
                  <td style={{ fontFamily: 'monospace', color: 'var(--text-muted)' }}>{p.mem_pct.toFixed(2)}%</td>
                  <td style={{ color: 'var(--text-muted)', fontSize: '11px' }}>{p.status}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
