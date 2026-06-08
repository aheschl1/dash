function StatusBadge({ status }) {
  const cls = status === 'running' ? 'badge-running' : status === 'exited' ? 'badge-exited' : 'badge-other'
  return <span className={`badge ${cls}`}>{status}</span>
}

function HealthBadge({ health }) {
  if (!health || health === 'none') return null
  const cls = health === 'healthy' ? 'badge-healthy' : health === 'unhealthy' ? 'badge-unhealthy' : 'badge-starting'
  return <span className={`badge ${cls}`}>{health}</span>
}

export default function ContainersTable({ data, onViewLogs, onContainerAction }) {
  return (
    <div className="card">
      <div className="card-title">Containers ({data ? data.length : '…'})</div>
      {!data ? (
        <div className="loading">Loading…</div>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Status</th>
                <th>Health</th>
                <th>Ports</th>
                <th></th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {data.map((c) => {
                const running = c.status === 'running'
                const stopped = c.status === 'exited' || c.status === 'created'
                return (
                  <tr key={c.name}>
                    <td className="td-name">{c.name}</td>
                    <td><StatusBadge status={c.status} /></td>
                    <td><HealthBadge health={c.health} /></td>
                    <td>
                      {c.ports.length === 0
                        ? <span className="empty">—</span>
                        : c.ports.map((p) => <span className="port-tag" key={p}>{p}</span>)
                      }
                    </td>
                    <td>
                      <div className="action-btns">
                        {stopped && (
                          <button
                            className="action-btn"
                            title="Start"
                            onClick={() => onContainerAction(c.name, 'start')}
                          >▶</button>
                        )}
                        {running && (
                          <button
                            className="action-btn"
                            title="Restart"
                            onClick={() => {
                              if (window.confirm(`Restart container "${c.name}"?`)) {
                                onContainerAction(c.name, 'restart')
                              }
                            }}
                          >↺</button>
                        )}
                        {running && (
                          <button
                            className="action-btn action-btn-danger"
                            title="Stop"
                            onClick={() => {
                              if (window.confirm(`Stop container "${c.name}"?`)) {
                                onContainerAction(c.name, 'stop')
                              }
                            }}
                          >⏹</button>
                        )}
                      </div>
                    </td>
                    <td>
                      <button className="logs-btn" onClick={() => onViewLogs(c)}>logs</button>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
