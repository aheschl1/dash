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

function tempColor(c) {
  if (c > 80) return 'var(--red)'
  if (c > 65) return 'var(--yellow)'
  return 'var(--green)'
}

export default function GpuCard({ data, procs }) {
  const vramPct = data ? Math.round(data.vram_used_mib / data.vram_total_mib * 100) : 0

  return (
    <div className="card">
      <div className="card-title">GPU{data ? ` — ${data.name}` : ''}</div>
      {!data ? (
        <div className="loading">No GPU data</div>
      ) : (
        <>
          <div className="stat-items">
            <div className="stat-item">
              <div className="stat-label">Util</div>
              <div className="stat-big" style={{ color: pctColor(data.util_pct) }}>{data.util_pct}%</div>
              <div className="bar-wrap"><div className={`bar-fill ${barCls(data.util_pct)}`} style={{ width: `${data.util_pct}%` }} /></div>
            </div>
            <div className="stat-item">
              <div className="stat-label">VRAM</div>
              <div className="stat-big" style={{ color: pctColor(vramPct) }}>{vramPct}%</div>
              <div className="bar-wrap"><div className={`bar-fill ${barCls(vramPct)}`} style={{ width: `${vramPct}%` }} /></div>
              <div className="stat-sub">{data.vram_used_mib} / {data.vram_total_mib} MiB</div>
            </div>
            <div className="stat-item">
              <div className="stat-label">Temp</div>
              <div className="stat-big" style={{ color: tempColor(data.temp_c) }}>{data.temp_c}°C</div>
            </div>
          </div>
          <div style={{ marginTop: '10px' }}>
            <div className="stat-label" style={{ marginBottom: '6px' }}>Compute Processes</div>
            {!procs || procs.length === 0 ? (
              <div className="empty" style={{ fontSize: '12px' }}>No active compute processes</div>
            ) : (
              <table className="compact-table" style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead>
                  <tr>
                    <th style={{ fontSize: '11px', color: 'var(--text-muted)', textAlign: 'left', padding: '3px 6px', borderBottom: '1px solid var(--border)' }}>Process</th>
                    <th style={{ fontSize: '11px', color: 'var(--text-muted)', textAlign: 'left', padding: '3px 6px', borderBottom: '1px solid var(--border)' }}>PID</th>
                    <th style={{ fontSize: '11px', color: 'var(--text-muted)', textAlign: 'right', padding: '3px 6px', borderBottom: '1px solid var(--border)' }}>VRAM MiB</th>
                  </tr>
                </thead>
                <tbody>
                  {procs.map((p) => (
                    <tr key={p.pid}>
                      <td style={{ fontSize: '12px', padding: '3px 6px', fontFamily: 'monospace', maxWidth: '140px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{p.name}</td>
                      <td style={{ fontSize: '12px', padding: '3px 6px', color: 'var(--text-muted)', fontFamily: 'monospace' }}>{p.pid}</td>
                      <td style={{ fontSize: '12px', padding: '3px 6px', textAlign: 'right', fontFamily: 'monospace' }}>{p.vram_mib}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </>
      )}
    </div>
  )
}
