function isRecent(handshake) {
  if (!handshake) return false
  return handshake.includes('second') || handshake.includes('minute')
}

export default function WireguardCard({ data, onAddClient }) {
  return (
    <div className="card">
      <div className="card-title wg-title">
        WireGuard
        {onAddClient && (
          <button className="wg-add-btn" onClick={onAddClient}>+ Add client</button>
        )}
      </div>
      {!data ? (
        <div className="loading">Loading…</div>
      ) : (
        <>
          <div className="wg-ip">
            {data.interface_ip ?? 'wg0 not found'}
            {data.peer_count > 0 && (
              <span style={{ fontSize: 12, color: 'var(--text-muted)', marginLeft: 12 }}>
                {data.peer_count} peer{data.peer_count !== 1 ? 's' : ''}
              </span>
            )}
          </div>
          {data.peers.length === 0 ? (
            <div className="empty">No peer data (requires root)</div>
          ) : (
            <div className="peer-scroll">
              {data.peers.map((p) => {
                const shortKey = p.pubkey ? p.pubkey.slice(0, 12) + '…' : ''
                const active = isRecent(p.last_handshake)
                return (
                  <div className="peer-card" key={p.pubkey}>
                    <div className="peer-header">
                      <div className={`peer-dot ${active ? 'peer-dot-active' : 'peer-dot-inactive'}`} />
                      <div className="peer-allowed-ips">{p.allowed_ips || shortKey}</div>
                    </div>
                    <div className="peer-pubkey">{shortKey}</div>
                    {p.endpoint && (
                      <div className="peer-detail">
                        endpoint: <span>{p.endpoint}</span>
                      </div>
                    )}
                    {p.last_handshake && (
                      <div className="peer-detail">
                        handshake: <span>{p.last_handshake}</span>
                      </div>
                    )}
                    {p.transfer && (
                      <div className="peer-detail">
                        transfer: <span>{p.transfer}</span>
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          )}
        </>
      )}
    </div>
  )
}
