import { useEffect, useState } from 'react'

const STORAGE_KEY = 'admindash.dismissedAlerts'

function loadDismissed() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    return new Set(raw ? JSON.parse(raw) : [])
  } catch {
    return new Set()
  }
}

function saveDismissed(set) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify([...set]))
  } catch {}
}

export default function AlertBanner({ alerts }) {
  const [dismissed, setDismissed] = useState(loadDismissed)

  // Prune dismissed entries for alerts that are no longer active, so the set
  // doesn't grow forever and a re-occurring alert reappears.
  useEffect(() => {
    if (!alerts) return
    const active = new Set(alerts.map(a => a.message))
    let changed = false
    const next = new Set()
    for (const m of dismissed) {
      if (active.has(m)) next.add(m)
      else changed = true
    }
    if (changed) {
      setDismissed(next)
      saveDismissed(next)
    }
  }, [alerts])

  if (!alerts || alerts.length === 0) return null

  const dismiss = (message) => {
    const next = new Set(dismissed)
    next.add(message)
    setDismissed(next)
    saveDismissed(next)
  }

  const visible = alerts.filter(a => !dismissed.has(a.message))
  if (visible.length === 0) return null

  const criticals = visible.filter(a => a.level === 'critical')
  const warnings = visible.filter(a => a.level === 'warning')

  const renderAlert = (a, i, cls) => (
    <div key={`${cls}-${i}`} className={`alert-item ${cls}`}>
      <span className="alert-icon">⚠</span>
      <span className="alert-message">{a.message}</span>
      <button
        type="button"
        className="alert-dismiss"
        aria-label="Dismiss alert"
        onClick={() => dismiss(a.message)}
      >×</button>
    </div>
  )

  return (
    <div className="alert-banner">
      {criticals.map((a, i) => renderAlert(a, i, 'alert-critical'))}
      {warnings.map((a, i) => renderAlert(a, i, 'alert-warning'))}
    </div>
  )
}
