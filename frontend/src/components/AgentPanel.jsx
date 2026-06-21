import { useState, useEffect, useRef, useCallback } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
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
const makeMdComponents = (rawContent, onPin) => {
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
    pre: ({ node, children, ...props }) => (
      <div className="pinnable">
        {pinButton('code')(node)}
        <pre {...props}>{children}</pre>
      </div>
    ),
    table: ({ node, children, ...props }) => (
      <div className="pinnable">
        {pinButton('table')(node)}
        <table {...props}>{children}</table>
      </div>
    ),
  }
}

// Right-edge assistant drawer. Collapsed it's a thin rail; expanded it pushes the
// dashboard left (via padding on .app). The top row lists past conversations
// (persisted in Postgres); each is a standard chat. Chats survive reloads and
// restarts — they're removed only by the explicit delete (×) button.
export default function AgentPanel({ open, setOpen, runProtected, pendingContext, clearPendingContext, onPinned }) {
  const [conversations, setConversations] = useState([])
  const [activeId, setActiveId] = useState(null)
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
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

  // Pin a code/table block from an answer onto the investigation board, tagged
  // with the conversation it came from, then open the board (via App).
  const pinBlock = useCallback(async ({ kind, content }) => {
    const id = activeIdRef.current
    const title = conversations.find(c => c.id === id)?.title || ''
    try {
      await fetch('/api/board', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ kind, content, source_conv: id, source_title: title }),
      })
      onPinned?.()
    } catch {}
  }, [conversations, onPinned])

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

  // First expand: adopt existing conversations, or start one if there are none.
  useEffect(() => {
    if (!open) return
    let cancelled = false
    ;(async () => {
      const cs = await loadConversations()
      if (cancelled) return
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

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight
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
      // A tool call ended the step: drop any streamed preamble (the persisted
      // render never shows assistant content that accompanies tool calls), then
      // add the chip.
      setMessages(m => [...m.filter(x => !(x.role === 'assistant' && x.streaming)), { role: 'tool', content: toolChipText(ev) }])
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
    }
  }, [fetchRendered, loadMessages, loadConversations])

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
        <span className="agent-rail-icon">💬</span>
        <span className="agent-rail-text">Assistant</span>
      </button>
    )
  }

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
        <span className="agent-title">Assistant</span>
        <button className="agent-collapse" onClick={() => setOpen(false)} title="Collapse" aria-label="Collapse">›</button>
      </div>

      <div className="agent-tabs">
        {conversations.map(c => (
          <div key={c.id} className={`agent-tab${c.id === activeId ? ' active' : ''}`}>
            <button
              className="agent-tab-label"
              onClick={() => selectConversation(c.id)}
              title={c.title || 'New chat'}
            >
              {c.title || 'New chat'}
            </button>
            <button
              className="agent-tab-del"
              onClick={() => deleteConversation(c.id)}
              title="Delete conversation"
              aria-label="Delete conversation"
            >
              ×
            </button>
          </div>
        ))}
        <button className="agent-tab-new" onClick={newConversation} title="New conversation" aria-label="New conversation">＋</button>
      </div>

      <div className="agent-messages" ref={scrollRef}>
        {messages.length === 0 && !sending && (
          <div className="agent-empty">Ask about the server — CPU, GPU, containers, disks, network…</div>
        )}
        {messages.map((m, i) => {
          if (m.role === 'tool') return <div key={i} className="agent-tool">→ {m.content}</div>
          if (m.role === 'note') return <div key={i} className="agent-note">{m.content}</div>
          if (m.role === 'reasoning') return (
            <details key={i} className="agent-reasoning" open={!!m.streaming}>
              <summary>Thinking</summary>
              <div className="agent-reasoning-body">{m.content}</div>
            </details>
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
            <div key={i} className="agent-msg agent-msg-assistant agent-md">
              <ReactMarkdown remarkPlugins={[remarkGfm]} components={makeMdComponents(m.content, pinBlock)}>{m.content}</ReactMarkdown>
            </div>
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
