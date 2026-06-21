import { useCallback, useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

const COLS = [
  { key: 'todo', label: 'To investigate' },
  { key: 'looking', label: 'Looking' },
  { key: 'done', label: 'Resolved' },
]

export default function InvestigationBoard({ open, onClose, refreshKey }) {
  const [cards, setCards] = useState([])
  const [dragId, setDragId] = useState(null)
  const laneRef = useRef(null)

  const load = useCallback(async () => {
    try {
      const r = await fetch('/api/board')
      const d = await r.json()
      setCards(d.cards || [])
    } catch {
      setCards([])
    }
  }, [])

  useEffect(() => {
    if (open) load()
  }, [open, refreshKey, load])

  const byCol = (col) =>
    cards.filter((c) => c.col === col).sort((a, b) => a.pos - b.pos)

  // Drop onto a column (optionally before a specific card) → midpoint pos.
  const drop = useCallback(async (col, beforeId) => {
    const id = dragId
    setDragId(null)
    if (id == null) return
    const colCards = byCol(col).filter((c) => c.id !== id)
    let idx = colCards.length
    if (beforeId != null) {
      const i = colCards.findIndex((c) => c.id === beforeId)
      if (i !== -1) idx = i
    }
    const prev = idx > 0 ? colCards[idx - 1].pos : 0
    const next = idx < colCards.length ? colCards[idx].pos : (prev + 1)
    const pos = (prev + next) / 2

    setCards((cs) => cs.map((c) => (c.id === id ? { ...c, col, pos } : c)))
    try {
      await fetch(`/api/board/${id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ col, pos }),
      })
    } finally {
      load()
    }
  }, [dragId, cards, load])

  const remove = useCallback(async (id) => {
    setCards((cs) => cs.filter((c) => c.id !== id))
    try {
      await fetch(`/api/board/${id}`, { method: 'DELETE' })
    } finally {
      load()
    }
  }, [load])

  // Drag the empty lane to pan horizontally.
  const onLaneDown = useCallback((e) => {
    const lane = laneRef.current
    if (!lane || e.target !== lane) return
    const startX = e.clientX
    const startScroll = lane.scrollLeft
    const move = (ev) => { lane.scrollLeft = startScroll - (ev.clientX - startX) }
    const up = () => {
      window.removeEventListener('mousemove', move)
      window.removeEventListener('mouseup', up)
    }
    window.addEventListener('mousemove', move)
    window.addEventListener('mouseup', up)
  }, [])

  if (!open) return null

  return (
    <div className="board-overlay">
      <div className="board-head">
        <span className="board-title">Investigation Board</span>
        <span className="board-count">{cards.length} pinned</span>
        <button className="board-close" onClick={onClose} aria-label="Close">×</button>
      </div>

      <div className="board-lane" ref={laneRef} onMouseDown={onLaneDown}>
        {COLS.map((col) => (
          <div
            className="board-col"
            key={col.key}
            onDragOver={(e) => e.preventDefault()}
            onDrop={(e) => { e.preventDefault(); drop(col.key, null) }}
          >
            <div className="board-col-head">
              {col.label} · {byCol(col.key).length}
            </div>
            <div className="board-col-body">
              {byCol(col.key).map((c) => (
                <div
                  className="board-card"
                  key={c.id}
                  draggable
                  onDragStart={() => setDragId(c.id)}
                  onDragOver={(e) => e.preventDefault()}
                  onDrop={(e) => { e.stopPropagation(); e.preventDefault(); drop(col.key, c.id) }}
                >
                  <div className="board-card-head">
                    <span className="board-card-src" title={c.source_title || ''}>
                      {c.source_title || 'pinned'}
                    </span>
                    <button
                      className="board-card-del"
                      onClick={() => remove(c.id)}
                      aria-label="Remove"
                    >×</button>
                  </div>
                  <div className="board-card-body agent-md">
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>{c.content}</ReactMarkdown>
                  </div>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
