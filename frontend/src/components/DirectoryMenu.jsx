import { useEffect } from 'react'

const HTTPS_PORTS = new Set(['443', '8443', '8561'])
// Ports that aren't HTTP services, so we render them as plain text not a link.
const NON_WEB_PORTS = new Set(['5432'])

// Build a clickable URL for a host that may carry a :port. A bare host (no
// port) defaults to https; otherwise the scheme follows the port.
function hostHref(host) {
  const port = host.includes(':') ? host.split(':').pop() : ''
  if (NON_WEB_PORTS.has(port)) return null
  const scheme = !port || HTTPS_PORTS.has(port) ? 'https' : 'http'
  return `${scheme}://${host}`
}

export default function DirectoryMenu({ data, open, onClose }) {
  useEffect(() => {
    if (!open) return
    function onKey(e) {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  if (!open) return null

  return (
    <div className="drawer-overlay" onClick={onClose} role="dialog">
      <aside className="drawer" onClick={(e) => e.stopPropagation()}>
        <div className="drawer-header">
          <span className="drawer-title">Directory</span>
          <button className="modal-close" onClick={onClose} title="Close">✕</button>
        </div>
        <div className="drawer-body">
          {!data ? (
            <div className="loading">Loading…</div>
          ) : data.length === 0 ? (
            <div className="empty">None found</div>
          ) : (
            data.map((d, i) => {
              const target = d.domain || d.ip
              const href = target ? hostHref(target) : null
              return (
                <div className="drawer-item" key={`${d.name}-${i}`}>
                  <span className="drawer-item-name">{d.name}</span>
                  {href ? (
                    <a className="drawer-item-link" href={href} target="_blank" rel="noreferrer">
                      {d.domain || d.ip}
                    </a>
                  ) : (
                    <span className="drawer-item-addr">{d.domain || d.ip || '—'}</span>
                  )}
                  {d.domain && d.ip && <span className="drawer-item-ip">{d.ip}</span>}
                </div>
              )
            })
          )}
        </div>
      </aside>
    </div>
  )
}
