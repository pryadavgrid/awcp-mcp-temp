// Agent selector + the selected agent's live metadata. All values come from the
// gateway's /user/agents response, so this renders whatever agents exist.

export default function AgentPicker({ agents, selectedId, selected, onSelect, disabled }) {
  return (
    <div className="agent-pick">
      <label className="lbl">Agent</label>
      <select
        className="select"
        value={selectedId || ''}
        onChange={(e) => onSelect(e.target.value)}
        disabled={disabled}
      >
        <option value="" disabled>
          Select an agent…
        </option>
        {agents.map((a) => (
          <option key={a.id} value={a.id}>
            {a.id}
            {a.framework ? ` · ${a.framework}` : ''}
            {a.running ? ' · running' : ' · stopped'}
          </option>
        ))}
      </select>

      {selected && (
        <div className="agent-meta">
          <span className={`badge ${selected.running ? 'ok' : ''}`}>
            {selected.running ? `running :${selected.port}` : 'stopped'}
          </span>
          {selected.framework && <span className="badge">{selected.framework}</span>}
          {selected.model && <span className="badge">{selected.model}</span>}
          {(selected.tools || []).map((t) => (
            <span className="chip" key={t}>
              {t}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}
