import { useState } from 'react'
import { authedFetch } from '../auth'

export default function AddClientModal({ onClose, onCreated }) {
  const [name, setName] = useState('')
  const [address, setAddress] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState(null)
  const [result, setResult] = useState(null)

  async function handleSubmit(e) {
    e.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      const r = await authedFetch('/api/vpn/clients', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: name.trim(), address: address.trim() }),
      })
      const d = await r.json().catch(() => ({}))
      if (!r.ok) {
        setError(d.error || r.statusText)
      } else {
        setResult(d)
        onCreated?.()
      }
    } catch (err) {
      setError(err.message)
    }
    setSubmitting(false)
  }

  function download() {
    const blob = new Blob([result.config], { type: 'text/plain' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = result.filename
    document.body.appendChild(a)
    a.click()
    a.remove()
    URL.revokeObjectURL(url)
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" style={{ maxWidth: 520 }} onClick={e => e.stopPropagation()}>
        <div className="modal-header">
          <div className="modal-title-group">
            <span className="modal-title">Add VPN Client</span>
          </div>
          <button className="modal-close" onClick={onClose}>✕</button>
        </div>

        {result ? (
          <div className="vpn-result">
            {result.activated ? (
              <div className="vpn-status vpn-status-ok">✓ Peer added and activated on wg0</div>
            ) : (
              <div className="vpn-status vpn-status-warn">
                ⚠ Peer added to server config, but live activation failed: {result.activation_error}
              </div>
            )}
            <div className="form-label">{result.filename}</div>
            <pre className="vpn-config">{result.config}</pre>
            <div className="vpn-note">
              This config contains the client's private key — it is shown only once and not stored.
              Download it now and transfer it securely.
            </div>
            <div className="vpn-actions">
              <button className="submit-btn" onClick={download}>Download {result.filename}</button>
            </div>
          </div>
        ) : (
          <form className="feedback-form" onSubmit={handleSubmit}>
            <div>
              <div className="form-label">Client name</div>
              <input className="form-input" value={name}
                onChange={e => setName(e.target.value)}
                placeholder="e.g. tablet" maxLength={32} autoFocus required />
            </div>
            <div>
              <div className="form-label">VPN address</div>
              <input className="form-input" value={address}
                onChange={e => setAddress(e.target.value)}
                placeholder="e.g. 10.8.0.5" required />
            </div>
            {error && <div className="vpn-error">{error}</div>}
            <div className="vpn-actions">
              <button className="submit-btn" type="submit"
                disabled={submitting || !name.trim() || !address.trim()}>
                {submitting ? 'Creating…' : 'Create client'}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  )
}
