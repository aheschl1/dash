import { useState, useEffect } from 'react'

const STATUS_COLOR = {
  pending: 'var(--text-muted)',
  'in-progress': 'var(--yellow)',
  done: 'var(--green)',
  'needs-review': 'var(--orange)',
  failed: 'var(--red)',
}

export default function FeedbackPanel() {
  const [items, setItems] = useState([])

  async function load() {
    try {
      const r = await fetch('/api/feedback')
      if (r.ok) setItems(await r.json())
    } catch {}
  }

  useEffect(() => {
    load()
    const t = setInterval(load, 15000)
    return () => clearInterval(t)
  }, [])

  const pendingCount = items.filter(i => i.status === 'pending').length

  return (
    <div className="card">
      <div className="card-title" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <span>Feedback Queue</span>
        <span className="feedback-count">{pendingCount} pending</span>
      </div>

      <div className="feedback-list">
        {items.length === 0 && <div className="empty">No feedback yet — use the Feedback button to submit.</div>}
        {items.map(item => (
          <div key={item.id} className="feedback-item">
            <div className="feedback-body">
              <div className="feedback-header-row">
                <span className="feedback-title">{item.title}</span>
                <span className="feedback-status" style={{ color: STATUS_COLOR[item.status] }}>
                  {item.status}
                </span>
              </div>
              {item.description && <div className="feedback-desc">{item.description}</div>}
              {item.resolution_note && (
                <div className="feedback-note">↳ {item.resolution_note}</div>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
