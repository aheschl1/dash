import { useCallback, useEffect, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import Mermaid, { mermaidSource } from './Mermaid'
import { authedFetch } from '../auth'

const ACTIVE_KEY = 'board-active-id'

// Render fenced ```mermaid blocks as diagrams; everything else as default markdown.
const MD_COMPONENTS = {
  pre: ({ node, children, ...props }) => {
    const mermaid = mermaidSource(children)
    return mermaid != null ? <Mermaid chart={mermaid} /> : <pre {...props}>{children}</pre>
  },
}

const COLS = [
  { key: 'todo', label: 'To investigate' },
  { key: 'looking', label: 'Looking' },
  { key: 'done', label: 'Resolved' },
]

const COLLAPSE_KEY = 'board-collapsed-cols'

export default function InvestigationBoard({ open, onClose, refreshKey, runProtected, activeBoardId, setActiveBoardId, onOpenConversation }) {
  const [boards, setBoards] = useState([])
  const [cards, setCards] = useState([])
  // Inline "create investigation" + "rename active investigation" editors.
  const [creating, setCreating] = useState(false)
  const [newTitle, setNewTitle] = useState('')
  const [renaming, setRenaming] = useState(false)
  const [renameTitle, setRenameTitle] = useState('')
  const [dragId, setDragId] = useState(null)
  // Where the dragged card would land: { col, beforeId } — drives the drop marker.
  const [over, setOver] = useState(null)
  // Collapsed status lanes (persisted) + per-card "engorged to full lane width" (session).
  const [collapsed, setCollapsed] = useState(() => {
    try { return new Set(JSON.parse(localStorage.getItem(COLLAPSE_KEY) || '[]')) }
    catch { return new Set() }
  })
  const [expanded, setExpanded] = useState(() => new Set())

  const toggleCollapsed = useCallback((col) => {
    setCollapsed((prev) => {
      const next = new Set(prev)
      next.has(col) ? next.delete(col) : next.add(col)
      try { localStorage.setItem(COLLAPSE_KEY, JSON.stringify([...next])) } catch { /* ignore */ }
      return next
    })
  }, [])

  const toggleExpanded = useCallback((id) => {
    setExpanded((prev) => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }, [])

  // Load cards for whichever investigation is currently selected.
  const load = useCallback(async () => {
    if (activeBoardId == null) { setCards([]); return }
    try {
      const r = await fetch(`/api/board?board_id=${activeBoardId}`)
      const d = await r.json()
      setCards(d.cards || [])
    } catch {
      setCards([])
    }
  }, [activeBoardId])

  // Load the investigation list; settle the active selection on a valid board.
  const loadBoards = useCallback(async () => {
    try {
      const r = await fetch('/api/boards')
      const d = await r.json()
      const list = d.boards || []
      setBoards(list)
      setActiveBoardId((cur) => {
        if (cur != null && list.some((b) => b.id === cur)) return cur
        const stored = Number(localStorage.getItem(ACTIVE_KEY))
        if (stored && list.some((b) => b.id === stored)) return stored
        return list.length ? list[0].id : null
      })
    } catch {
      setBoards([])
    }
  }, [setActiveBoardId])

  useEffect(() => {
    if (open) loadBoards()
  }, [open, refreshKey, loadBoards])

  useEffect(() => {
    if (open) load()
  }, [open, refreshKey, load])

  useEffect(() => {
    if (activeBoardId != null) {
      try { localStorage.setItem(ACTIVE_KEY, String(activeBoardId)) } catch { /* ignore */ }
    }
  }, [activeBoardId])

  const activeBoard = boards.find((b) => b.id === activeBoardId) || null

  const createBoard = useCallback(async () => {
    const title = newTitle.trim()
    if (!title) { setCreating(false); return }
    try {
      const r = await fetch('/api/boards', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title }),
      })
      const d = await r.json()
      setCreating(false); setNewTitle('')
      await loadBoards()
      if (d.board?.id) setActiveBoardId(d.board.id)
    } catch { /* ignore */ }
  }, [newTitle, loadBoards, setActiveBoardId])

  const renameBoard = useCallback(async () => {
    const title = renameTitle.trim()
    setRenaming(false)
    if (!title || !activeBoard || title === activeBoard.title) return
    try {
      await fetch(`/api/boards/${activeBoard.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title }),
      })
      await loadBoards()
    } catch { /* ignore */ }
  }, [renameTitle, activeBoard, loadBoards])

  // Deleting a whole investigation cascades its cards — admin-gated on the server,
  // so route through runProtected to attach the Bearer token.
  const deleteBoard = useCallback(() => {
    if (!activeBoard) return
    if (!window.confirm(`Delete investigation "${activeBoard.title}" and all its cards?`)) return
    runProtected?.(async () => {
      const r = await authedFetch(`/api/boards/${activeBoard.id}`, { method: 'DELETE' })
      if (r.ok) {
        setActiveBoardId(null)
        await loadBoards()
      }
    })
  }, [activeBoard, runProtected, loadBoards, setActiveBoardId])

  const byCol = (col) =>
    cards.filter((c) => c.col === col).sort((a, b) => a.pos - b.pos)

  const clearDrag = useCallback(() => { setDragId(null); setOver(null) }, [])

  // Drop onto a zone (optionally before a specific card) → midpoint pos.
  const drop = useCallback(async (col, beforeId) => {
    const id = dragId
    clearDrag()
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
  }, [dragId, cards, load, clearDrag])

  const remove = useCallback(async (id) => {
    setCards((cs) => cs.filter((c) => c.id !== id))
    try {
      await fetch(`/api/board/${id}`, { method: 'DELETE' })
    } finally {
      load()
    }
  }, [load])

  if (!open) return null

  return (
    <div className="board-overlay">
      <div className="board-head">
        <span className="board-title">Investigations</span>
        <div className="board-tabs">
          {boards.map((b) => (
            <button
              key={b.id}
              className={`board-tab${b.id === activeBoardId ? ' active' : ''}`}
              onClick={() => { setRenaming(false); setActiveBoardId(b.id) }}
              title={b.description || b.title}
            >
              {b.title}
              <span className="board-tab-count">{b.card_count}</span>
            </button>
          ))}
          {creating ? (
            <input
              className="board-tab-input"
              autoFocus
              value={newTitle}
              placeholder="Investigation name…"
              onChange={(e) => setNewTitle(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') createBoard()
                if (e.key === 'Escape') { setCreating(false); setNewTitle('') }
              }}
              onBlur={createBoard}
            />
          ) : (
            <button className="board-tab board-tab-new" onClick={() => setCreating(true)} title="New investigation">+</button>
          )}
        </div>
        <span className="board-count">{cards.length} pinned</span>
        {activeBoard && (
          renaming ? (
            <input
              className="board-tab-input"
              autoFocus
              value={renameTitle}
              onChange={(e) => setRenameTitle(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') renameBoard()
                if (e.key === 'Escape') setRenaming(false)
              }}
              onBlur={renameBoard}
            />
          ) : (
            <>
              <button className="board-head-btn" onClick={() => { setRenameTitle(activeBoard.title); setRenaming(true) }} title="Rename investigation">Rename</button>
              <button className="board-head-btn board-head-del" onClick={deleteBoard} title="Delete investigation">Delete</button>
            </>
          )
        )}
        <button className="board-close" onClick={onClose} aria-label="Close">×</button>
      </div>

      <div className="board-wall">
        {COLS.map((col) => {
          const zoneCards = byCol(col.key)
          const isOverZone = over?.col === col.key
          const isCollapsed = collapsed.has(col.key)
          return (
            <section
              className={`board-zone zone-${col.key}${isOverZone ? ' is-over' : ''}${isCollapsed ? ' is-collapsed' : ''}`}
              key={col.key}
              onDragOver={(e) => { e.preventDefault(); setOver({ col: col.key, beforeId: null }) }}
              onDrop={(e) => { e.preventDefault(); drop(col.key, over?.beforeId ?? null) }}
            >
              <header
                className="board-zone-head"
                onClick={() => toggleCollapsed(col.key)}
                title={isCollapsed ? 'Expand lane' : 'Collapse lane'}
              >
                <span className={`board-zone-chevron${isCollapsed ? ' collapsed' : ''}`}>▾</span>
                <span className="board-zone-dot" />
                <span className="board-zone-label">{col.label}</span>
                <span className="board-zone-count">{zoneCards.length}</span>
              </header>

              {!isCollapsed && (
              <div className="board-zone-cards">
                {zoneCards.length === 0 && (
                  <div className="board-zone-empty">Drag a card here</div>
                )}
                {zoneCards.map((c) => {
                  const markBefore = isOverZone && over?.beforeId === c.id
                  const isExpanded = expanded.has(c.id)
                  return (
                    <article
                      className={[
                        'board-card',
                        dragId === c.id ? 'dragging' : '',
                        markBefore ? 'drop-before' : '',
                        isExpanded ? 'expanded' : '',
                      ].filter(Boolean).join(' ')}
                      key={c.id}
                      draggable
                      onDragStart={() => setDragId(c.id)}
                      onDragEnd={clearDrag}
                      onDragOver={(e) => {
                        e.preventDefault(); e.stopPropagation()
                        setOver({ col: col.key, beforeId: c.id })
                      }}
                      onDrop={(e) => { e.stopPropagation(); e.preventDefault(); drop(col.key, c.id) }}
                    >
                      <div className="board-card-head">
                        {c.source_conv ? (
                          <button
                            className="board-card-src board-card-src-link"
                            title={`Open conversation: ${c.source_title || ''}`}
                            onClick={() => onOpenConversation?.(c.source_conv)}
                          >
                            {c.source_title || 'pinned'}
                          </button>
                        ) : (
                          <span className="board-card-src" title={c.source_title || ''}>
                            {c.source_title || 'pinned'}
                          </span>
                        )}
                        <button
                          className="board-card-expand"
                          onClick={() => toggleExpanded(c.id)}
                          aria-label={isExpanded ? 'Shrink' : 'Expand'}
                          title={isExpanded ? 'Shrink' : 'Expand'}
                        >{isExpanded ? '⤡' : '⤢'}</button>
                        <button
                          className="board-card-del"
                          onClick={() => remove(c.id)}
                          aria-label="Remove"
                        >×</button>
                      </div>
                      <div className="board-card-body agent-md">
                        <ReactMarkdown remarkPlugins={[remarkGfm]} components={MD_COMPONENTS}>{c.content}</ReactMarkdown>
                      </div>
                    </article>
                  )
                })}
              </div>
              )}
            </section>
          )
        })}
      </div>
    </div>
  )
}
