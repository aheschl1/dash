import { useState } from 'react'

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
  if (!c) return 'var(--text)'
  if (c > 70) return 'var(--red)'
  if (c > 55) return 'var(--yellow)'
  return 'var(--green)'
}

function formatHours(h) {
  if (h == null) return '—'
  const years = (h / 8760).toFixed(1)
  return `${h.toLocaleString()} hrs (${years} yrs)`
}

function skipMount(mount) {
  if (mount.startsWith('/sys')) return true
  if (mount.startsWith('/proc')) return true
  // Skip /dev except real block devices mounted elsewhere
  if (mount === '/dev') return true
  if (mount.startsWith('/dev/') && !mount.match(/^\/dev\/(sd|nv|hd|xv|vd)/)) return true
  return false
}

function HealthRow({ label, value, color }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between' }}>
      <span style={{ color: 'var(--text-muted)' }}>{label}</span>
      <span style={{ fontFamily: 'monospace', color: color || 'var(--text)' }}>{value}</span>
    </div>
  )
}

function SmartHealth({ data }) {
  if (!data) return <div className="loading">Loading…</div>
  if (data.length === 0) return <div className="empty">No SMART data available</div>
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
      {data.map((dev) => (
        <div key={dev.device}>
          <div style={{ fontFamily: 'monospace', fontWeight: 600, fontSize: '13px', marginBottom: '6px', color: 'var(--accent)' }}>
            {dev.device} <span style={{ color: 'var(--text-muted)', fontWeight: 400, fontSize: '11px' }}>{dev.type?.toUpperCase()}</span>
          </div>
          {dev.type === 'nvme' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '4px', fontSize: '12px' }}>
              <HealthRow label="Temperature" color={tempColor(dev.attrs.temperature_c)}
                value={dev.attrs.temperature_c != null ? `${dev.attrs.temperature_c}°C` : '—'} />
              <HealthRow label="Available Spare"
                value={dev.attrs.available_spare_pct != null ? `${dev.attrs.available_spare_pct}%` : '—'} />
              <HealthRow label="Percentage Used" color={dev.attrs.percentage_used > 80 ? 'var(--red)' : 'var(--text)'}
                value={dev.attrs.percentage_used != null ? `${dev.attrs.percentage_used}%` : '—'} />
              <HealthRow label="Power-On Hours" value={formatHours(dev.attrs.power_on_hours)} />
              <HealthRow label="Unsafe Shutdowns" value={dev.attrs.unsafe_shutdowns ?? '—'} />
            </div>
          )}
          {dev.type === 'ata' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '4px', fontSize: '12px' }}>
              <HealthRow label="Temperature" color={tempColor(dev.attrs.Temperature_Celsius)}
                value={dev.attrs.Temperature_Celsius != null ? `${dev.attrs.Temperature_Celsius}°C` : '—'} />
              <HealthRow label="Reallocated Sectors" color={dev.attrs.Reallocated_Sector_Ct > 0 ? 'var(--red)' : 'var(--text)'}
                value={dev.attrs.Reallocated_Sector_Ct ?? '—'} />
              <HealthRow label="Pending Sectors" color={dev.attrs.Current_Pending_Sector > 0 ? 'var(--red)' : 'var(--text)'}
                value={dev.attrs.Current_Pending_Sector ?? '—'} />
              <HealthRow label="Power-On Hours" value={formatHours(dev.attrs.Power_On_Hours)} />
            </div>
          )}
        </div>
      ))}
    </div>
  )
}

export default function DiskCard({ data, smart }) {
  const [showHealth, setShowHealth] = useState(false)
  const partitions = data ? data.filter(p => !skipMount(p.mount)) : []

  return (
    <div className="card">
      <div className="card-title">Disk</div>
      {!data ? (
        <div className="loading">Loading…</div>
      ) : partitions.length === 0 ? (
        <div className="empty">No partitions found</div>
      ) : (
        <div className="disk-list">
          {partitions.map((p) => (
            <div className="disk-row" key={p.source + p.mount}>
              <div className="disk-row-top">
                <div>
                  <div className="disk-mount">{p.mount}</div>
                  <div className="disk-source">{p.source}</div>
                </div>
                <div style={{ textAlign: 'right' }}>
                  <div className="disk-pct" style={{ color: pctColor(p.pct) }}>{p.pct}%</div>
                  <div className="disk-usage">{p.used_gb} / {p.size_gb} GB</div>
                </div>
              </div>
              <div className="disk-bar-wrap">
                <div
                  className={`disk-bar-fill ${barCls(p.pct)}`}
                  style={{ width: `${p.pct}%` }}
                />
              </div>
            </div>
          ))}
        </div>
      )}

      <button
        className="disk-health-toggle"
        onClick={() => setShowHealth((v) => !v)}
        aria-expanded={showHealth}
      >
        <span>SMART Disk Health</span>
        <span className={`disk-health-chevron${showHealth ? ' open' : ''}`}>▾</span>
      </button>
      {showHealth && (
        <div className="disk-health-body">
          <SmartHealth data={smart} />
        </div>
      )}
    </div>
  )
}
