import { useState, useEffect, useRef, useCallback, useMemo, memo } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import Mermaid, { mermaidSource } from './Mermaid'
import { getToken, clearToken } from '../auth'

const WIDTH_KEY = 'agentWidth'
const MIN_WIDTH = 320
// Desktop only; on mobile the panel goes full-width and the handle is hidden.
const clampWidth = (w) => Math.max(MIN_WIDTH, Math.min(w, Math.round(window.innerWidth * 0.85)))

// A tool_call event (live or replayed on resume) → the chip text in the transcript.
const toolChipText = (ev) => {
  const arg = ev.args && (ev.args.path ?? Object.values(ev.args)[0])
  return `${ev.name}${arg ? ' ' + arg : ''}`
}

// Append a streamed token to the trailing live bubble of `role`, or start one.
// Live bubbles carry `streaming: true` until the server sends the authoritative
// full event for the step (see finalizeStream).
const appendDelta = (m, role, chunk) => {
  const last = m[m.length - 1]
  if (last && last.role === role && last.streaming) {
    return [...m.slice(0, -1), { ...last, content: last.content + chunk }]
  }
  return [...m, { role, content: chunk, streaming: true }]
}

// Replace the in-flight streamed bubble of `role` with the authoritative content
// the server sends once the step completes (or append if nothing streamed).
const finalizeStream = (m, role, content) => {
  let found = false
  const next = m.map(x => {
    if (x.role === role && x.streaming) { found = true; return { role, content } }
    return x
  })
  return found ? next : [...next, { role, content }]
}

// Open links in a new tab; everything else renders with library defaults.
const MD_COMPONENTS = {
  a: ({ node, ...props }) => <a {...props} target="_blank" rel="noopener noreferrer" />,
}

// Like MD_COMPONENTS but overlays a pin on fenced code blocks (`pre`, never inline
// `code`) and tables. The pinned source is recovered by slicing the original
// message text via the node's remark position offsets — exact verbatim markdown,
// more faithful than rebuilding a table from the AST. Used only on answer bubbles.
const makeMdComponents = (rawContent, onPin, streaming) => {
  const pinButton = (kind) => (node) => {
    const p = node?.position
    if (!p || !onPin) return null
    return (
      <button
        type="button"
        className="pin-btn"
        title="Pin to investigation board"
        onClick={() => onPin({ kind, content: rawContent.slice(p.start.offset, p.end.offset) })}
      >📌</button>
    )
  }
  return {
    ...MD_COMPONENTS,
    pre: ({ node, children, ...props }) => {
      // Don't try to render a diagram from a half-streamed (and so usually invalid)
      // source — show it as plain code until the turn finalizes, then it renders once.
      const mermaid = streaming ? null : mermaidSource(children)
      return (
        <div className="pinnable">
          {pinButton('code')(node)}
          {mermaid != null ? <Mermaid chart={mermaid} /> : <pre {...props}>{children}</pre>}
        </div>
      )
    },
    table: ({ node, children, ...props }) => (
      <div className="pinnable">
        {pinButton('table')(node)}
        <table {...props}>{children}</table>
      </div>
    ),
  }
}

// One reasoning ("Thinking") bubble. Collapsed by default; the user opens it to
// follow along. While it's open and the step is still streaming, the body keeps its
// scroll pinned to the bottom so the newest tokens stay in view (the body itself
// scrolls once it passes its max-height). Also re-pins the moment it's opened.
const ReasoningBubble = memo(function ReasoningBubble({ content, streaming }) {
  const detailsRef = useRef(null)
  const bodyRef = useRef(null)
  const pin = () => {
    if (detailsRef.current?.open && bodyRef.current) {
      bodyRef.current.scrollTop = bodyRef.current.scrollHeight
    }
  }
  useEffect(() => { if (streaming) pin() }, [content, streaming])
  return (
    <details ref={detailsRef} className="agent-reasoning" onToggle={pin}>
      <summary>Thinking</summary>
      <div className="agent-reasoning-body" ref={bodyRef}>{content}</div>
    </details>
  )
})

// One rendered answer bubble. Memoized on (content, streaming) so an unrelated
// parent re-render (the dashboard polls every few seconds) doesn't rebuild the
// markdown component map — which, by handing ReactMarkdown a fresh `pre` identity,
// would remount and re-render every embedded Mermaid diagram and make them flicker.
const AssistantBubble = memo(function AssistantBubble({ content, streaming, onPin }) {
  const components = useMemo(
    () => makeMdComponents(content, onPin, streaming),
    [content, streaming, onPin],
  )
  return (
    <div className="agent-msg agent-msg-assistant agent-md">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>{content}</ReactMarkdown>
    </div>
  )
})

// Right-edge assistant drawer. Collapsed it's a thin rail; expanded it pushes the
// dashboard left (via padding on .app). The top row lists past conversations
// (persisted in Postgres); each is a standard chat. Chats survive reloads and
// restarts — they're removed only by the explicit delete (×) button.
export default function AgentPanel({ open, setOpen, runProtected, pendingContext, clearPendingContext, openConvId, clearOpenConv, fallbackBoardId, onScopeChange, onPinned }) {
  const [conversations, setConversations] = useState([])
  const [boards, setBoards] = useState([])
  const [activeId, setActiveId] = useState(null)
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  // The conversation sidebar (slide-in list) — replaces the old horizontal tabs.
  const [listOpen, setListOpen] = useState(false)
  // Text the user highlighted on the dashboard and chose to "ask about"; shown as
  // a chip above the input and folded into the next send (see send()).
  const [context, setContext] = useState(null)
  const [sending, setSending] = useState(false)
  const [width, setWidth] = useState(() => {
    const saved = parseInt(localStorage.getItem(WIDTH_KEY), 10)
    return clampWidth(Number.isFinite(saved) ? saved : 380)
  })
  const scrollRef = useRef(null)
  const wsRef = useRef(null)
  const activeIdRef = useRef(null)
  // conv_id of the turn this client is currently awaiting (its own send or a
  // resumed in-flight turn), so a reconnect can tell whether to keep waiting.
  const pendingConvRef = useRef(null)
  // conv_ids reconstructed from a resume; on 'done' we reload them from the DB
  // to replace the optimistic pending tail with the canonical persisted turn.
  const resumedRef = useRef(new Set())

  // The WS onmessage closure reads the live active id without rebinding.
  useEffect(() => { activeIdRef.current = activeId }, [activeId])

  // Collapsing the panel keeps the component mounted, so drop the sidebar too —
  // otherwise it'd still be open the next time the panel is expanded.
  useEffect(() => { if (!open) setListOpen(false) }, [open])

  const loadConversations = useCallback(async () => {
    try {
      const r = await fetch('/api/conversations')
      if (r.ok) {
        const d = await r.json()
        setConversations(d.conversations || [])
        return d.conversations || []
      }
    } catch {}
    return []
  }, [])

  const loadBoards = useCallback(async () => {
    try {
      const r = await fetch('/api/boards')
      if (r.ok) setBoards((await r.json()).boards || [])
    } catch {}
  }, [])

  useEffect(() => { if (open) loadBoards() }, [open, loadBoards])

  // The investigation this conversation is scoped to (drives where its pins and
  // the agent's pin/edit tools land). null = unscoped.
  const scopeBoardId = conversations.find(c => c.id === activeId)?.board_id ?? null

  // Report scope up so the board panel can default to the active chat's
  // investigation when it opens.
  useEffect(() => { onScopeChange?.(scopeBoardId) }, [scopeBoardId, onScopeChange])

  const setConversationScope = useCallback(async (boardId) => {
    if (!activeId) return
    try {
      await fetch(`/api/conversations/${activeId}/board`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ board_id: boardId }),
      })
      await loadConversations()
    } catch {}
  }, [activeId, loadConversations])

  // Pin a code/table block from an answer onto the investigation board, tagged
  // with the conversation it came from, then open the board (via App). Lands in
  // the conversation's scoped investigation, else whichever board the board panel
  // is showing.
  const pinBlock = useCallback(async ({ kind, content }) => {
    const id = activeIdRef.current
    const conv = conversations.find(c => c.id === id)
    const title = conv?.title || ''
    const boardId = conv?.board_id ?? fallbackBoardId ?? null
    try {
      const r = await fetch('/api/board', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ kind, content, source_conv: id, source_title: title, board_id: boardId }),
      })
      const d = await r.json().catch(() => ({}))
      onPinned?.(d.card?.board_id ?? boardId)
    } catch {}
  }, [conversations, fallbackBoardId, onPinned])

  // Fetch a conversation's rendered turns without touching state. Returns the
  // array, null on 404 (gone), or undefined on a transient error.
  const fetchRendered = useCallback(async (id) => {
    try {
      const r = await fetch(`/api/conversations/${id}`)
      if (r.ok) return (await r.json()).messages || []
      if (r.status === 404) return null
    } catch {}
    return undefined
  }, [])

  const loadMessages = useCallback(async (id) => {
    const msgs = await fetchRendered(id)
    if (msgs === null) {
      setConversations(cs => cs.filter(c => c.id !== id))
      setActiveId(null)
      setMessages([])
    } else if (msgs !== undefined) {
      setMessages(msgs)
    }
  }, [fetchRendered])

  const newConversation = useCallback(async () => {
    try {
      const r = await fetch('/api/newconversation', { method: 'POST' })
      if (r.ok) {
        const d = await r.json()
        setConversations(cs => [{ id: d.id, title: '', turns: 0 }, ...cs])
        setActiveId(d.id)
        setMessages([])
      }
    } catch {}
  }, [])

  // The panel is a full-screen fixed overlay on mobile; lock the underlying
  // document so swipes on its non-scrolling areas (header, tabs, composer) don't
  // scroll the dashboard behind it. The nested .agent-messages keeps its own
  // scroll (overscroll-behavior: contain stops it chaining out).
  useEffect(() => {
    if (!open) return
    document.body.classList.add('agent-panel-open')
    return () => document.body.classList.remove('agent-panel-open')
  }, [open])

  // First expand: adopt existing conversations, or start one if there are none.
  useEffect(() => {
    if (!open) return
    let cancelled = false
    ;(async () => {
      const cs = await loadConversations()
      if (cancelled) return
      // A specific conversation was requested (board card) — let that effect pick
      // it rather than defaulting to the first tab.
      if (openConvId != null && cs.some(c => c.id === openConvId)) return
      if (cs.length === 0) {
        newConversation()
      } else {
        setActiveId(prev => prev || cs[0].id)
        if (!activeId) loadMessages(cs[0].id)
      }
    })()
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  // Adopt a snippet handed in from the dashboard's "Ask about this". Attach it as
  // the pending context chip (the panel's open-effect already ensures a
  // conversation exists), then clear it upstream so reopening later doesn't
  // re-attach a stale selection.
  useEffect(() => {
    if (pendingContext == null) return
    setContext(pendingContext)
    clearPendingContext()
  }, [pendingContext, clearPendingContext])

  // Auto-scroll to the newest tokens, but only if the user is already near the
  // bottom — otherwise we'd yank them back down while they scroll up to read.
  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80
    if (nearBottom) el.scrollTop = el.scrollHeight
  }, [messages, sending])

  // Drive both the panel width and the .app right-padding off one CSS var on :root.
  useEffect(() => {
    document.documentElement.style.setProperty('--agent-width', `${width}px`)
  }, [width])

  // Drag the left edge to resize. Width = distance from the cursor to the right
  // edge of the viewport, clamped. Persist on release.
  const startResize = useCallback((e) => {
    e.preventDefault()
    document.body.classList.add('agent-resizing')
    const onMove = (ev) => {
      const x = ev.touches ? ev.touches[0].clientX : ev.clientX
      setWidth(clampWidth(window.innerWidth - x))
    }
    const onUp = () => {
      document.body.classList.remove('agent-resizing')
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
      window.removeEventListener('touchmove', onMove)
      window.removeEventListener('touchend', onUp)
      setWidth(w => { localStorage.setItem(WIDTH_KEY, String(w)); return w })
    }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
    window.addEventListener('touchmove', onMove, { passive: false })
    window.addEventListener('touchend', onUp)
  }, [])

  const selectConversation = useCallback((id) => {
    setActiveId(id)
    loadMessages(id)
  }, [loadMessages])

  // Open a specific conversation requested from elsewhere (a board card's source).
  // Ensure the conversation list is loaded so the requested tab exists, then select
  // it; loadMessages handles the case where it was since deleted (404).
  useEffect(() => {
    if (!open || openConvId == null) return
    let cancelled = false
    ;(async () => {
      let cs = conversations
      if (!cs.some(c => c.id === openConvId)) cs = await loadConversations()
      if (cancelled) return
      if (cs.some(c => c.id === openConvId)) selectConversation(openConvId)
      clearOpenConv()
    })()
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, openConvId])

  const deleteConversation = useCallback(async (id) => {
    try { await fetch(`/api/end/${id}`, { method: 'DELETE' }) } catch {}
    const remaining = conversations.filter(c => c.id !== id)
    setConversations(remaining)
    if (activeId === id) {
      const next = remaining[0]
      if (next) { setActiveId(next.id); loadMessages(next.id) }
      else { setActiveId(null); setMessages([]) }
    }
  }, [conversations, activeId, loadMessages])

  // Route one server event into the transcript. Events for a conversation other
  // than the active one are dropped from the UI (the turn still persists server
  // side). `resume`/`idle` come back from a subscribe after a (re)connect.
  const handleEvent = useCallback(async (ev) => {
    if (ev.conv_id && ev.conv_id !== activeIdRef.current) return
    if (ev.type === 'tool_call') {
      // A tool call ended the step: any streamed assistant text is narration the
      // model emitted before calling the tool (some models, e.g. Gemma, do this).
      // Keep it as a `preamble` above the chip (rendered like a full answer) rather
      // than dropping it — the backend `_render` surfaces the same preamble on reload.
      setMessages(m => [
        ...m
          .map(x => (x.role === 'assistant' && x.streaming)
            ? { role: 'preamble', content: x.content }
            : x)
          .filter(x => !(x.role === 'preamble' && !x.content.trim())),
        { role: 'tool', content: toolChipText(ev) },
      ])
    } else if (ev.type === 'answer_delta') {
      setMessages(m => appendDelta(m, 'assistant', ev.content))
    } else if (ev.type === 'reasoning_delta') {
      setMessages(m => appendDelta(m, 'reasoning', ev.content))
    } else if (ev.type === 'reasoning') {
      setMessages(m => finalizeStream(m, 'reasoning', ev.content))
    } else if (ev.type === 'answer') {
      setMessages(m => finalizeStream(m, 'assistant', ev.content))
    } else if (ev.type === 'error') {
      setMessages(m => [...m.filter(x => !x.streaming), { role: 'assistant', content: `Error: ${ev.error}` }])
    } else if (ev.type === 'canceled') {
      // Partial streamed text wasn't persisted; drop it so the bubble matches the
      // canonical (cancelled) turn.
      setMessages(m => [...m.filter(x => !x.streaming), { role: 'note', content: 'Stopped.' }])
    } else if (ev.type === 'approval_request') {
      setMessages(m => [...m, { role: 'approval', approval_id: ev.approval_id, kind: ev.kind, summary: ev.summary, detail: ev.detail, status: 'pending' }])
    } else if (ev.type === 'approval_unauthorized') {
      // Server rejected the token (missing/expired). Drop it so a retry re-prompts
      // login, and revert the optimistically-approved bubble back to pending.
      clearToken()
      setMessages(m => m.map(x =>
        (x.role === 'approval' && x.approval_id === ev.approval_id)
          ? { ...x, status: 'pending' }
          : x
      ))
    } else if (ev.type === 'resume') {
      // Rejoined a turn that's still running (reload/drop mid-turn). Rebuild the
      // pending tail authoritatively: persisted prior turns + the pending user
      // message + the tool calls already dispatched. Live events continue after.
      pendingConvRef.current = ev.conv_id
      resumedRef.current.add(ev.conv_id)
      setSending(true)
      const prior = await fetchRendered(ev.conv_id)
      if (ev.conv_id !== activeIdRef.current) return
      const base = Array.isArray(prior) ? prior : []
      const chips = (ev.events || []).map(e =>
        e.type === 'reasoning'
          ? { role: 'reasoning', content: e.content }
          : { role: 'tool', content: toolChipText(e) })
      const approvals = (ev.approvals || []).map(a => ({ role: 'approval', approval_id: a.approval_id, kind: a.kind, summary: a.summary, detail: a.detail, status: 'pending' }))
      setMessages([...base, { role: 'user', content: ev.user_message }, ...chips, ...approvals])
    } else if (ev.type === 'idle') {
      // Nothing running. If we were awaiting this conv, its turn finished while we
      // were disconnected — pull the persisted result.
      if (pendingConvRef.current === ev.conv_id) {
        pendingConvRef.current = null
        setSending(false)
        loadMessages(ev.conv_id)
      }
    } else if (ev.type === 'done') {
      setSending(false)
      pendingConvRef.current = null
      if (resumedRef.current.delete(ev.conv_id)) loadMessages(ev.conv_id)
      loadConversations()
      // The agent may have scoped this conversation or created a new investigation
      // mid-turn; refresh both so the scope dropdown shows the new value AND has an
      // option for a freshly-created board.
      loadBoards()
      // The titling sidecar names a conversation asynchronously after the turn
      // finishes (non-blocking), so the new title isn't ready at `done`. Refresh
      // once more shortly after to pick it up.
      setTimeout(loadConversations, 4000)
    }
  }, [fetchRendered, loadMessages, loadConversations, loadBoards])

  // Hold a WebSocket open while the panel is open, reconnecting on drop. On every
  // (re)connect, subscribe to the active conversation so an in-flight turn that
  // outlived the old socket is rejoined and its answer still lands in the UI.
  useEffect(() => {
    if (!open) return
    let cancelled = false
    let retry = null
    const connect = () => {
      let ws
      try {
        const proto = location.protocol === 'https:' ? 'wss' : 'ws'
        ws = new WebSocket(`${proto}://${location.host}/api/ws/agent`)
      } catch { return }
      wsRef.current = ws
      ws.onopen = () => {
        const id = activeIdRef.current
        if (id) ws.send(JSON.stringify({ conv_id: id, subscribe: true }))
      }
      ws.onmessage = (e) => {
        let ev
        try { ev = JSON.parse(e.data) } catch { return }
        handleEvent(ev)
      }
      ws.onerror = () => { try { ws.close() } catch {} }
      ws.onclose = () => {
        if (wsRef.current === ws) wsRef.current = null
        if (!cancelled) retry = setTimeout(connect, 1500)
      }
    }
    connect()
    return () => {
      cancelled = true
      if (retry) clearTimeout(retry)
      const ws = wsRef.current
      wsRef.current = null
      if (ws) { ws.onclose = null; try { ws.close() } catch {} }
    }
  }, [open, handleEvent])

  // When the active conversation changes (tab switch / adoption) and the socket
  // is already open, subscribe so we rejoin any turn in flight on it. (A fresh
  // connect is handled by onopen above.) Re-subscribing is safe: a resume rebuilds
  // the tail rather than appending, so duplicates can't accumulate.
  useEffect(() => {
    if (!activeId) return
    // Reflect whether this conversation has a turn in flight; a resume/idle reply
    // to the subscribe below will correct it once the server responds.
    setSending(pendingConvRef.current === activeId)
    const ws = wsRef.current
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ conv_id: activeId, subscribe: true }))
    }
  }, [activeId])

  // POST fallback for when the WebSocket isn't connected.
  const sendViaPost = useCallback(async (id, text) => {
    try {
      const r = await fetch(`/api/send/${id}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text }),
      })
      if (r.ok) {
        const d = await r.json()
        setMessages(m => [...m, { role: 'assistant', content: d.answer }])
      } else if (r.status === 404) {
        setMessages(m => [...m, { role: 'assistant', content: '(conversation expired — start a new one with ＋)' }])
      } else if (r.status === 409) {
        setMessages(m => [...m, { role: 'assistant', content: '(still working on the previous message — try again in a moment)' }])
      } else {
        const e = await r.json().catch(() => ({}))
        setMessages(m => [...m, { role: 'assistant', content: `Error: ${e.error || r.statusText}` }])
      }
    } catch (e) {
      setMessages(m => [...m, { role: 'assistant', content: `Error: ${e.message}` }])
    } finally {
      setSending(false)
      pendingConvRef.current = null
      loadConversations()
    }
  }, [loadConversations])

  const send = useCallback(async () => {
    const text = input.trim()
    if ((!text && !context) || !activeId || sending) return
    // Fold a highlighted snippet into the message: the model gets the preamble +
    // quote, but since we send (and optimistically render) the composed text
    // verbatim, the live bubble and the reloaded/persisted bubble stay identical.
    const composed = context
      ? `Context highlighted from the dashboard (a static snapshot — use your tools for live values if needed):\n\n> ${context.replace(/\n/g, '\n> ')}\n\n${text || 'What does this mean?'}`
      : text
    setInput('')
    setContext(null)
    setMessages(m => [...m, { role: 'user', content: composed }])
    setSending(true)
    pendingConvRef.current = activeId
    const ws = wsRef.current
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ conv_id: activeId, message: composed }))
      // setSending(false) + refresh happen on the 'done' event.
    } else {
      sendViaPost(activeId, composed)
    }
  }, [input, context, activeId, sending, sendViaPost])

  // Stop the in-flight turn: ask the server to cancel it over the WS. The agent
  // loop unwinds at its next safe point and the server emits canceled + done, which
  // clears the spinner; no optimistic state change here. Needs no auth.
  const stop = useCallback(() => {
    const ws = wsRef.current
    const id = activeIdRef.current
    if (ws && ws.readyState === WebSocket.OPEN && id) {
      ws.send(JSON.stringify({ conv_id: id, cancel: true }))
    }
  }, [])

  // Answer a gated-tool approval prompt (root_bash / post) over the WS and mark the
  // bubble resolved. Approving runs a privileged action, so it's gated behind admin
  // login (runProtected prompts for it and replays on success) and carries the Bearer
  // token the server re-verifies; denying needs no auth.
  const sendApproval = useCallback((approvalId, approved) => {
    const ws = wsRef.current
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ conv_id: activeIdRef.current, approval_id: approvalId, approved, token: getToken() }))
    }
    setMessages(m => m.map(x =>
      (x.role === 'approval' && x.approval_id === approvalId)
        ? { ...x, status: approved ? 'approved' : 'denied' }
        : x
    ))
  }, [])

  const respondApproval = useCallback((approvalId, approved) => {
    if (approved) runProtected(() => sendApproval(approvalId, true))
    else sendApproval(approvalId, false)
  }, [runProtected, sendApproval])

  if (!open) {
    return (
      <button className="agent-rail" onClick={() => setOpen(true)} title="Open assistant" aria-label="Open assistant">
        <span className="agent-rail-text">Assistant</span>
      </button>
    )
  }

  const activeTitle =
    conversations.find(c => c.id === activeId)?.title || (activeId ? 'New chat' : 'Assistant')

  return (
    <aside className="agent-panel">
      <div
        className="agent-resize"
        onMouseDown={startResize}
        onTouchStart={startResize}
        title="Drag to resize"
        role="separator"
        aria-orientation="vertical"
      />
      <div className="agent-head">
        <button
          className="agent-menu"
          onClick={() => setListOpen(v => !v)}
          title="Conversations"
          aria-label="Conversations"
          aria-expanded={listOpen}
        >☰</button>
        <span className="agent-title" title={activeTitle}>{activeTitle}</span>
        <button
          className="agent-newchat"
          onClick={() => { newConversation(); setListOpen(false) }}
          title="New conversation"
          aria-label="New conversation"
        >＋</button>
        <button className="agent-collapse" onClick={() => setOpen(false)} title="Collapse" aria-label="Collapse">›</button>
      </div>

      {activeId && (
        <div className="agent-scope" title="Investigation this conversation is scoped to — its pins and the agent's pin/edit actions land here">
          <span className="agent-scope-label">Investigation</span>
          <select
            className="agent-scope-select"
            value={scopeBoardId ?? ''}
            onChange={(e) => setConversationScope(e.target.value === '' ? null : Number(e.target.value))}
          >
            <option value="">None</option>
            {boards.map(b => (
              <option key={b.id} value={b.id}>{b.title}</option>
            ))}
          </select>
        </div>
      )}

      {listOpen && (
        <>
          <div className="agent-drawer-backdrop" onClick={() => setListOpen(false)} />
          <div className="agent-drawer" role="dialog" aria-label="Conversations">
            <div className="agent-drawer-head">
              <span>Conversations</span>
              <button
                className="agent-drawer-new"
                onClick={() => { newConversation(); setListOpen(false) }}
              >＋ New</button>
            </div>
            <div className="agent-drawer-list">
              {conversations.length === 0 && (
                <div className="agent-drawer-empty">No conversations yet</div>
              )}
              {conversations.map(c => (
                <div key={c.id} className={`agent-conv${c.id === activeId ? ' active' : ''}`}>
                  <button
                    className="agent-conv-label"
                    onClick={() => { selectConversation(c.id); setListOpen(false) }}
                    title={c.title || 'New chat'}
                  >
                    <span className="agent-conv-title">{c.title || 'New chat'}</span>
                    <span className="agent-conv-meta">{c.turns || 0} turn{c.turns === 1 ? '' : 's'}</span>
                  </button>
                  <button
                    className="agent-conv-del"
                    onClick={() => deleteConversation(c.id)}
                    title="Delete conversation"
                    aria-label="Delete conversation"
                  >×</button>
                </div>
              ))}
            </div>
          </div>
        </>
      )}

      <div className="agent-messages" ref={scrollRef}>
        {messages.length === 0 && !sending && (
          <div className="agent-empty">Ask about the server — CPU, GPU, containers, disks, network…</div>
        )}
        {messages.map((m, i) => {
          if (m.role === 'tool') return <div key={i} className="agent-tool">→ {m.content}</div>
          if (m.role === 'note') return <div key={i} className="agent-note">{m.content}</div>
          // Narration the model emitted before a tool call. Rendered identically to
          // a final answer (full markdown, pins, Mermaid) — models sometimes put
          // important content here.
          if (m.role === 'preamble') return (
            <AssistantBubble key={i} content={m.content} streaming={false} onPin={pinBlock} />
          )
          if (m.role === 'reasoning') return (
            <ReasoningBubble key={i} content={m.content} streaming={m.streaming} />
          )
          if (m.role === 'approval') return (
            <div key={i} className={`agent-approval agent-approval-${m.status}`}>
              <div className="agent-approval-head">{m.kind === 'post' ? 'Send this request?' : 'Run this command as root?'}</div>
              <pre className="agent-approval-cmd">{m.detail ?? m.command}</pre>
              {m.status === 'pending'
                ? <div className="agent-approval-actions">
                    <button className="agent-approve" onClick={() => respondApproval(m.approval_id, true)}>Approve</button>
                    <button className="agent-deny" onClick={() => respondApproval(m.approval_id, false)}>Deny</button>
                  </div>
                : <div className="agent-approval-status">{m.status === 'approved' ? '✓ Approved' : '✕ Denied'}</div>}
            </div>
          )
          if (m.role === 'assistant') return (
            <AssistantBubble key={i} content={m.content} streaming={m.streaming} onPin={pinBlock} />
          )
          return <div key={i} className={`agent-msg agent-msg-${m.role}`}>{m.content}</div>
        })}
        {sending && !(messages.length && messages[messages.length - 1].streaming) && (
          <div className="agent-msg agent-msg-assistant agent-typing">…</div>
        )}
      </div>

      {context && (
        <div className="agent-context">
          <span className="agent-context-quote">“{context}”</span>
          <button
            className="agent-context-clear"
            onClick={() => setContext(null)}
            title="Remove highlighted context"
            aria-label="Remove highlighted context"
          >
            ×
          </button>
        </div>
      )}

      <form className="agent-input" onSubmit={e => { e.preventDefault(); send() }}>
        <textarea
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() } }}
          placeholder={context ? 'Ask about the highlighted text…' : 'Ask the assistant…'}
          rows={2}
        />
        {sending
          ? <button type="button" className="agent-stop" onClick={stop} title="Stop the assistant">Stop</button>
          : <button type="submit" disabled={!input.trim() && !context}>Send</button>}
      </form>
    </aside>
  )
}
