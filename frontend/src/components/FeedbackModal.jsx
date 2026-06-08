import { useState } from 'react'

export default function FeedbackModal({ onClose }) {
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [done, setDone] = useState(false)

  async function handleSubmit(e) {
    e.preventDefault()
    if (!title.trim()) return
    setSubmitting(true)
    try {
      await fetch('/api/feedback', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: title.trim(), description: description.trim() }),
      })
      setDone(true)
      setTimeout(onClose, 1200)
    } catch {}
    setSubmitting(false)
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" style={{ maxWidth: 480 }} onClick={e => e.stopPropagation()}>
        <div className="modal-header">
          <div className="modal-title-group">
            <span className="modal-title">Submit Feedback</span>
          </div>
          <button className="modal-close" onClick={onClose}>✕</button>
        </div>

        {done ? (
          <div style={{ padding: '32px', textAlign: 'center', color: 'var(--green)', fontSize: 15 }}>
            ✓ Submitted!
          </div>
        ) : (
          <form className="feedback-form" onSubmit={handleSubmit}>
            <div>
              <div className="form-label">Title</div>
              <input className="form-input" value={title}
                onChange={e => setTitle(e.target.value)}
                placeholder="Brief summary..." maxLength={200} required />
            </div>
            <div>
              <div className="form-label">Description <span style={{ fontWeight: 400 }}>(optional)</span></div>
              <textarea className="form-textarea" value={description}
                onChange={e => setDescription(e.target.value)}
                placeholder="More detail..." rows={4} maxLength={2000} />
            </div>
            <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
              <button className="submit-btn" type="submit" disabled={submitting || !title.trim()}>
                {submitting ? 'Submitting…' : 'Submit'}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  )
}
