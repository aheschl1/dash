import { useState, useEffect, useCallback, useRef } from 'react'

const MAX_SNIPPET = 2000

// Collapse the messy whitespace a table/card selection produces into something
// readable, and cap the length so a "select all" doesn't dump the whole page.
const cleanSnippet = (s) =>
  s.replace(/[ \t]*\n[ \t]*/g, '\n').replace(/[ \t]{2,}/g, ' ').trim().slice(0, MAX_SNIPPET)

// Watch for a text selection anywhere on the dashboard and offer an "Ask about
// this" button anchored to it. Clicking hands the selected text to the agent
// (onAsk). Selections inside the agent panel itself are ignored so the assistant
// doesn't offer to ask about its own replies.
export default function AskSelection({ onAsk }) {
  const [anchor, setAnchor] = useState(null) // { x, y, text } | null
  const anchorRef = useRef(null)
  useEffect(() => { anchorRef.current = anchor }, [anchor])

  const refresh = useCallback(() => {
    const sel = window.getSelection()
    if (!sel || sel.isCollapsed || sel.rangeCount === 0) { setAnchor(null); return }
    const text = cleanSnippet(sel.toString())
    if (!text) { setAnchor(null); return }
    const node = sel.anchorNode
    const el = node && (node.nodeType === 1 ? node : node.parentElement)
    if (el && el.closest('.agent-panel')) { setAnchor(null); return }
    const rect = sel.getRangeAt(0).getBoundingClientRect()
    if (!rect || (rect.width === 0 && rect.height === 0)) { setAnchor(null); return }
    // Clamp to the viewport so a selection near an edge keeps the button visible.
    const x = Math.min(Math.max(rect.left + rect.width / 2, 60), window.innerWidth - 60)
    const y = Math.max(rect.top - 8, 8)
    setAnchor({ x, y, text })
  }, [])

  useEffect(() => {
    // mouseup covers desktop drags; selectionchange covers keyboard + mobile
    // long-press. Hide on scroll/resize since the anchor rect goes stale.
    const onSelChange = () => { if (!window.getSelection()?.toString().trim()) setAnchor(null) }
    const hide = () => setAnchor(null)
    document.addEventListener('mouseup', refresh)
    document.addEventListener('selectionchange', onSelChange)
    window.addEventListener('scroll', hide, true)
    window.addEventListener('resize', hide)
    return () => {
      document.removeEventListener('mouseup', refresh)
      document.removeEventListener('selectionchange', onSelChange)
      window.removeEventListener('scroll', hide, true)
      window.removeEventListener('resize', hide)
    }
  }, [refresh])

  if (!anchor) return null

  return (
    <button
      className="ask-selection-btn"
      style={{ left: anchor.x, top: anchor.y }}
      // mousedown (not click) so the button fires before the selection clears.
      onMouseDown={(e) => {
        e.preventDefault()
        const text = anchorRef.current?.text
        if (text) onAsk(text)
        window.getSelection()?.removeAllRanges()
        setAnchor(null)
      }}
    >
      Use Context
    </button>
  )
}
