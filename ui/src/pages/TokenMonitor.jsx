import { useState } from 'react'
import { usePoll } from '../hooks/usePoll.js'
import { getBudgets, getUsage, resetWindow, setBudget } from '../api.js'
import { LAMINAR_URL } from '../config.js'
import { Panel, Table, Td, EmptyRow } from '../components/Table.jsx'
import { StatusBadge } from '../components/Badge.jsx'
import { fmtCost, fmtInt, pctCapped, pctReal, shortId } from '../lib/format.js'

const load = async () => {
  const [usage, budgets] = await Promise.all([getUsage(), getBudgets().catch(() => null)])
  return { usage, budgets }
}

const BAR = {
  exhausted: 'bg-rose-500',
  warn: 'bg-amber-500',
  ok: 'bg-brand-500',
}

export default function TokenMonitor() {
  const { data, loading, refresh } = usePoll(load, [])
  const usage = data?.usage || []
  const budgets = data?.budgets
  const overrides = budgets?.overrides || {}

  return (
    <div className="space-y-6">
      <Panel
        title="Token Monitor"
        subtitle="Per-agent token usage, budget state, and cost over the sliding window"
        right={
          <a
            href={LAMINAR_URL}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1.5 rounded-lg bg-brand-600 px-3 py-1.5 text-xs font-semibold text-white shadow-sm transition hover:bg-brand-700"
          >
            Open Laminar dashboard ↗
          </a>
        }
      >
        {budgets && (
          <div className="flex flex-wrap items-center gap-2 border-b border-slate-200 bg-slate-50 px-5 py-3 text-xs text-slate-500">
            <span>
              Window: <span className="font-medium text-brand-900">{Math.round((budgets.window_s || 0) / 60)}m</span>
            </span>
            <span className="text-slate-300">·</span>
            <span>
              Default: <span className="font-medium text-brand-900">{fmtInt(budgets.system_default)} tok</span>
            </span>
            {budgets.risk_defaults &&
              Object.entries(budgets.risk_defaults).map(([tier, val]) => (
                <span key={tier} className="text-slate-300">
                  · <span className="text-slate-500">{tier}</span>{' '}
                  <span className="font-medium text-brand-900">{fmtInt(val)}</span>
                </span>
              ))}
          </div>
        )}

        <Table
          columns={[
            'Agent',
            'Risk',
            'Window usage',
            'Budget (set)',
            'State',
            'Calls',
            'Last model',
            'Cost ($)',
            'Trace',
            '',
          ]}
        >
          {loading && !data ? (
            <EmptyRow colSpan={10}>Loading usage…</EmptyRow>
          ) : usage.length === 0 ? (
            <EmptyRow colSpan={10}>
              No agent has reported token usage yet (the ledger fills as agents run).
            </EmptyRow>
          ) : (
            usage.map((u) => {
              const state = u.budget?.state || 'ok'
              const ratio = u.budget?.ratio || 0
              const capped = pctCapped(ratio)
              const real = pctReal(ratio)
              return (
                <tr key={u.agent_id} className="hover:bg-slate-50">
                  <Td>
                    <div className="font-medium text-brand-900">{u.name}</div>
                    <div className="font-mono text-[11px] text-slate-400">{u.agent_id}</div>
                  </Td>
                  <Td>
                    <StatusBadge value={u.risk} />
                  </Td>
                  <Td>
                    <div className="flex items-center justify-between gap-3 text-xs">
                      <span className="text-slate-700">{fmtInt(u.window?.total_tokens)} tok</span>
                      <span
                        className={
                          real >= 100
                            ? 'font-medium text-rose-600'
                            : real >= 80
                              ? 'font-medium text-amber-600'
                              : 'text-slate-500'
                        }
                        title={`${real}% of budget`}
                      >
                        ({capped}%{real > 100 ? ` · ${real}%` : ''})
                      </span>
                    </div>
                    <div className="mt-1 h-1.5 w-44 overflow-hidden rounded-full bg-slate-200">
                      <div
                        className={`h-full rounded-full ${BAR[state] || BAR.ok}`}
                        style={{ width: `${capped}%` }}
                      />
                    </div>
                  </Td>
                  <Td>
                    <BudgetCell
                      agentId={u.agent_id}
                      current={u.budget?.budget_tokens}
                      hasOverride={overrides[u.agent_id] != null}
                      onDone={refresh}
                    />
                  </Td>
                  <Td>
                    <StatusBadge value={state} />
                  </Td>
                  <Td className="text-slate-700">{u.window?.calls ?? 0}</Td>
                  <Td>
                    {u.window?.last_model ? (
                      <span className="font-mono text-xs text-slate-700">{u.window.last_model}</span>
                    ) : (
                      <span className="text-slate-400">—</span>
                    )}
                  </Td>
                  <Td className="font-mono text-slate-700">{fmtCost(u.window?.cost)}</Td>
                  <Td>
                    {u.last_trace_id ? (
                      u.last_trace_url ? (
                        <a
                          href={u.last_trace_url}
                          target="_blank"
                          rel="noreferrer"
                          className="font-mono text-xs font-semibold text-brand-600 hover:underline"
                          title={u.last_trace_id}
                        >
                          {shortId(u.last_trace_id)} ↗
                        </a>
                      ) : (
                        <span className="font-mono text-xs text-slate-500" title={u.last_trace_id}>
                          {shortId(u.last_trace_id)}
                        </span>
                      )
                    ) : (
                      <span className="text-slate-400">—</span>
                    )}
                  </Td>
                  <Td>
                    <ResetButton agentId={u.agent_id} onDone={refresh} />
                  </Td>
                </tr>
              )
            })
          )}
        </Table>
      </Panel>
    </div>
  )
}

// Editable budget cell: shows the effective budget, and lets the operator set a
// new per-agent override (POST /laminar/budgets/{id}) or clear it back to the
// risk-tier default (tokens = 0).
function BudgetCell({ agentId, current, hasOverride, onDone }) {
  const [val, setVal] = useState('')
  const [busy, setBusy] = useState(false)
  const n = parseInt(val, 10)

  const apply = async (tokens) => {
    if (busy) return
    setBusy(true)
    try {
      await setBudget(agentId, tokens)
      setVal('')
      await onDone?.()
    } catch (e) {
      // surface failures without crashing the table
      // eslint-disable-next-line no-alert
      alert(`Set budget failed: ${e.message || e}`)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-1">
      <div className="flex items-baseline gap-1.5">
        <span className="font-medium text-brand-900">{fmtInt(current)}</span>
        {hasOverride && (
          <span
            className="text-[10px] font-semibold uppercase tracking-wide text-brand-600"
            title="a per-agent override is set"
          >
            override
          </span>
        )}
      </div>
      <form
        className="flex items-center gap-1"
        onSubmit={(e) => {
          e.preventDefault()
          if (n > 0) apply(n)
        }}
      >
        <input
          type="number"
          min="1"
          value={val}
          disabled={busy}
          onChange={(e) => setVal(e.target.value)}
          placeholder="new"
          className="w-16 rounded-md border border-slate-300 bg-white px-2 py-0.5 text-xs text-slate-700 placeholder:text-slate-400 focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500 disabled:opacity-50"
          title="Set a new tokens-per-window budget for this agent"
        />
        <button
          type="submit"
          disabled={busy || !(n > 0)}
          className="rounded-md bg-brand-600 px-2 py-0.5 text-xs font-medium text-white transition hover:bg-brand-700 disabled:opacity-50"
        >
          {busy ? '…' : 'Set'}
        </button>
        {hasOverride && (
          <button
            type="button"
            disabled={busy}
            onClick={() => apply(0)}
            className="text-[11px] text-slate-400 transition hover:text-brand-600 disabled:opacity-50"
            title="Clear the override — fall back to the risk-tier budget"
          >
            clear
          </button>
        )}
      </form>
    </div>
  )
}

function ResetButton({ agentId, onDone }) {
  const [busy, setBusy] = useState(false)
  return (
    <button
      disabled={busy}
      onClick={async () => {
        setBusy(true)
        try {
          await resetWindow(agentId)
          await onDone?.()
        } catch (e) {
          // surface failures without crashing the table
          // eslint-disable-next-line no-alert
          alert(`Reset failed: ${e.message || e}`)
        } finally {
          setBusy(false)
        }
      }}
      className="whitespace-nowrap rounded-md border border-slate-300 bg-white px-2.5 py-1 text-xs font-medium text-slate-600 transition hover:border-brand-500 hover:text-brand-700 disabled:opacity-50"
      title="Clear this agent's usage window"
    >
      {busy ? 'resetting…' : 'reset window'}
    </button>
  )
}
