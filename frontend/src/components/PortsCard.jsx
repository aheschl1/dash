export default function PortsCard({ data }) {
  return (
    <div className="card">
      <div className="card-title">Host Open Ports</div>
      {!data ? (
        <div className="loading">Loading…</div>
      ) : data.length === 0 ? (
        <div className="empty">None found</div>
      ) : (
        <div className="ports-list">
          {data.map((p) => (
            <div className="port-row" key={`${p.address}:${p.port}`}>
              <span className="port-number">{p.port}</span>
              <span className="port-addr">{p.address}</span>
              <span className="port-proc">{p.process || '—'}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
