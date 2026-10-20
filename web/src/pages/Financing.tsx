import { useState } from 'react'
import { Badge, Check, Empty, Loading, PageHead, Panel, Stat, cx } from '../components/ui'
import { api, pct, rm, shortDate, type Json } from '../lib/api'
import { useLoad } from '../lib/hooks'

const fx = (v: number | null | undefined, d = 4) => (v == null ? '—' : v.toFixed(d))

function LiveRates() {
  const { data: s } = useLoad(api.market)
  if (!s) return null
  const cn = s.china
  const src = (x: Json) => (x?.source === 'fallback'
    ? <span className="text-ink3">offline</span>
    : <span className="text-ink3">{x.date ? shortDate(x.date) : ''}{x.source === 'saved' ? ' · saved' : ' · live'}</span>)
  const rows: [string, string, string, Json][] = [
    ['USD / MYR', fx(s.usd_myr.rate), 'Bank Negara Malaysia', s.usd_myr],
    ['CNY / MYR', fx(s.cny_myr.rate), 'Bank Negara Malaysia', s.cny_myr],
    ['CNY / MYR · 人民币中间价', fx(cn.cny_myr_parity.rate, 5), 'CFETS · People\'s Bank of China', cn.cny_myr_parity],
    ['USD / CNY · 人民币中间价', fx(cn.usd_cny_parity.rate), 'CFETS · People\'s Bank of China', cn.usd_cny_parity],
    ['OPR (Malaysia)', s.opr.level_pct == null ? '—' : `${s.opr.level_pct.toFixed(2)}%`, 'Bank Negara Malaysia', s.opr],
    ['LPR 1Y / 5Y · 贷款市场报价利率', cn.lpr['1y_pct'] == null ? '—' : `${cn.lpr['1y_pct'].toFixed(2)}% / ${cn.lpr['5y_pct']?.toFixed(2) ?? '—'}%`, 'CFETS · People\'s Bank of China', cn.lpr],
  ]
  return (
    <Panel className="mt-4" title="Live rates · Malaysia & China">
      <table className="data">
        <thead><tr><th>Rate</th><th className="text-right">Value</th><th>Source</th><th className="text-right">As of</th></tr></thead>
        <tbody>
          {rows.map(([name, v, who, x]) => (
            <tr key={name}><td>{name}</td><td className="num text-right">{v}</td><td className="text-[12.5px] text-ink2">{who}</td><td className="text-right text-[12px]">{src(x)}</td></tr>
          ))}
        </tbody>
      </table>
      <p className="mt-3 text-[12.5px] text-ink3">
        {s.cny_myr_gap_pct != null && <>Malaysia's and China's official yuan–ringgit rates differ by {Math.abs(s.cny_myr_gap_pct).toFixed(2)}%. </>}
        {Object.values({ a: s.usd_myr, b: s.opr, c: cn.lpr }).some((x: Json) => x.source === 'saved') && <>Live services unreachable: showing real data saved on {shortDate(s.saved_on)}. </>}
        Your books use fixed sandbox rates (USD {fx(s.usd_myr.book_rate, 2)}) so the demo is reproducible; live rates value your foreign receivables today, price every currency conversion and set the currency swing in the risk simulation.
      </p>
      <Converter s={s} />
    </Panel>
  )
}

type Cur = 'MYR' | 'USD' | 'CNY'
const WORD: Record<Cur, string> = { MYR: 'RM', USD: 'USD', CNY: 'yuan' }

// Same rule as tidewise/fx.py: ringgit legs at Bank Negara's rate, USD↔CNY at CFETS's central parity.
function rateOf(s: Json, from: Cur, to: Cur): [number, string] {
  const perMyr: Record<Cur, number> = { MYR: 1, USD: s.usd_myr.rate, CNY: s.cny_myr.rate }
  const parity = s.china?.usd_cny_parity?.rate
  if (from !== to && (from === 'USD' || from === 'CNY') && (to === 'USD' || to === 'CNY') && parity)
    return [from === 'USD' ? parity : 1 / parity, 'CFETS central parity']
  return [perMyr[from] / perMyr[to], 'Bank Negara Malaysia']
}

function Converter({ s }: { s: Json }) {
  const [amount, setAmount] = useState(10000)
  const [from, setFrom] = useState<Cur>('MYR')
  const [to, setTo] = useState<Cur>('CNY')
  const same = from === to
  const [rate, who] = rateOf(s, from, to)
  const cmd = `convert ${WORD[from]} ${amount.toLocaleString()} to ${WORD[to]}`
  return (
    <div className="mt-4 rounded-lg border border-edge bg-canvas/50 p-3">
      <div className="eyebrow mb-2">Convert · MYR · USD · 人民币 CNY</div>
      <div className="flex flex-wrap items-center gap-2 text-[13px]">
        <input className="input w-32" type="number" min={1} value={amount} onChange={(e) => setAmount(Math.max(0, Number(e.target.value)))} />
        <select className="input w-24" value={from} onChange={(e) => setFrom(e.target.value as Cur)}>{(['MYR', 'USD', 'CNY'] as Cur[]).map((c) => <option key={c}>{c}</option>)}</select>
        <span className="text-ink3">→</span>
        <select className="input w-24" value={to} onChange={(e) => setTo(e.target.value as Cur)}>{(['MYR', 'USD', 'CNY'] as Cur[]).map((c) => <option key={c}>{c}</option>)}</select>
        {!same && <span className="num font-semibold">≈ {to} {(amount * rate).toLocaleString(undefined, { maximumFractionDigits: 2 })}</span>}
      </div>
      {!same && <p className="mt-2 text-[12px] text-ink3">1 {from} = {rate.toFixed(4)} {to} · {who}. To convert, ask the Copilot “{cmd}” (or 中文 / Bahasa Malaysia). Up to about USD 5,000 runs on its own; more waits for your approval.</p>}
    </div>
  )
}

export default function Financing() {
  const { data: m, error } = useLoad(api.financing)
  if (!m) return <Loading error={error} />
  if (!m.need) return (
    <>
      <PageHead title="Financing" />
      <Empty>No funding gap in the 13-week forecast — nothing to borrow.</Empty>
      <LiveRates />
    </>
  )

  return (
    <>
      <PageHead title="Financing" sub="Every product is checked rule by rule against your own books. Order: eligible → arrives in time → not high-cost → covers the need → cheapest. The catalogue is illustrative (fictional providers)."
        action={
          <div className="flex flex-wrap gap-2 text-[12.5px]">
            <div className="rounded-lg border border-edge bg-card px-3 py-2"><span className="eyebrow">BNM OPR</span> <span className="num ml-1 font-semibold">{m.opr?.level_pct != null ? `${m.opr.level_pct.toFixed(2)}%` : '—'}</span></div>
            <div className="rounded-lg border border-edge bg-card px-3 py-2"><span className="eyebrow">China LPR 1Y</span> <span className="num ml-1 font-semibold">{m.lpr?.['1y_pct'] != null ? `${m.lpr['1y_pct'].toFixed(2)}%` : '—'}</span></div>
          </div>} />
      <div className="grid gap-4 sm:grid-cols-3">
        <Stat label="You need" value={rm(m.need)} hint={`by ${shortDate(m.need_by)} (planning ${rm(m.target_with_fee_margin)} incl. fee margin)`} tone="bad" />
        <Stat label="Recommended plan" value={m.recommended_plan.map((p: Json) => p.id).join(' + ') || '—'} hint={m.plan_covers_need ? 'closes the gap' : `still short ${rm(m.unmet)}`} tone={m.plan_covers_need ? 'ok' : 'warn'} />
        <Stat label="Estimated cost" value={rm(m.plan_total_cost)} hint="interest + fees over the expected tenor" />
      </div>

      <Panel className="mt-4" title="Recommended plan">
        <div className="grid gap-3 md:grid-cols-2">
          {m.recommended_plan.map((p: Json) => (
            <div key={p.id} className="rounded-lg border border-brand/30 bg-brand/5 p-4">
              <div className="font-semibold">{p.name}</div>
              <div className="num mt-1 text-lg">{rm(p.amount)}</div>
              <div className="text-[12.5px] text-ink3">funds by {shortDate(p.funds_by)} · est. cost {rm(p.cost)}</div>
            </div>
          ))}
        </div>
        <p className="mt-3 text-[12.5px] text-ink3">Applying is a T3 action: ask the Copilot “What should I do this week?” and approve it on the Approvals page.</p>
      </Panel>

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        {m.options.map((o: Json) => (
          <div key={o.id} className={cx('panel p-5', !o.eligible && 'opacity-80')}>
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-semibold">{o.name}</span>
              {o.eligible ? <Badge tone="good">eligible</Badge> : <Badge tone="critical">not eligible</Badge>}
              {o.eligible && !o.on_time && <Badge tone="warning">too slow</Badge>}
              {o.high_cost && <Badge tone="critical">high cost</Badge>}
            </div>
            <div className="mt-1 text-[12.5px] text-ink3">{o.provider} · {o.summary}</div>
            <div className="mt-3 grid grid-cols-3 gap-2 text-[12.5px]">
              <div><div className="eyebrow">Available</div><div className="num">{rm(o.available)}</div></div>
              <div><div className="eyebrow">Funds by</div><div className="num">{shortDate(o.funds_by)}</div></div>
              <div><div className="eyebrow">Effective APR</div><div className={cx('num', o.high_cost && 'text-bad')}>{pct(o.effective_apr)}</div></div>
            </div>
            {o.checks.length > 0 && <div className="mt-3 space-y-1">{o.checks.map((c: Json, i: number) => <Check key={i} {...c} />)}</div>}
          </div>
        ))}
      </div>
      <LiveRates />
    </>
  )
}
