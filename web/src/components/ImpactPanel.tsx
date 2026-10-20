import { ArrowRight, Sparkles } from 'lucide-react'
import { api, rm, type Json } from '../lib/api'
import { useLoad, type Page } from '../lib/hooks'
import { Panel, cx } from './ui'

const pct = (p: number) => `${p < 0.1 && p > 0 ? (p * 100).toFixed(1) : Math.round(p * 100)}%`

function Tile({ label, value, tone, children }: { label: string; value: string; tone?: 'ok' | 'bad'; children: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-edge bg-canvas/50 p-4">
      <div className="eyebrow">{label}</div>
      <div className={cx('num mt-1.5 text-[24px] font-semibold tracking-tight', tone === 'ok' && 'text-ok', tone === 'bad' && 'text-bad')}>{value}</div>
      <div className="mt-1 text-[12.5px] text-ink3">{children}</div>
    </div>
  )
}

/** What Tidewise has done for the owner: money, risk, fraud and time, from the session's own records. */
export function ImpactPanel({ go }: { go: (p: Page) => void }) {
  const { data: d } = useLoad(api.impact)
  if (!d) return null
  const fast = d.cost.fast_cash

  if (!d.acted) {
    return (
      <Panel className="mt-4" title={<span className="inline-flex items-center gap-2"><Sparkles size={17} /> What Tidewise will do for you</span>}>
        <div className="flex flex-wrap items-center gap-4">
          <p className="max-w-3xl text-[13.5px] text-ink2">
            Nothing done yet. Covering this {rm(d.cash.gap_before)} gap the fast way, with a {fast?.name.toLowerCase() ?? 'cash advance'},
            would cost <b className="text-bad">{fast ? rm(fast.cost) : '—'}</b>{fast?.apr ? ` (${Math.round(fast.apr * 100)}% APR)` : ''}.
            Ask Tidewise and this panel shows what it actually saved.
          </p>
          <button className="btn btn-primary ml-auto" onClick={() => go('copilot')}>Ask Tidewise what to do <ArrowRight size={14} /></button>
        </div>
      </Panel>
    )
  }

  const f = d.fraud
  const sooner = d.receivables_sooner[0]
  return (
    <Panel className="mt-4" title={<span className="inline-flex items-center gap-2"><Sparkles size={17} /> What Tidewise has done for you</span>}
      action={<button className="text-[12.5px] text-brand hover:underline" onClick={() => go('activity')}>see every step in the audit trail →</button>}>
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Tile label="Cash crisis" value={`${rm(d.cash.gap_closed)} closed`} tone={d.cash.gap_now <= 0 ? 'ok' : undefined}>
          chance of breaching your floor {pct(d.cash.risk_before)} → <b className="text-ink2">{pct(d.cash.risk_now)}</b>
          {sooner && <> · {sooner.invoice} lands ~{sooner.days_sooner} days sooner</>}
        </Tile>
        <Tile label="Cost of the fix" value={rm(d.cost.spent)}>
          {fast ? <>vs <span className="text-bad">{rm(fast.cost)}</span> for a {fast.name.toLowerCase()} ({Math.round(fast.apr * 100)}% APR): <b className="text-ok">saved {rm(d.cost.saved_vs_fast_cash)}</b></> : 'supplier surcharge and financing fees'}
        </Tile>
        <Tile label="Fraud blocked" value={rm(f.blocked_myr)} tone="ok">
          {f.items.length} scam email{f.items.length === 1 ? '' : 's'} quarantined
          {f.risky_orders_declined.length > 0 && <> · {f.risky_orders_declined.length} risky order declined ({f.risky_orders_declined[0].currency} {f.risky_orders_declined[0].amount.toLocaleString()})</>}
          {f.attack_lab_attempts > 0 && <> · {f.attack_lab_attempts} live attack{f.attack_lab_attempts === 1 ? '' : 's'} stopped</>}
        </Tile>
        <Tile label="Work done for you" value={`${d.work.actions} tasks`}>
          ≈ {d.work.hours_saved_estimate} hours of your time (estimate): invoices checked, corrected invoice, reminders, deposits, bookings, bills
        </Tile>
      </div>
    </Panel>
  )
}
