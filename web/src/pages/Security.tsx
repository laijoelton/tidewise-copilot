import { Pause, Play } from 'lucide-react'
import { useState } from 'react'
import { Badge, Loading, PageHead, Panel, TierBadge } from '../components/ui'
import { api, rm, type Json } from '../lib/api'
import { bump, useLoad } from '../lib/hooks'

const TIERS = [
  ['T0 · read', 'Look at books, forecast, analyse, check documents', 'Automatic'],
  ['T1 · notify', 'Templated messages to registered contacts only (reminders, B/L amendment)', 'Automatic, rate-limited'],
  ['T2 · execute', 'Pay approved bills to verified payees, small FX — inside owner limits', 'Automatic within limits'],
  ['T3 · approve', 'Larger payments, any credit commitment, renegotiating terms', 'Owner approval, step-up above threshold'],
  ['T4 · prohibited', 'New payees, changed bank details, unknown transfers, changing limits', 'Never — owner uses their bank'],
]

export default function Security() {
  const { data: policy, error } = useLoad(api.policy)
  const [report, setReport] = useState<Json | null>(null)
  const [busy, setBusy] = useState(false)
  if (!policy) return <Loading error={error} />
  const l = policy.limits

  return (
    <>
      <PageHead title="Security" sub="The agent proposes; deterministic policy decides. The model is never asked whether an action is safe, never sees raw external content, and cannot approve anything." />
      <KillSwitch paused={policy.paused} />

      <div className="mt-4 grid gap-4 xl:grid-cols-2">
        <Panel title="Permission tiers">
          <table className="data">
            <thead><tr><th>Tier</th><th>Scope</th><th>Who decides</th></tr></thead>
            <tbody>
              {TIERS.map(([t, scope, who], i) => (
                <tr key={t}><td><TierBadge tier={i} label={t} /></td><td className="text-ink2">{scope}</td><td className="text-ink3">{who}</td></tr>
              ))}
            </tbody>
          </table>
        </Panel>
        <Panel title="Owner limits (agent can read, never write)">
          <table className="data"><tbody>
            <tr><td>Auto-pay per payment</td><td className="num text-right">{rm(l.auto_pay_per_tx_myr)}</td></tr>
            <tr><td>Auto-pay per day</td><td className="num text-right">{rm(l.auto_pay_daily_myr)}</td></tr>
            <tr><td>Auto FX conversion</td><td className="num text-right">USD {l.auto_fx_usd.toLocaleString()}</td></tr>
            <tr><td>Step-up confirmation above</td><td className="num text-right">{rm(l.step_up_above_myr)}</td></tr>
            <tr><td>Reminder cooldown</td><td className="num text-right">{l.reminder_cooldown_days} days</td></tr>
            <tr><td>Autonomous actions per turn</td><td className="num text-right">{l.max_auto_actions_per_turn}</td></tr>
            <tr><td>Approval expiry</td><td className="num text-right">{l.approval_ttl_hours} h</td></tr>
          </tbody></table>
        </Panel>
      </div>

      <Panel className="mt-4" title="Red-team suite"
        action={<button className="btn btn-primary" disabled={busy} onClick={async () => { setBusy(true); setReport(await api.redteam()); setBusy(false) }}>
          <Play size={14} /> {busy ? 'Running…' : 'Run attacks now'}</button>}>
        {!report ? (
          <p className="text-ink3">Runs 23 attack scenarios — invoice-redirection fraud (incl. look-alike Unicode domains), hidden and non-English prompt injection, duplicate and split payments, data exfiltration, self-escalation, approval bypass, audit tampering invented numbers in model output, kill switch — each against a fresh copy of the real policy, sandbox and audit code.</p>
        ) : (
          <>
            <div className="mb-3 text-[15px]"><span className="num font-semibold text-ok">{report.passed}/{report.total}</span> attacks handled as expected</div>
            <table className="data">
              <thead><tr><th>ID</th><th>Attack</th><th>Category</th><th>Expected</th><th>Actual</th><th /></tr></thead>
              <tbody>
                {report.results.map((r: Json) => (
                  <tr key={r.id}>
                    <td className="num text-ink3">{r.id}</td><td>{r.name}</td><td className="text-ink3">{r.category}</td>
                    <td className="num text-[12px]">{r.expected}</td><td className="num text-[12px]">{r.actual}</td>
                    <td>{r.passed ? <Badge tone="good">pass</Badge> : <Badge tone="critical">fail</Badge>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
      </Panel>
    </>
  )
}

function KillSwitch({ paused }: { paused: boolean }) {
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  async function toggle() {
    setBusy(true); setErr(null)
    try { await api.setPaused(!paused); bump() } catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }
  return (
    <div className={`panel flex flex-wrap items-center gap-4 p-5 ${paused ? '!border-warn/50 !bg-warn/8' : ''}`}>
      <div className="flex-1">
        <h2 className="text-[18px]">{paused ? 'Autonomy is paused' : 'Kill switch'}</h2>
        <p className="mt-1 text-[13px] text-ink2">
          {paused
            ? 'Every action — even a reminder — now waits for your approval. Reading, forecasting and analysis still work.'
            : 'One click stops all autonomous actions. Tidewise keeps reading and proposing, but nothing runs until you approve it. Owner token required.'}
        </p>
        {err && <p className="mt-1 text-[12.5px] text-bad">{err} — set your owner token on the Approvals page.</p>}
      </div>
      <button className={paused ? 'btn btn-primary' : 'btn btn-danger !border-bad/40'} disabled={busy} onClick={toggle}>
        {paused ? <><Play size={14} /> Resume autonomy</> : <><Pause size={14} /> Pause all autonomy</>}
      </button>
    </div>
  )
}
