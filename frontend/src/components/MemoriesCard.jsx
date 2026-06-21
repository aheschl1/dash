import { useCallback, useEffect, useState } from 'react'
import { authedFetch } from '../auth'

export default function MemoriesCard({ runProtected }) {
  const [memories, setMemories] = useState(null)
  const [draft, setDraft] = useState('')
  const [editing, setEditing] = useState(null)
  const [editText, setEditText] = useState('')
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    try {
      const r = await fetch('/api/memories')
      const d = await r.json()
      setMemories(d.memories || [])
    } catch {
      setMemories([])
    }
  }, [])

  useEffect(() => { load() }, [load])

  const add = useCallback(() => {
    const content = draft.trim()
    if (!content) return
    runProtected(async () => {
      setBusy(true)
      try {
        const r = await authedFetch('/api/memories', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ content }),
        })
        if (r.ok) {
          setDraft('')
          await load()
        }
      } finally {
        setBusy(false)
      }
    })
  }, [draft, runProtected, load])

  const startEdit = useCallback((m) => {
    setEditing(m.id)
    setEditText(m.content)
  }, [])

  const saveEdit = useCallback((id) => {
    const content = editText.trim()
    if (!content) return
    runProtected(async () => {
      setBusy(true)
      try {
        const r = await authedFetch(`/api/memories/${id}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ content }),
        })
        if (r.ok) {
          setEditing(null)
          await load()
        }
      } finally {
        setBusy(false)
      }
    })
  }, [editText, runProtected, load])

  const remove = useCallback((id) => {
    runProtected(async () => {
      setBusy(true)
      try {
        const r = await authedFetch(`/api/memories/${id}`, { method: 'DELETE' })
        if (r.ok) await load()
      } finally {
        setBusy(false)
      }
    })
  }, [runProtected, load])

  return (
    <div className="card">
      <div className="card-title">Agent Memories</div>

      <div className="memory-add">
        <textarea
          className="memory-input"
          rows={2}
          placeholder="Add a memory for the agent…"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
        />
        <button className="memory-btn" onClick={add} disabled={busy || !draft.trim()}>
          Add
        </button>
      </div>

      {!memories ? (
        <div className="loading">Loading…</div>
      ) : memories.length === 0 ? (
        <div className="empty">No memories yet</div>
      ) : (
        <div className="memory-list">
          {memories.map((m) => (
            <div className="memory-item" key={m.id}>
              <span className="memory-id">#{m.id}</span>
              {editing === m.id ? (
                <div className="memory-edit">
                  <textarea
                    className="memory-input"
                    rows={3}
                    value={editText}
                    onChange={(e) => setEditText(e.target.value)}
                  />
                  <div className="memory-actions">
                    <button
                      className="memory-btn"
                      onClick={() => saveEdit(m.id)}
                      disabled={busy || !editText.trim()}
                    >
                      Save
                    </button>
                    <button
                      className="memory-btn memory-btn-ghost"
                      onClick={() => setEditing(null)}
                      disabled={busy}
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              ) : (
                <>
                  <span className="memory-content">{m.content}</span>
                  <div className="memory-actions">
                    <button
                      className="memory-btn memory-btn-ghost"
                      onClick={() => startEdit(m)}
                      disabled={busy}
                    >
                      Edit
                    </button>
                    <button
                      className="memory-btn memory-btn-danger"
                      onClick={() => remove(m.id)}
                      disabled={busy}
                    >
                      Delete
                    </button>
                  </div>
                </>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
