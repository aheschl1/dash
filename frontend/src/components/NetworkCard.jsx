function fmtRate(bps) {
  if (bps < 1024) return `${bps.toFixed(0)} B/s`
  if (bps < 1024 * 1024) return `${(bps / 1024).toFixed(1)} KB/s`
  return `${(bps / 1024 / 1024).toFixed(2)} MB/s`
}

export default function NetworkCard({ data }) {
  return (
    <div className="card">
      <div className="card-title">Network</div>
      {!data ? (
        <div className="loading">Loading…</div>
      ) : data.length === 0 ? (
        <div className="empty">No network interfaces</div>
      ) : (
        <div className="net-list">
          {data.map((iface) => (
            <div className="net-row" key={iface.iface}>
              <div className="net-row-top">
                <div className="net-iface">{iface.iface}</div>
                <div className="net-rates">
                  <span className="net-rx">↓ {fmtRate(iface.rx_bps)}</span>
                  <span className="net-tx">↑ {fmtRate(iface.tx_bps)}</span>
                </div>
              </div>
              <div className="net-totals">
                rx {iface.rx_total_gb.toFixed(3)} GB &nbsp;/&nbsp; tx {iface.tx_total_gb.toFixed(3)} GB
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
