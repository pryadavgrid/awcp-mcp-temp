// Left sidebar: brand, a "New chat" button, and the conversation history.
// History is whatever is in app state (persisted to localStorage) — it grows as
// the user starts conversations; nothing is hardcoded. On narrow screens it
// slides in as a drawer over a scrim (see styles.css).
export default function Sidebar({
  chats,
  activeChatId,
  onNewChat,
  onSelectChat,
  onDeleteChat,
  open,
  onClose,
}) {
  return (
    <>
      <div className={`scrim ${open ? 'show' : ''}`} onClick={onClose} />
      <aside className={`sidebar ${open ? 'open' : ''}`}>
        <div className="sb-head">
          <span className="sb-title">AWCP</span>
        </div>

        <button type="button" className="new-chat" onClick={onNewChat}>
          <span className="nc-plus">+</span> New chat
        </button>

        <div className="sb-section">Recents</div>
        <div className="history">
          {chats.length === 0 && <div className="hist-empty">No conversations yet</div>}
          {chats.map((c) => (
            <div
              key={c.id}
              className={`hist-item ${c.id === activeChatId ? 'active' : ''}`}
              onClick={() => onSelectChat(c.id)}
              title={c.title || 'New chat'}
            >
              <span className="hist-title">{c.title || 'New chat'}</span>
              <button
                type="button"
                className="hist-del"
                aria-label="Delete conversation"
                onClick={(e) => {
                  e.stopPropagation()
                  onDeleteChat(c.id)
                }}
              >
                ×
              </button>
            </div>
          ))}
        </div>
      </aside>
    </>
  )
}
