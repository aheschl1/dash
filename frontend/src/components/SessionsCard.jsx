export default function SessionsCard({ data }) {
  return (
    <div className="card">
      <div className="card-title">Active Sessions</div>
      {!data ? (
        <div className="loading">Loading…</div>
      ) : data.length === 0 ? (
        <div className="empty">No active sessions</div>
      ) : (
        <div className="sessions-list">
          {data.map((s, i) => (
            <div key={i} className={`session-row${s.public ? ' session-public' : ''}`}>
              <span className="session-user">{s.user}</span>
              <span className="session-tty">{s.tty}</span>
              <span className="session-time">{s.login_time}</span>
              {s.host && <span className="session-host">{s.host}</span>}
              {s.public && <span className="session-badge">public</span>}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
