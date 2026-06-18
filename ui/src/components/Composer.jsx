import { useRef } from 'react'
import AgentMenu from './AgentMenu.jsx'
import TokenRing from './TokenRing.jsx'

// The chat composer (Claude-style box):
//   • a growing textarea,
//   • bottom-left  → agent selector + circular token-usage ratio,
//   • bottom-right → "+" attach-file button + send button.
// No voice/audio control by design. Layout is fluid and works on any width.
export default function Composer({
  input,
  setInput,
  onSend,
  agents,
  selectedId,
  onSelectAgent,
  tokenInfo,
  attachments,
  onAddFiles,
  onRemoveAttachment,
  busy,
  running,
  disabled,
}) {
  const fileRef = useRef(null)
  const taRef = useRef(null)
  const blocked = busy || running
  const canSend = !blocked && !!selectedId && (input.trim() || attachments.length > 0)

  // Keep the textarea height in step with its content (no fixed rows).
  const autosize = (el) => {
    if (!el) return
    el.style.height = 'auto'
    el.style.height = Math.min(el.scrollHeight, 220) + 'px'
  }

  const handleKey = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      if (canSend) onSend()
    }
  }

  return (
    <div className="composer">
      {attachments.length > 0 && (
        <div className="attach-row">
          {attachments.map((f) => (
            <span className="attach-chip" key={f.id} title={f.name}>
              <span className="ac-name">📎 {f.name}</span>
              <button type="button" className="ac-x" onClick={() => onRemoveAttachment(f.id)} aria-label="Remove">
                ×
              </button>
            </span>
          ))}
        </div>
      )}

      <textarea
        ref={taRef}
        className="composer-input"
        placeholder={selectedId ? 'How can I help you today?' : 'Select an agent on the left to begin…'}
        value={input}
        rows={1}
        disabled={blocked}
        onChange={(e) => {
          setInput(e.target.value)
          autosize(e.target)
        }}
        onKeyDown={handleKey}
      />

      <div className="composer-bar">
        <div className="cb-left">
          <AgentMenu agents={agents} selectedId={selectedId} onSelect={onSelectAgent} disabled={blocked} />
          <TokenRing
            usage={tokenInfo.info}
            pending={tokenInfo.pending}
            agentName={(agents.find((a) => a.id === selectedId) || {}).name}
          />
        </div>

        <div className="cb-right">
          <input
            ref={fileRef}
            type="file"
            multiple
            hidden
            onChange={(e) => {
              onAddFiles(e.target.files)
              e.target.value = '' // allow re-selecting the same file
            }}
          />
          <button
            type="button"
            className="icon-btn plus"
            title="Attach files"
            disabled={blocked}
            onClick={() => fileRef.current && fileRef.current.click()}
          >
            +
          </button>
          <button
            type="button"
            className="icon-btn send"
            title="Send (Enter)"
            disabled={!canSend}
            onClick={onSend}
          >
            {busy ? <span className="spinner sm" /> : '↑'}
          </button>
        </div>
      </div>

      {disabled && <div className="composer-note">Can’t reach the gateway — start it, then it reconnects automatically.</div>}
    </div>
  )
}
