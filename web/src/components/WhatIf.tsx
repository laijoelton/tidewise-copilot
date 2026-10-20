import { RotateCcw } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api, rm, shortDate, type Json } from '../lib/api'
import { CashChart } from './CashChart'
import { Panel, cx } from './ui'

interface S { who: string; days: number; lose: string; sales: number; extra: number; usd: number }
const EMPTY: S = { who: '*', days: 0, lose: '', sales: 0, extra: 0, usd: 0 }

const PRESETS: { label: string; s: Partial<S> }[] = [
  { label: 'Everyone pays 2 weeks late', s: { who: '*', days: 14 } },
  { label: 'Sales drop 20%', s: { sales: -20 } },
  { label: 'Hire 3 staff (RM 9k/mo)', s: { extra: 9000 } },
  { label: 'USD weakens 5%', s: { usd: -5 } },
]

// Simulates changes on top of the forecast. Read-only: the API never
// touches the books for a what-if.
export function WhatIf({ weekly, buffer, customers }: { weekly: Json[]; buffer: number; customers: string[] }) {
  const [s, setS] = useState<S>(EMPTY)
  const [res, setRes] = useState<Json | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const active = s.days !== 0 || !!s.lose || s.sales !== 0 || s.extra !== 0 || s.usd !== 0

  useEffect(() => {
    if (!active) { setRes(null); return }
    const scenario: Json = {}
    if (s.days) scenario.customer_delay = { customer: s.who, days: s.days }
    if (s.lose) scenario.lose_customer = s.lose
    if (s.sales) scenario.sales_change_pct = s.sales
    if (s.extra) scenario.extra_monthly_cost = s.extra
    if (s.usd) scenario.usd_change_pct = s.usd
    const t = setTimeout(() => api.whatif(scenario).then((r) => { setRes(r); setErr(null) }).catch((e) => setErr(e.message)), 180)
    return () => clearTimeout(t)
  }, [s, active])

  const set = (patch: Partial<S>) => setS((x) => ({ ...x, ...patch }))
  const b = res?.baseline, w = res?.whatif

  return (
    <Panel title="13-week forecast & what-if" action={active && <button className="btn" onClick={() => setS(EMPTY)}><RotateCcw size={14} /> Reset</button>}>
      <div className="grid gap-6 xl:grid-cols-[320px_1fr]">
        <div className="space-y-4">
          <div className="flex flex-wrap gap-1.5">
            {PRESETS.map((p) => (
              <button key={p.label} className="rounded-full border border-edge bg-canvas/60 px-3 py-1 text-[12px] text-ink2 hover:border-ink3 hover:text-ink"
                onClick={() => setS({ ...EMPTY, ...p.s })}>{p.label}</button>
            ))}
          </div>
          <Field label="Customers pay late">
            <div className="flex gap-2">
              <select className="input !w-auto flex-1" value={s.who} onChange={(e) => set({ who: e.target.value })}>
                <option value="*">Everyone</option>
                {customers.map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
              <Num value={s.days} onChange={(v) => set({ days: v })} suffix="days" min={-30} max={90} />
            </div>
          </Field>
          <Field label="Lose a customer">
            <select className="input" value={s.lose} onChange={(e) => set({ lose: e.target.value })}>
              <option value="">No</option>
              {customers.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
          </Field>
          <Slider label="New sales change" value={s.sales} min={-50} max={50} step={5} fmt={(v) => `${v > 0 ? '+' : ''}${v}%`} onChange={(v) => set({ sales: v })} />
          <Slider label="New monthly cost" value={s.extra} min={0} max={50000} step={1000} fmt={(v) => rm(v)} onChange={(v) => set({ extra: v })} />
          <Slider label="USD vs MYR" value={s.usd} min={-15} max={15} step={1} fmt={(v) => `${v > 0 ? '+' : ''}${v}%`} onChange={(v) => set({ usd: v })} />
        </div>

        <div className="min-w-0">
          <div className="mb-3 grid grid-cols-3 gap-3">
            <Delta label="Lowest point" base={b?.trough.balance} alt={w?.trough.balance} hint={w ? shortDate(w.trough.date) : undefined} />
            <Delta label="Funding gap" base={b?.funding_gap} alt={w?.funding_gap} invert />
            <Delta label="Balance in 13 weeks" base={b?.closing} alt={w?.closing} />
          </div>
          <CashChart weekly={weekly} buffer={buffer} compare={res?.weekly_whatif} height={300} />
          <div className="mt-2 flex flex-wrap items-center gap-x-5 gap-y-1 text-[12px] text-ink3">
            <span><span className="mr-1.5 inline-block h-0.5 w-5 bg-brand align-middle" />Forecast</span>
            {res && <span><span className="mr-1.5 inline-block w-5 border-t-2 border-dashed border-warn align-middle" />What-if: {res.changes.join(', ')}</span>}
            {err && <span className="text-bad">{err}</span>}
            <span className="ml-auto">Simulation only — your books are never changed.</span>
          </div>
        </div>
      </div>
    </Panel>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <label className="block"><div className="mb-1.5 text-[12.5px] font-medium text-ink2">{label}</div>{children}</label>
}

function Num({ value, onChange, suffix, min, max }: { value: number; onChange: (v: number) => void; suffix: string; min: number; max: number }) {
  return (
    <div className="flex items-center gap-1.5">
      <input type="number" className="input num !w-20" value={value} min={min} max={max}
        onChange={(e) => onChange(Math.max(min, Math.min(max, Number(e.target.value) || 0)))} />
      <span className="text-[12px] text-ink3">{suffix}</span>
    </div>
  )
}

function Slider({ label, value, min, max, step, fmt, onChange }: { label: string; value: number; min: number; max: number; step: number; fmt: (v: number) => string; onChange: (v: number) => void }) {
  return (
    <label className="block">
      <div className="mb-1 flex justify-between text-[12.5px]"><span className="font-medium text-ink2">{label}</span><span className="num text-ink">{fmt(value)}</span></div>
      <input type="range" className="w-full accent-[#e5162f]" min={min} max={max} step={step} value={value} onChange={(e) => onChange(Number(e.target.value))} />
    </label>
  )
}

function Delta({ label, base, alt, hint, invert }: { label: string; base?: number; alt?: number; hint?: string; invert?: boolean }) {
  const d = base != null && alt != null ? alt - base : 0
  const worse = invert ? d > 0 : d < 0
  return (
    <div className="rounded-xl border border-edge bg-canvas/50 p-3">
      <div className="eyebrow">{label}</div>
      <div className="num mt-1 text-[18px] font-semibold">{alt != null ? rm(alt) : '—'}</div>
      <div className={cx('num text-[12px]', d === 0 ? 'text-ink3' : worse ? 'text-bad' : 'text-ok')}>
        {base == null ? 'move a control' : d === 0 ? 'no change' : `${d > 0 ? '+' : '−'}${rm(Math.abs(d))}`}{hint ? ` · ${hint}` : ''}
      </div>
    </div>
  )
}
