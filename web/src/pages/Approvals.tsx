import { KeyRound } from 'lucide-react'
import { useState } from 'react'
import { GateTrace } from '../components/GateTrace'
import { Check, Empty, Loading, PageHead, Panel, TierBadge } from '../components/ui'
import { api, getOwnerToken, rm, setOwnerToken, type Json } from '../lib/api'
import { bump, useLoad } from '../lib/hooks'

export default function Approvals() {
  const { data, error } = useLoad(api.approvals)
  const [token, setToken] = useState(getOwnerToken())
  if (!data) return <Loading error={error} />
  const pending = data.filter((a: Json) => a.status === 'pending')
  const done = data.filter((a: Json) => a.status !== 'pending')

  return (
    <>
      <PageHead title="Approvals" sub="The owner channel. Nothing here can be triggered from chat or by anything the agent reads — each approval needs your owner token, and large amounts need you to re-type the figure." />

      <Panel className="mb-4" title={<span className="flex items-center gap-2"><KeyRound size={16} /> Owner token</span>}>
        <div className="flex flex-wrap items-center gap-2">
          <input className="input max-w-sm num" type="password" value={token} placeholder="printed by the API at start-up"
            onChange={(e) => setToken(e.target.value)} />
          <button className="btn" onClick={() => { setOwnerToken(token); bump() }}>Save for this session</button>
          <span className="text-[12px] text-ink3">Stored in this tab only (sessionStorage).</span>
        </div>
      </Panel>

      <div className="flex flex-col gap-4">
        {pending.length === 0 && <Empty>Nothing waiting for you. Ask the Copilot “What should I do this week?”</Empty>}
        {pending.map((a: Json) => <ApprovalCard key={a.id} a={a} />)}
      </div>

      {done.length > 0 && (
        <Panel className="mt-6" title="History">
          <table className="data">
            <thead><tr><th>ID</th><th>Action</th><th>Status</th><th>Receipt</th></tr></thead>
            <tbody>
              {done.map((a: Json) => (
                <tr key={a.id}>
                  <td className="num text-ink3">{a.id}</td>
                  <td>{a.title}</td>
                  <td className={a.status === 'approved' ? 'text-ok' : 'text-ink3'}>{a.status.replaceAll('_', ' ')}</td>
                  <td className="num text-ink3">{a.receipt?.ref || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
      )}
    </>
  )
}

function ApprovalCard({ a }: { a: Json }) {
  const [amount, setAmount] = useState('')
  const [msg, setMsg] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const stepUp = a.decision.step_up

  async function act(kind: 'approve' | 'reject') {
    setBusy(true)
    setMsg(null)
    try {
      if (kind === 'approve') await api.approve(a.id, stepUp ? Number(amount.replace(/[^\d.]/g, '')) : undefined)
      else await api.reject(a.id)
      bump()
    } catch (e) {
      setMsg((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="panel p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2"><TierBadge tier={a.decision.tier} label={a.decision.tier_label} /><span className="num text-[12px] text-ink3">{a.id}</span></div>
          <div className="mt-2 text-[16px] font-semibold">{a.title}</div>
          <div className="mt-1 text-[13px] text-ink2">Why: {a.action.rationale}</div>
        </div>
        <div className="num text-xl font-semibold">{rm(a.amount_myr)}</div>
      </div>
      <div className="mt-4"><GateTrace gates={a.gates} /></div>
      <details className="mt-3">
        <summary className="cursor-pointer text-[12.5px] text-ink3">All policy checks</summary>
        <div className="mt-2 space-y-1">{a.decision.checks.map((c: Json, i: number) => <Check key={i} {...c} />)}</div>
      </details>
      <div className="mt-4 flex flex-wrap items-center gap-2">
        {stepUp && (
          <input className="input num max-w-[220px]" value={amount} placeholder={`Re-type amount (${Math.round(a.amount_myr).toLocaleString()})`}
            onChange={(e) => setAmount(e.target.value)} aria-label="Confirm amount" />
        )}
        <button className="btn btn-primary" disabled={busy || (stepUp && !amount)} onClick={() => act('approve')}>Approve & execute in sandbox</button>
        <button className="btn btn-danger" disabled={busy} onClick={() => act('reject')}>Reject</button>
        {msg && <span className="text-[12.5px] text-bad">{msg}</span>}
      </div>
    </div>
  )
}
