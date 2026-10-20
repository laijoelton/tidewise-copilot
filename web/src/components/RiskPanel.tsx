import { useState } from 'react'
import { Area, CartesianGrid, ComposedChart, Line, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { api, rm, shortDate, type Json } from '../lib/api'
import { bump, type Page, useLoad } from '../lib/hooks'
import { C, tick, tooltipStyle } from '../lib/theme'
import { Panel, Stat, cx } from './ui'

const pct = (p: number, d = 0) => `${(p * 100).toFixed(d)}%`
const k = (v: number) => `${Math.round(v / 1000)}k`

/** Monte Carlo forecast: probability of breaching the floor + a fan chart. */
export function RiskPanel({ weekly }: { weekly: Json[] }) {
  const { data: r, error } = useLoad(api.risk)
  if (!r) return <Panel title="How likely is a shortfall?"><div className="text-ink3">{error ? `Couldn't load: ${error}` : 'Simulating 2,000 futures…'}</div></Panel>

  const rows = r.weekly_bands.map((b: Json, i: number) => ({
    name: shortDate(b.start), band90: [b.p5, b.p95], band80: [b.p10, b.p90], p50: b.p50, single: weekly[i]?.closing,
  }))
  const p = r.p_below_floor
  return (
    <Panel title="How likely is a shortfall?"
      action={<span className="text-[12px] text-ink3">{r.sims.toLocaleString()} simulated futures · seed {r.seed}</span>}>
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Stat label="Chance of dipping below your floor" value={pct(p)} tone={p > 0.2 ? 'bad' : p > 0.05 ? 'warn' : 'ok'}
          hint={r.first_breach ? `most likely from ${shortDate(r.first_breach.p50)}` : 'in none of the futures'} />
        <Stat label="Gap in the single forecast" value={rm(r.deterministic.funding_gap)} hint="one number, one future" />
        <Stat label="Typical gap (median)" value={rm(r.gap.p50)} tone={r.gap.p50 > r.deterministic.funding_gap ? 'warn' : undefined}
          hint={r.gap.p50 > r.deterministic.funding_gap ? 'worse than the single forecast' : 'across all futures'} />
        <Stat label="Bad case (1 in 10)" value={rm(r.gap.p90)} tone={r.gap.p90 > 0 ? 'bad' : 'ok'} hint="the gap to plan for" />
      </div>

      <div className="mt-5">
        <ResponsiveContainer width="100%" height={300}>
          <ComposedChart data={rows} margin={{ top: 8, right: 8, left: 4, bottom: 0 }}>
            <CartesianGrid stroke={C.grid} vertical={false} />
            <XAxis dataKey="name" tick={tick} axisLine={false} tickLine={false} />
            <YAxis tick={tick} axisLine={false} tickLine={false} width={52} tickFormatter={k} />
            <Tooltip contentStyle={tooltipStyle} formatter={(v, n) => {
              const label = { band90: '90% of futures', band80: '80% of futures', p50: 'Median future', single: 'Single forecast' }[String(n)] || String(n)
              return [Array.isArray(v) ? `${rm(Number(v[0]))} – ${rm(Number(v[1]))}` : rm(Number(v)), label]
            }} />
            <ReferenceLine y={0} stroke={C.bad} strokeOpacity={0.5} />
            <ReferenceLine y={r.cash_buffer} stroke={C.warn} strokeDasharray="5 4" strokeOpacity={0.8}
              label={{ value: 'your floor', fill: C.warn, fontSize: 11, position: 'insideTopRight' }} />
            <Area dataKey="band90" stroke="none" fill={C.brandSoft} fillOpacity={0.28} isAnimationActive={false} />
            <Area dataKey="band80" stroke="none" fill={C.brandSoft} fillOpacity={0.5} isAnimationActive={false} />
            <Line dataKey="p50" stroke={C.brand} strokeWidth={2.2} dot={false} isAnimationActive={false} />
            <Line dataKey="single" stroke={C.ink} strokeWidth={1.6} strokeDasharray="4 4" dot={false} isAnimationActive={false} />
          </ComposedChart>
        </ResponsiveContainer>
        <div className="mt-2 flex flex-wrap gap-x-5 gap-y-1 text-[12px] text-ink2">
          <span><i className="mr-1.5 inline-block h-2.5 w-4 rounded-sm align-middle" style={{ background: C.brandSoft, opacity: 0.8 }} />80% of futures</span>
          <span><i className="mr-1.5 inline-block h-2.5 w-4 rounded-sm align-middle" style={{ background: C.brandSoft, opacity: 0.35 }} />90% of futures</span>
          <span><i className="mr-1.5 inline-block h-0.5 w-4 align-middle" style={{ background: C.brand }} />median future</span>
          <span><i className="mr-1.5 inline-block w-4 border-t-2 border-dashed align-middle" style={{ borderColor: C.ink }} />single forecast</span>
        </div>
      </div>

      {r.drivers.some((d: Json) => d.lift > 0.01) && (
        <div className="mt-5">
          <div className="eyebrow mb-2">What drives the risk</div>
          <table className="data">
            <thead><tr><th>Receipt</th><th className="text-right">Risk if it lands late</th><th className="text-right">if on time</th></tr></thead>
            <tbody>
              {r.drivers.filter((d: Json) => d.lift > 0.01).map((d: Json) => (
                <tr key={d.source}><td>{d.label}</td><td className="num text-right">{pct(d.p_if_late)}</td><td className="num text-right">{pct(d.p_if_on_time)}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="mt-4 text-[12px] text-ink3">
        Each future draws payment dates from each customer's own history, adds the ML collection-risk tail (paid 30+ days late),
        varies how long a PO-mismatch hold lasts, USD/MYR, deposits and run-rate sales. Bills, payroll and repayments stay fixed.
      </p>
    </Panel>
  )
}

/** The cheapest set of actions that makes the owner N% safe. */
export function ConfidencePlanner({ go }: { go?: (p: Page) => void }) {
  const [target, setTarget] = useState(0.05)
  const { data: plan, error } = useLoad(() => api.riskPlan(target), [target])
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState<Json | null>(null)

  async function propose() {
    setBusy(true)
    try {
      const r = await api.chat(`make me ${Math.round((1 - target) * 100)}% safe`)
      setDone({ pending: r.pending_approvals.length, steps: r.steps.filter((s: Json) => s.kind === 'action') })
      bump()
    } finally {
      setBusy(false)
    }
  }

  return (
    <Panel title="Make me safe"
      action={
        <div className="flex items-center gap-1 rounded-lg border border-edge p-0.5 text-[12.5px]">
          {[0.1, 0.05, 0.01].map((t) => (
            <button key={t} onClick={() => { setTarget(t); setDone(null) }}
              className={cx('rounded-md px-2.5 py-1', t === target ? 'bg-brand text-card' : 'text-ink2 hover:bg-sub')}>
              {pct(1 - t)} safe
            </button>
          ))}
        </div>}>
      {!plan ? <div className="text-ink3">{error ? `Couldn't load: ${error}` : 'Searching for the cheapest plan…'}</div> : (
        <>
          <div className="grid gap-3 md:grid-cols-2">
            <div className="rounded-xl border border-edge bg-sub/40 p-4">
              <div className="eyebrow">The usual plan · cover the single forecast's gap</div>
              <div className="mt-2 flex items-baseline gap-3">
                <span className="num text-[26px] font-semibold text-bad">{pct(plan.classic.p_below_floor)}</span>
                <span className="text-[13px] text-ink2">still dip below your floor</span>
              </div>
              <div className="mt-1 text-[12.5px] text-ink3">costs {rm(plan.classic.cost)} · 1 in 10 futures still short by {rm(plan.classic.gap_p90)}</div>
            </div>
            <div className="rounded-xl border border-brand/40 bg-brand/5 p-4">
              <div className="eyebrow">Tidewise · cheapest plan to {pct(1 - plan.target)} safe</div>
              <div className="mt-2 flex items-baseline gap-3">
                <span className={cx('num text-[26px] font-semibold', plan.reached ? 'text-ok' : 'text-warn')}>{pct(plan.p_after, 1)}</span>
                <span className="text-[13px] text-ink2">chance of dipping below</span>
              </div>
              <div className="mt-1 text-[12.5px] text-ink3">
                costs {rm(plan.total_cost)}{plan.total_cost > plan.classic.cost && ` (${rm(plan.total_cost - plan.classic.cost)} more)`}
                {!plan.reached && ' · the lowest the available options can reach'}
              </div>
            </div>
          </div>

          <div className="mt-5 space-y-2.5">
            {plan.ladder.map((s: Json) => (
              <div key={s.step} className="grid grid-cols-[1.6rem_1fr] items-start gap-3 sm:grid-cols-[1.6rem_1fr_14rem]">
                <span className="num mt-0.5 text-center text-[12px] text-ink3">{s.step === 0 ? '·' : s.step}</span>
                <div>
                  <div className="text-[13.5px]">{s.title}</div>
                  {s.step > 0 && <div className="text-[12px] text-ink3">{s.cost ? `${rm(s.cost)} · running total ${rm(s.cum_cost)}` : 'free'} · expected shortfall {rm(s.expected_gap)}</div>}
                </div>
                <div className="col-span-2 flex items-center gap-2 sm:col-span-1">
                  <div className="h-2.5 flex-1 overflow-hidden rounded-full bg-sub">
                    <div className="h-full rounded-full" style={{ width: `${Math.max(1.5, s.p_below_floor * 100)}%`, background: s.p_below_floor <= plan.target ? C.ok : s.p_below_floor > 0.2 ? C.bad : C.warn }} />
                  </div>
                  <span className="num w-12 text-right text-[12.5px]">{pct(s.p_below_floor, s.p_below_floor < 0.1 ? 1 : 0)}</span>
                </div>
              </div>
            ))}
          </div>

          <div className="mt-5 flex flex-wrap items-center gap-3 border-t border-edge pt-4">
            {!done ? (
              <button className="btn btn-primary" disabled={busy || plan.ladder.length < 2} onClick={propose}>
                {busy ? 'Proposing…' : `Propose this plan`}
              </button>
            ) : (
              <>
                <span className="text-[13px] text-ink2">Proposed {done.steps.length} step(s) — your policy decided what runs now.</span>
                {done.pending > 0 && go && <button className="btn btn-primary" onClick={() => go('approvals')}>Review {done.pending} approval{done.pending > 1 ? 's' : ''}</button>}
              </>
            )}
            <span className="text-[12px] text-ink3">
              Tried {plan.combinations_tried} combinations on a copy of your books, {plan.sims.toLocaleString()} futures each. Nothing is sent or applied until you propose it; payments and financing still need your approval.
            </span>
          </div>
        </>
      )}
    </Panel>
  )
}
