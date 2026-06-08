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

export default function HardwareCard({ data }) {
  return (
    <div className="card">
      <div className="card-title">Hardware</div>
      {!data ? (
        <div className="loading">Loading…</div>
      ) : (
        <>
          <div className="hw-model">{data.model || 'Unknown CPU'}</div>
          <div className="hw-specs">
            <span>{data.physical_cores}C / {data.logical_cores}T</span>
            {data.freq_max_mhz && (
              <span>
                {data.freq_current_mhz ? `${(data.freq_current_mhz / 1000).toFixed(2)} / ` : ''}
                {(data.freq_max_mhz / 1000).toFixed(2)} GHz
              </span>
            )}
            <span>{data.arch}</span>
            {data.l3_cache && <span>L3 {data.l3_cache}</span>}
          </div>

          {data.load_avg && (
            <div className="hw-load">
              <span className="stat-label">Load avg</span>
              <span className="hw-load-vals">
                {data.load_avg.map((v, i) => {
                  const ratio = data.logical_cores ? v / data.logical_cores : 0
                  return (
                    <span key={i} style={{ color: pctColor(ratio * 100) }}>{v.toFixed(2)}</span>
                  )
                })}
              </span>
              <span className="stat-sub">1m · 5m · 15m</span>
            </div>
          )}

          {data.per_core_pct && data.per_core_pct.length > 0 && (
            <div className="hw-cores">
              {data.per_core_pct.map((p, i) => (
                <div className="hw-core" key={i} title={`Core ${i}: ${p}%`}>
                  <div className="hw-core-track">
                    <div
                      className={`hw-core-fill ${barCls(p)}`}
                      style={{ height: `${Math.max(p, 2)}%` }}
                    />
                  </div>
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  )
}
