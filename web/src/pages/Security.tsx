import { Pause, Play } from 'lucide-react'
import { useState } from 'react'
import { AttackLab } from '../components/AttackLab'
import { Badge, Loading, PageHead, Panel, TierBadge } from '../components/ui'
import { api, rm, type Json } from '../lib/api'
import { bump, useLoad } from '../lib/hooks'

const TIERS = [
  ['T0 · read', 'Look at books, forecast, analyse, check documents', 'Automatic'],
  ['T1 · notify', 'Templated messages to registered contacts only: reminders, corrected invoices, deposit requests', 'Automatic, rate-limited'],
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
      <AttackLab />
      <div className="mt-4"><KillSwitch paused={policy.paused} /></div>

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

      <MlModels />

      <Panel className="mt-4" title="Red-team suite"
        action={<button className="btn btn-primary" disabled={busy} onClick={async () => { setBusy(true); setReport(await api.redteam()); setBusy(false) }}>
          <Play size={14} /> {busy ? 'Running…' : 'Run attacks now'}</button>}>
        {!report ? (
          <p className="text-ink3">Runs 28 attack scenarios — invoice-redirection fraud (incl. look-alike Unicode domains), hidden and non-English prompt injection, duplicate and split payments, data exfiltration, self-escalation, approval bypass, audit tampering, invented numbers in model output, kill switch, paraphrased fraud only ML catches, padded bills — each against a fresh copy of the real policy, sandbox and audit code.</p>
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

function MlModels() {
  const { data } = useLoad(api.ml)
  const pctx = (v?: number) => (v == null ? '—' : `${Math.round(v * 1000) / 10}%`)
  return (
    <Panel className="mt-4" title="Machine-learning risk models">
      <p className="-mt-2 mb-4 max-w-3xl text-[13px] text-ink2">
        Three models trained locally on synthetic data (fixed seed). Each adds a signal on top of the rules — they can quarantine
        or escalate, but can never make anything more permissive. Scores below are on held-out data the models never saw.
      </p>
      {!data ? <div className="text-ink3">Evaluating models…</div> : (
        <div className="grid gap-4 lg:grid-cols-3">
          <div className="rounded-xl border border-edge bg-canvas/50 p-4">
            <div className="eyebrow">Inbound threat classifier</div>
            <div className="serif mt-1 text-[16px] font-semibold">Fraud & prompt injection, EN / MS / ZH</div>
            <div className="mt-1 text-[12px] text-ink3">TF-IDF character n-grams + logistic regression</div>
            <table className="data mt-3 text-[12.5px]">
              <thead><tr><th>Held-out paraphrases</th><th className="text-right">Caught</th><th className="text-right">False alarms</th></tr></thead>
              <tbody>
                {(['rules_only', 'model_only', 'rules_plus_model'] as const).map((k) => (
                  <tr key={k} className={k === 'rules_plus_model' ? 'font-semibold' : ''}>
                    <td>{k === 'rules_only' ? 'Rules only' : k === 'model_only' ? 'Model only' : 'Rules + model (live)'}</td>
                    <td className="num text-right">{pctx(data.threat_classifier.detection_on_heldout[k].threat_recall)}</td>
                    <td className="num text-right">{pctx(data.threat_classifier.detection_on_heldout[k].false_alarm_rate)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="rounded-xl border border-edge bg-canvas/50 p-4">
            <div className="eyebrow">Payment anomaly detector</div>
            <div className="serif mt-1 text-[16px] font-semibold">Unusual amount for this payee</div>
            <div className="mt-1 text-[12px] text-ink3">Isolation Forest on each payee's own payment history</div>
            <div className="mt-4 grid grid-cols-2 gap-3">
              <div><div className="num text-[24px] font-semibold text-ok">{pctx(data.payment_anomaly.detection_rate)}</div><div className="text-[12px] text-ink3">anomalies caught</div></div>
              <div><div className="num text-[24px] font-semibold">{pctx(data.payment_anomaly.false_alarm_rate)}</div><div className="text-[12px] text-ink3">false alarms</div></div>
            </div>
            <p className="mt-3 text-[12px] text-ink3">An unusual payment is never auto-paid — it goes to your approvals with the reason.</p>
          </div>
          <div className="rounded-xl border border-edge bg-canvas/50 p-4">
            <div className="eyebrow">Collection-risk predictor</div>
            <div className="serif mt-1 text-[16px] font-semibold">Will this invoice be paid 30+ days late?</div>
            <div className="mt-1 text-[12px] text-ink3">Gradient boosting on a simulated SME receivables portfolio</div>
            <div className="mt-4 grid grid-cols-2 gap-3">
              <div><div className="num text-[24px] font-semibold text-ok">{data.collection_risk.roc_auc}</div><div className="text-[12px] text-ink3">ROC AUC</div></div>
              <div><div className="num text-[24px] font-semibold">{data.collection_risk.brier}</div><div className="text-[12px] text-ink3">Brier score</div></div>
            </div>
            <p className="mt-3 text-[12px] text-ink3">Top drivers: customer's own delay history, days overdue.</p>
          </div>
        </div>
      )}
    </Panel>
  )
}
