import { useState } from 'react'
import { login } from '../auth'

export default function LoginModal({ onClose, onSuccess }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')

  async function handleSubmit(e) {
    e.preventDefault()
    if (!username.trim() || !password) return
    setSubmitting(true)
    setError('')
    try {
      await login(username.trim(), password)
      onSuccess()
    } catch (err) {
      setError(err.message || 'Login failed')
      setSubmitting(false)
    }
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" style={{ maxWidth: 380 }} onClick={e => e.stopPropagation()}>
        <div className="modal-header">
          <div className="modal-title-group">
            <span className="modal-title">Admin Login</span>
          </div>
          <button className="modal-close" onClick={onClose}>✕</button>
        </div>

        <form className="feedback-form" onSubmit={handleSubmit}>
          <div>
            <div className="form-label">Username</div>
            <input className="form-input" value={username} autoFocus
              autoComplete="username"
              onChange={e => setUsername(e.target.value)} />
          </div>
          <div>
            <div className="form-label">Password</div>
            <input className="form-input" type="password" value={password}
              autoComplete="current-password"
              onChange={e => setPassword(e.target.value)} />
          </div>
          {error && <div className="login-error">{error}</div>}
          <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
            <button className="submit-btn" type="submit" disabled={submitting || !username.trim() || !password}>
              {submitting ? 'Signing in…' : 'Sign in'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
