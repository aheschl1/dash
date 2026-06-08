function actionColor(action) {
  if (action === 'start' || action === 'health_status: healthy') return 'var(--green)'
  if (action === 'die' || action === 'kill' || action === 'oom' || action === 'health_status: unhealthy') return 'var(--red)'
  if (action === 'stop' || action === 'destroy') return 'var(--orange)'
  return 'var(--text-muted)'
}

export default function EventsFeed({ data }) {
  return (
    <div className="card">
      <div className="card-title">Events</div>
      {!data ? (
        <div className="loading">Loading…</div>
      ) : data.length === 0 ? (
        <div className="empty">No recent events</div>
      ) : (
        <div className="events-list">
          {data.map((ev, i) => (
            <div key={i} className="event-row">
              <span className="event-ts">{new Date(ev.ts * 1000).toLocaleTimeString()}</span>
              <span className="event-action" style={{ color: actionColor(ev.action) }}>{ev.action}</span>
              <span className="event-container">{ev.container}</span>
              {ev.image && <span className="event-image">{ev.image}</span>}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
