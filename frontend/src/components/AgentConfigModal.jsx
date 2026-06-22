import { useState, useEffect, useRef } from 'react'
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
  const [modelsLoading, setModelsLoading] = useState(false)
  const modelsRequested = useRef(false)
  const [reflectEnabled, setReflectEnabled] = useState(true)
  const [reflectTime, setReflectTime] = useState('00:00')
  const [reflectNextRun, setReflectNextRun] = useState(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const [cr, rr] = await Promise.all([
          fetch('/api/agent/config').then(r => r.json()),
          fetch('/api/agent/reflection-config').then(r => r.json()),
        ])
        if (cancelled) return
        setCfg(cr)
        setBaseUrl(cr.base_url || '')
        setModel(cr.model || '')
        setReflectEnabled(rr.enabled !== false)
        setReflectTime(`${String(rr.hour ?? 0).padStart(2, '0')}:${String(rr.minute ?? 0).padStart(2, '0')}`)
        setReflectNextRun(rr.next_run_time || null)
      } catch {
        if (!cancelled) setError('Failed to load config')
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => { cancelled = true }
  }, [])

  // Best-effort: query the endpoint's /v1/models to offer a dropdown. This hits
  // the live LLM endpoint (up to an 8s timeout server-side), so it is loaded
  // lazily — on first focus of the Model field or via "refresh list" — never on
  // open, so the popup itself only ever waits on fast local config reads.
  const loadModels = async () => {
    modelsRequested.current = true
    setModelsError('')
    setModelsLoading(true)
    try {
      const r = await fetch('/api/agent/models')
      const d = await r.json()
      if (d.available) setModels(d.models || [])
      else setModelsError(d.error || 'endpoint unreachable')
    } catch {
      setModelsError('endpoint unreachable')
    } finally {
      setModelsLoading(false)
    }
  }
  const loadModelsOnce = () => { if (!modelsRequested.current) loadModels() }

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

        const [h, m] = reflectTime.split(':').map(n => parseInt(n, 10) || 0)
        const rr = await authedFetch('/api/agent/reflection-config', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ enabled: reflectEnabled, hour: h, minute: m }),
        })
        if (!rr.ok) throw new Error('Save failed (' + rr.status + ')')
        setReflectNextRun((await rr.json()).next_run_time || null)
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
      <div className="modal settings-modal" onClick={e => e.stopPropagation()}>
        <div className="modal-header">
          <div className="modal-title-group">
            <span className="modal-title">Agent Settings</span>
          </div>
          <button className="modal-close" onClick={onClose}>✕</button>
        </div>

        {loading ? (
          <div className="settings-body"><div className="form-hint">Loading…</div></div>
        ) : (
          <>
            <div className="settings-body">
              <section className="settings-section">
                <div className="settings-section-head">
                  <h3 className="settings-section-title">LLM endpoint</h3>
                  <p className="settings-section-sub">Any OpenAI-compatible server. Applies to the next message.</p>
                </div>

                <div className="settings-field">
                  <div className="settings-label-row">
                    <label className="settings-label">Base URL</label>
                    {baseUrl !== (cfg?.defaults?.base_url || '') && (
                      <button type="button" className="agent-cfg-link" onClick={() => resetToDefault('base_url')}>reset to default</button>
                    )}
                  </div>
                  <input className="form-input" value={baseUrl}
                    placeholder={cfg?.defaults?.base_url || 'http://llama-cpp:8080/v1'}
                    onChange={e => setBaseUrl(e.target.value)} />
                  <div className="form-hint">Should end in <code>/v1</code>. Default: <code>{cfg?.defaults?.base_url}</code></div>
                </div>

                <div className="settings-field">
                  <div className="settings-label-row">
                    <label className="settings-label">Model</label>
                    <span className="settings-label-actions">
                      <button type="button" className="agent-cfg-link" onClick={loadModels}>refresh list</button>
                      {model !== (cfg?.defaults?.model || '') && (
                        <button type="button" className="agent-cfg-link" onClick={() => resetToDefault('model')}>reset</button>
                      )}
                    </span>
                  </div>
                  <input className="form-input" value={model} list="agent-model-list"
                    placeholder={cfg?.defaults?.model || ''}
                    onFocus={loadModelsOnce}
                    onChange={e => setModel(e.target.value)} />
                  <datalist id="agent-model-list">
                    {models.map(m => <option key={m} value={m} />)}
                  </datalist>
                  <div className="form-hint">
                    {modelsLoading
                      ? 'Listing models from this endpoint…'
                      : models.length > 0
                        ? `${models.length} model${models.length === 1 ? '' : 's'} available from this endpoint`
                        : (modelsError ? `Couldn't list models: ${modelsError}` : 'Focus to list available models')}
                  </div>
                </div>

                <div className="settings-field">
                  <label className="settings-label">API key</label>
                  <input className="form-input" type="password" value={apiKey}
                    placeholder={cfg?.api_key_set ? '•••••••• (set — leave blank to keep)' : 'only if your endpoint authenticates'}
                    onChange={e => setApiKey(e.target.value)} />
                </div>
              </section>

              <section className="settings-section">
                <div className="settings-section-head">
                  <h3 className="settings-section-title">Memory reflection</h3>
                  <p className="settings-section-sub">A scheduled job where the agent reviews recent conversations and curates its durable memories.</p>
                </div>

                <div className="settings-toggle-row">
                  <div className="settings-toggle-text">
                    <div className="settings-toggle-name">Run nightly reflection</div>
                    <div className="form-hint">Turn off to disable the job entirely.</div>
                  </div>
                  <label className="switch">
                    <input type="checkbox" checked={reflectEnabled}
                      onChange={e => setReflectEnabled(e.target.checked)} />
                    <span className="switch-slider" />
                  </label>
                </div>

                <div className={`settings-field settings-time-field${reflectEnabled ? '' : ' is-disabled'}`}>
                  <label className="settings-label">Run at</label>
                  <div className="settings-time-row">
                    <input className="form-input" type="time" value={reflectTime} disabled={!reflectEnabled}
                      onChange={e => setReflectTime(e.target.value)} />
                    <span className="form-hint" style={{ margin: 0 }}>
                      {reflectEnabled
                        ? (reflectNextRun ? `Next run ${new Date(reflectNextRun).toLocaleString()}` : 'Scheduled')
                        : 'Disabled'}
                    </span>
                  </div>
                </div>
              </section>
            </div>

            <div className="settings-footer">
              <div className="settings-footer-status">
                {error && <span className="login-error" style={{ margin: 0 }}>{error}</span>}
                {!error && saved && <span className="settings-saved">✓ Saved</span>}
              </div>
              <button className="submit-btn" type="button" onClick={doSave} disabled={saving}>
                {saving ? 'Saving…' : 'Save changes'}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
