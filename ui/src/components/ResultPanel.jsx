import { md } from '../md.js'

// Final answer + governed writes + tools used + a deep link into Temporal. All
// fields are whatever the agent returned; tools/steps are not hardcoded.
export default function ResultPanel({ status, onApprove }) {
  if (!status) return null
  const {
    result,
    tools_used = [],
    steps = [],
    temporal_url,
    error,
    awaiting,
    status: s,
  } = status

  return (
    <div className="result-panel">
      {awaiting && s === 'awaiting_approval' && (
        <div className="approve">
          <span className="q">
            ⚠ Approval required: high-risk <b>{awaiting.action}</b>
            {awaiting.detail ? ` — ${awaiting.detail}` : ''}
          </span>
          <button className="btn ok" onClick={() => onApprove('approve')}>
            Approve
          </button>
          <button className="btn danger" onClick={() => onApprove('deny')}>
            Deny
          </button>
        </div>
      )}

      {error && <div className="err">{error}</div>}

      {result ? (
        <div className="result" dangerouslySetInnerHTML={{ __html: md(result) }} />
      ) : (
        <div className="muted">No result yet — it appears here when the agent finishes.</div>
      )}

      {tools_used.length > 0 && (
        <div className="block">
          <div className="lbl">Tools used</div>
          <div className="chips">
            {tools_used.map((t, i) => (
              <span className="chip" key={`${t}-${i}`}>
                {t}
              </span>
            ))}
          </div>
        </div>
      )}

      {steps.length > 0 && (
        <div className="block">
          <div className="lbl">Governed writes</div>
          <div className="gsteps">
            {steps.map((st, i) => (
              <div className="gstep" key={i}>
                <span className="mono">{st.action}</span>
                {st.risk && <span className={`rk rk-${st.risk}`}>{st.risk}</span>}
                <span className={`sstat ${st.status}`}>
                  {(st.status || '').replace('_', ' ')}
                </span>
                {st.info && <span className="ginfo">{st.info}</span>}
              </div>
            ))}
          </div>
        </div>
      )}

      {temporal_url && (
        <a className="tlink" href={temporal_url} target="_blank" rel="noreferrer">
          Open this run in Temporal ↗
        </a>
      )}
    </div>
  )
}
