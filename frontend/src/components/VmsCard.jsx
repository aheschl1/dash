import { Fragment, useState, useEffect, useCallback } from 'react'

function pctColor(pct) {
  if (pct > 85) return 'var(--red)'
  if (pct > 60) return 'var(--yellow)'
  return 'var(--green)'
}

function stateBadge(state) {
  if (state === 'running') return 'badge-running'
  if (state === 'shut off' || state === 'crashed') return 'badge-exited'
  return 'badge-other'
}

function mem(mb) {
  if (mb == null) return '—'
  return mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${mb} MB`
}

function VmNetDetail({ name }) {
  const [net, setNet] = useState(null)
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const r = await fetch(`/api/vms/${encodeURIComponent(name)}/network`)
      setNet(await r.json())
    } catch {
      setNet({ error: 'Failed to fetch network info' })
    } finally {
      setLoading(false)
    }
  }, [name])

  useEffect(() => { load() }, [load])

  if (loading && !net) return <div className="vm-net-body loading">Loading…</div>
  if (net?.error) return <div className="vm-net-body empty">{net.error}</div>
  if (!net?.available) return <div className="vm-net-body empty">VM not running — no network info</div>

  return (
    <div className="vm-net-body">
      <div className="vm-net-source">addresses via {net.source}</div>
      {net.interfaces.map((iface) => (
        <div key={iface.mac} className="vm-net-iface">
          <span className="vm-net-name">{iface.interface}</span>
          <span className="vm-net-meta">{iface.type}/{iface.model} · {iface.source} · {iface.mac}</span>
          <span className="vm-net-addrs">
            {iface.addresses.length === 0
              ? <span className="vm-net-noip">no IP</span>
              : iface.addresses.map((a, i) => (
                  <span key={i} className="vm-net-ip">
                    {a.address}{a.prefix != null ? `/${a.prefix}` : ''}
                  </span>
                ))}
          </span>
        </div>
      ))}
    </div>
  )
}

export default function VmsCard({ data, onViewVmLogs }) {
  const vms = data?.vms ?? []
  const [expanded, setExpanded] = useState(null)

  return (
    <div className="card">
      <div className="card-title">Virtual Machines</div>
      {!data ? (
        <div className="loading">Loading…</div>
      ) : !data.available ? (
        <div className="empty">libvirt not available on this host</div>
      ) : vms.length === 0 ? (
        <div className="empty">No VMs defined</div>
      ) : (
        <div className="table-wrap">
          <table className="compact-table">
            <thead>
              <tr>
                <th>Name</th>
                <th>State</th>
                <th>vCPU</th>
                <th>CPU%</th>
                <th>Memory</th>
                <th>Host RSS</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {vms.map((v) => (
                <Fragment key={v.name}>
                  <tr>
                    <td className="td-name">
                      {v.name}
                      {v.autostart && <span className="vm-tag" title="Autostart enabled">auto</span>}
                    </td>
                    <td><span className={`badge ${stateBadge(v.state)}`}>{v.state}</span></td>
                    <td style={{ fontFamily: 'monospace' }}>{v.vcpus ?? '—'}</td>
                    <td style={{ fontFamily: 'monospace', fontWeight: 600, color: v.cpu_pct != null ? pctColor(v.cpu_pct) : 'var(--text-muted)' }}>
                      {v.cpu_pct != null ? `${v.cpu_pct.toFixed(1)}%` : '—'}
                    </td>
                    <td style={{ fontFamily: 'monospace' }}>
                      {v.guest_used_mb != null ? (
                        <span style={{ color: pctColor(v.guest_used_pct) }}>
                          {mem(v.guest_used_mb)} <span style={{ color: 'var(--text-muted)' }}>/ {mem(v.max_mem_mb)}</span>
                        </span>
                      ) : (
                        <span style={{ color: 'var(--text-muted)' }}>{mem(v.alloc_mem_mb)}</span>
                      )}
                    </td>
                    <td style={{ fontFamily: 'monospace', color: 'var(--text-muted)' }}>{mem(v.rss_mb)}</td>
                    <td className="vm-actions">
                      <button className="logs-btn" onClick={() => onViewVmLogs(v)}>logs</button>
                      <button
                        className={`logs-btn${expanded === v.name ? ' active' : ''}`}
                        onClick={() => setExpanded(expanded === v.name ? null : v.name)}
                      >
                        net
                      </button>
                    </td>
                  </tr>
                  {expanded === v.name && (
                    <tr className="vm-net-row">
                      <td colSpan={7}>
                        <VmNetDetail name={v.name} />
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
