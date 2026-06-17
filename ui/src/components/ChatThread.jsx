import { useEffect, useRef } from 'react'
import Timeline from './Timeline.jsx'
import ResultPanel from './ResultPanel.jsx'

// Renders one conversation as a chat thread: user prompts on the right, agent
// turns on the left. While an agent turn is still working we show the live
// "thinking" indicator (Timeline); once it settles (or while awaiting approval)
// we show the ResultPanel — final answer, tools used, governed writes, Temporal
// link, and Approve/Deny. All of it comes from the per-message run state.
const ACTIVE = new Set(['queued', 'pending', 'running'])

export default function ChatThread({ messages, onApprove }) {
  const endRef = useRef(null)
  useEffect(() => {
    endRef.current && endRef.current.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [messages])

  return (
    <div className="thread">
      {messages.map((m, i) =>
        m.role === 'user' ? (
          <div className="msg user" key={m.id}>
            <div className="bubble u">
              {m.text && <div className="u-text">{m.text}</div>}
              {m.attachments && m.attachments.length > 0 && (
                <div className="msg-files">
                  {m.attachments.map((f) => (
                    <span className="file-chip" key={f.id}>
                      📎 {f.name}
                    </span>
                  ))}
                </div>
              )}
            </div>
          </div>
        ) : (
          <div className="msg assistant" key={m.id}>
            <div className="bubble a">
              {m.run && ACTIVE.has(m.run.status) && !m.run.result ? (
                <Timeline items={m.run.timeline} status={m.run.status} />
              ) : (
                <ResultPanel
                  status={m.run}
                  onApprove={onApprove}
                  title={(messages[i - 1] && messages[i - 1].text) || 'AWCP result'}
                />
              )}
            </div>
          </div>
        ),
      )}
      <div ref={endRef} />
    </div>
  )
}
