import { useState, useEffect, useRef, useCallback } from 'react'

const LINE_OPTIONS = [50, 100, 200, 500]

export default function LogsModal({ container, onClose }) {
  const isVm = container.kind === 'vm'
  const [lines, setLines] = useState(100)
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const bottomRef = useRef(null)

  const fetch_ = useCallback(async (n = lines) => {
    setLoading(true)
    const url = isVm
      ? `/api/vms/${encodeURIComponent(container.name)}/logs?lines=${n}`
      : `/api/logs/${encodeURIComponent(container.name)}?lines=${n}`
    try {
      const r = await fetch(url)
      const j = await r.json()
      setData(j)
    } catch {
      setData({ error: 'Failed to fetch logs' })
    } finally {
      setLoading(false)
    }
  }, [container.name, isVm, lines])

  useEffect(() => { fetch_(lines) }, [container.name])

  useEffect(() => {
    if (bottomRef.current) bottomRef.current.scrollIntoView()
  }, [data])

  function handleLines(n) {
    setLines(n)
    fetch_(n)
  }

  function handleKey(e) {
    if (e.key === 'Escape') onClose()
  }

  return (
    <div className="modal-overlay" onClick={onClose} onKeyDown={handleKey} role="dialog">
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <div className="modal-title-group">
            <span className="modal-title">{isVm ? 'VM Logs' : 'Logs'}</span>
            <span className="modal-container-name">{container.name}</span>
          </div>
          <div className="modal-controls">
            <div className="line-selector">
              {LINE_OPTIONS.map((n) => (
                <button
                  key={n}
                  className={`line-btn${lines === n ? ' active' : ''}`}
                  onClick={() => handleLines(n)}
                >
                  {n}
                </button>
              ))}
            </div>
            <button className="modal-refresh" onClick={() => fetch_(lines)} disabled={loading} title="Refresh">
              ↻
            </button>
            <button className="modal-close" onClick={onClose} title="Close">✕</button>
          </div>
        </div>

        <div className="modal-body">
          {loading && !data && <div className="loading" style={{ padding: '24px' }}>Loading…</div>}
          {data?.error && <div className="loading" style={{ padding: '24px', color: 'var(--red)' }}>{data.error}</div>}
          {data?.lines && (
            <pre className="log-output">
              {data.lines.map((line, i) => (
                <LogLine key={i} line={line} />
              ))}
              <div ref={bottomRef} />
            </pre>
          )}
        </div>
      </div>
    </div>
  )
}

function LogLine({ line }) {
  // Docker timestamps look like: 2026-05-24T08:17:22.846Z <message>
  const tsMatch = line.match(/^(\d{4}-\d{2}-\d{2}T[\d:.]+Z)\s(.*)$/)
  if (!tsMatch) return <span className="log-line">{line}{'\n'}</span>

  const ts = tsMatch[1].replace('T', ' ').replace(/\.\d+Z$/, '')
  const msg = tsMatch[2]

  const color = msg.match(/\b(error|err|fatal|panic)\b/i)
    ? 'var(--red)'
    : msg.match(/\b(warn|warning)\b/i)
    ? 'var(--yellow)'
    : msg.match(/\b(info|started|success|healthy|ready)\b/i)
    ? 'var(--green)'
    : 'var(--text)'

  return (
    <span className="log-line">
      <span className="log-ts">{ts} </span>
      <span style={{ color }}>{msg}</span>
      {'\n'}
    </span>
  )
}
