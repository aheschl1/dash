const STATE_CLASS = {
  ESTABLISHED: 'conn-state-estab',
  ESTAB: 'conn-state-estab',
  TIME_WAIT: 'conn-state-wait',
  CLOSE_WAIT: 'conn-state-wait',
  FIN_WAIT: 'conn-state-wait',
  SYN_SENT: 'conn-state-pending',
  SYN_RECV: 'conn-state-pending',
}

const SCOPE_CLASS = {
  public: 'conn-scope-public',
  private: 'conn-scope-private',
  loopback: 'conn-scope-loopback',
  other: 'conn-scope-other',
}

function fmtEndpoint(addr, port) {
  if (!port && port !== 0) return addr
  if (port === '' || port == null) return addr
  return `${addr}:${port}`
}

export default function ConnectionsCard({ data }) {
  return (
    <div className="card">
      <div className="card-title">
        Network Surface
        {data && <span className="conn-total">{data.total}</span>}
        {data?.source === 'ss' && (
          <span className="conn-src-note">conntrack unavailable — socket view</span>
        )}
      </div>
      {!data ? (
        <div className="loading">Loading…</div>
      ) : data.connections.length === 0 ? (
        <div className="empty">No active flows</div>
      ) : (
        <>
          <div className="conn-summary">
            {data.by_scope && Object.entries(data.by_scope)
              .sort((a, b) => b[1] - a[1])
              .map(([scope, count]) => (
                <span className="conn-chip" key={`scope-${scope}`}>
                  <span className={`conn-dot ${SCOPE_CLASS[scope] || ''}`} />
                  {scope} {count}
                </span>
              ))}
            {data.by_proto && Object.entries(data.by_proto)
              .sort((a, b) => b[1] - a[1])
              .map(([proto, count]) => (
                <span className="conn-chip conn-chip-proto" key={`proto-${proto}`}>
                  {proto} {count}
                </span>
              ))}
          </div>
          <div className="conn-scroll">
            <div className="conn-row conn-head">
              <span className="conn-proto">Proto</span>
              <span className="conn-state">State</span>
              <span className="conn-local">Source</span>
              <span className="conn-peer">Destination</span>
              <span className="conn-proc">Process</span>
            </div>
            {data.connections.map((c, i) => (
              <div className="conn-row" key={i}>
                <span className="conn-proto">{c.proto}</span>
                <span className={`conn-state ${STATE_CLASS[c.state] || ''}`}>
                  {c.state || '—'}
                </span>
                <span className={`conn-local ${SCOPE_CLASS[c.src_scope] || ''}`}>
                  {fmtEndpoint(c.src_addr, c.src_port)}
                </span>
                <span className={`conn-peer ${SCOPE_CLASS[c.dst_scope] || ''}`}>
                  {fmtEndpoint(c.dst_addr, c.dst_port)}
                  {c.nat && <span className="conn-nat" title="NAT translated">nat</span>}
                </span>
                <span className="conn-proc">{c.process || '—'}</span>
              </div>
            ))}
          </div>
          {data.total > data.connections.length && (
            <div className="conn-truncated">
              showing {data.connections.length} of {data.total}
            </div>
          )}
        </>
      )}
    </div>
  )
}
