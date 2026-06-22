import { useState, useEffect } from 'react'
import { authedFetch } from '../auth'

// Edit the agent's LLM endpoint/model at runtime (DB-backed, survives rebuilds).
// Reading the config is public; saving is admin-gated — the parent wraps onSave
// through runProtected so a login is prompted and the PUT replays on success.
export default function AgentConfigModal({ onClose, runProtected }) {
  const [baseUrl, setBaseUrl] = useState('')
  const [model, setModel] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [cfg, setCfg] = useState(null)
  const [models, setModels] = useState([])
  const [modelsError, setModelsError] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const r = await fetch('/api/agent/config')
        const d = await r.json()
        if (cancelled) return
        setCfg(d)
        setBaseUrl(d.base_url || '')
        setModel(d.model || '')
      } catch {
        if (!cancelled) setError('Failed to load config')
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => { cancelled = true }
  }, [])

  // Best-effort: query the endpoint's /v1/models to offer a dropdown.
  const loadModels = async () => {
    setModelsError('')
    try {
      const r = await fetch('/api/agent/models')
      const d = await r.json()
      if (d.available) setModels(d.models || [])
      else setModelsError(d.error || 'endpoint unreachable')
    } catch {
      setModelsError('endpoint unreachable')
    }
  }
  useEffect(() => { loadModels() }, [])

  function doSave() {
    setSaving(true)
    setError('')
    setSaved(false)
    runProtected(async () => {
      try {
        const body = { base_url: baseUrl.trim(), model: model.trim() }
        // Only send api_key if the user typed one — blank keeps the stored value.
        if (apiKey) body.api_key = apiKey
        const r = await authedFetch('/api/agent/config', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        })
        if (!r.ok) throw new Error('Save failed (' + r.status + ')')
        const d = await r.json()
        setCfg(d)
        setApiKey('')
        setSaved(true)
      } catch (err) {
        setError(err.message || 'Save failed')
      } finally {
        setSaving(false)
      }
    })
  }

  function resetToDefault(field) {
    if (field === 'base_url') setBaseUrl('')
    if (field === 'model') setModel('')
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" style={{ maxWidth: 460 }} onClick={e => e.stopPropagation()}>
        <div className="modal-header">
          <div className="modal-title-group">
            <span className="modal-title">Agent LLM Endpoint</span>
          </div>
          <button className="modal-close" onClick={onClose}>✕</button>
        </div>

        {loading ? (
          <div className="form-hint" style={{ padding: '8px 0' }}>Loading…</div>
        ) : (
          <div className="feedback-form">
            <div>
              <div className="form-label">Base URL (OpenAI-compatible /v1)</div>
              <input className="form-input" value={baseUrl}
                placeholder={cfg?.defaults?.base_url || 'http://llama-cpp:8080/v1'}
                onChange={e => setBaseUrl(e.target.value)} />
              <div className="form-hint">
                Default: {cfg?.defaults?.base_url}
                {!baseUrl && <span> (using default)</span>}
                {' · '}<button type="button" className="agent-cfg-link" onClick={() => resetToDefault('base_url')}>reset</button>
              </div>
            </div>

            <div>
              <div className="form-label">Model</div>
              <input className="form-input" value={model} list="agent-model-list"
                placeholder={cfg?.defaults?.model || ''}
                onChange={e => setModel(e.target.value)} />
              <datalist id="agent-model-list">
                {models.map(m => <option key={m} value={m} />)}
              </datalist>
              <div className="form-hint">
                {models.length > 0
                  ? `${models.length} model(s) reported by the endpoint`
                  : (modelsError ? `Could not list models: ${modelsError}` : 'No models listed')}
                {' · '}<button type="button" className="agent-cfg-link" onClick={loadModels}>refresh</button>
                {' · '}<button type="button" className="agent-cfg-link" onClick={() => resetToDefault('model')}>reset</button>
              </div>
            </div>

            <div>
              <div className="form-label">API key {cfg?.api_key_set ? '(set — leave blank to keep)' : '(optional)'}</div>
              <input className="form-input" type="password" value={apiKey}
                placeholder={cfg?.api_key_set ? '••••••••' : 'only if your endpoint authenticates'}
                onChange={e => setApiKey(e.target.value)} />
            </div>

            {error && <div className="login-error">{error}</div>}
            {saved && <div className="form-hint" style={{ color: 'var(--green)' }}>Saved — applies on the next turn.</div>}

            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
              <button className="submit-btn" type="button" onClick={doSave} disabled={saving}>
                {saving ? 'Saving…' : 'Save'}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
