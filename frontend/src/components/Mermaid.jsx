import { useEffect, useState } from 'react'

// Mermaid is heavy (~hundreds of KB), so it's dynamically imported the first time
// a diagram actually renders — vite splits it into its own chunk and the main
// dashboard bundle stays lean. The module + theme init are shared across diagrams.
let mermaidPromise = null
const loadMermaid = () => {
  if (!mermaidPromise) {
    mermaidPromise = import('mermaid').then((m) => {
      m.default.initialize({ startOnLoad: false, theme: 'dark', securityLevel: 'strict' })
      return m.default
    })
  }
  return mermaidPromise
}

// First non-empty line of an untagged block — used to recognize Mermaid when the
// model forgets the ```mermaid language hint (it frequently does). Matches the
// diagram-type keyword that every Mermaid source must open with.
const MERMAID_OPENER = /^\s*(?:%%\{[^}]*\}%%\s*)?(?:graph|flowchart|sequenceDiagram|classDiagram|stateDiagram(?:-v2)?|erDiagram|journey|gantt|pie|gitGraph|mindmap|timeline|quadrantChart|requirementDiagram|sankey-beta|xychart-beta|block-beta|C4Context)\b/

// A fenced ```mermaid block renders as <pre><code class="language-mermaid">…; pull
// the source text back off the rendered <code> child of a <pre>, or null if it
// isn't a mermaid block. When the block carries no language at all we fall back to
// sniffing the first line for a diagram keyword, so an untagged diagram still
// renders. Shared by every ReactMarkdown `pre` override.
export const mermaidSource = (children) => {
  const code = Array.isArray(children) ? children[0] : children
  if (code?.props?.children == null) return null
  const cls = code.props.className || ''
  const text = String(code.props.children).replace(/\n$/, '')
  if (/\blanguage-mermaid\b/.test(cls)) return text
  if (!cls && MERMAID_OPENER.test(text)) return text
  return null
}

// Rendered SVG keyed by exact source. ReactMarkdown hands us a fresh `pre`
// component identity on every parent render, so this subtree remounts often
// (every poll tick); the cache makes a remount of an already-rendered diagram
// instant and flicker-free instead of re-running the async render each time.
const svgCache = new Map()

// Render a mermaid source string to an inline SVG. The agent can emit invalid
// syntax, so on a parse/render failure we fall back to showing the raw source
// rather than throwing away the block.
export default function Mermaid({ chart }) {
  // Seed from cache synchronously so a remount paints the diagram immediately.
  const [svg, setSvg] = useState(() => svgCache.get(chart) || '')
  const [error, setError] = useState(false)

  useEffect(() => {
    const cached = svgCache.get(chart)
    if (cached) { setSvg(cached); setError(false); return }
    let cancelled = false
    setSvg('')
    setError(false)
    loadMermaid()
      .then((mermaid) => mermaid.render(`mmd-${Math.random().toString(36).slice(2)}`, chart))
      .then(({ svg }) => { svgCache.set(chart, svg); if (!cancelled) setSvg(svg) })
      .catch(() => { if (!cancelled) setError(true) })
    return () => { cancelled = true }
  }, [chart])

  if (error) return <pre className="mermaid-error">{chart}</pre>
  if (!svg) return <div className="mermaid-diagram mermaid-loading">rendering diagram…</div>
  return <div className="mermaid-diagram" dangerouslySetInnerHTML={{ __html: svg }} />
}
