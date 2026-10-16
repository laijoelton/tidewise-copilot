import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { Badge, Loading, PageHead, Panel, Stat } from '../components/ui'
import { api, pct, rm, type Json } from '../lib/api'
import { useLoad } from '../lib/hooks'

export default function Health() {
  const { data, error } = useLoad(api.analysis)
  if (!data) return <Loading error={error} />
  const m = data.metrics
  return (
    <>
      <PageHead title="Business health" sub={`Computed from the last ${data.window_days} days of your ledger.`} />
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Stat label="Revenue (90 days)" value={rm(m.revenue_90d)} />
        <Stat label="Gross / operating margin" value={`${pct(m.gross_margin)} / ${pct(m.operating_margin)}`} tone={m.operating_margin > 0 ? 'ok' : 'bad'} />
        <Stat label="Customers pay in / you pay in" value={`${m.dso_days}d / ${m.dpo_days}d`} hint="DSO / DPO" />
        <Stat label="Cash runway" value={`${m.cash_runway_months} mo`} hint={`at ${rm(m.monthly_outflow)} / month out`} tone={m.cash_runway_months < 2 ? 'warn' : 'ok'} />
      </div>
      <div className="mt-4 grid gap-4 xl:grid-cols-[1.5fr_1fr]">
        <Panel title="Revenue vs costs by month">
          <ResponsiveContainer width="100%" height={280}>
            <BarChart data={data.trend}>
              <CartesianGrid stroke="rgba(157,179,189,0.08)" vertical={false} />
              <XAxis dataKey="month" tick={{ fill: '#6a8592', fontSize: 11 }} axisLine={false} tickLine={false} />
              <YAxis tick={{ fill: '#6a8592', fontSize: 11 }} axisLine={false} tickLine={false} tickFormatter={(v: number) => `${Math.round(v / 1000)}k`} />
              <Tooltip contentStyle={{ background: '#0d1c25', border: '1px solid #24404f', borderRadius: 8, fontSize: 12 }} formatter={(v) => rm(Number(v))} />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              <Bar dataKey="revenue" name="Revenue" fill="#2dd4bf" radius={[3, 3, 0, 0]} />
              <Bar dataKey="costs" name="Costs" fill="#f87171" fillOpacity={0.6} radius={[3, 3, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </Panel>
        <div className="flex flex-col gap-4">
          <Panel title="What the numbers say">
            <div className="flex flex-col gap-3">
              {data.insights.map((i: Json, k: number) => (
                <div key={k}>
                  <div className="flex items-center gap-2"><Badge tone={i.tone}>{i.tone}</Badge><span className="font-medium">{i.title}</span></div>
                  <div className="mt-1 text-[12.5px] text-ink3">{i.detail}</div>
                </div>
              ))}
            </div>
          </Panel>
          <Panel title="Where the money went (90 days)">
            <table className="data"><tbody>
              {Object.entries(data.expenses_90d).map(([k, v]) => (
                <tr key={k}><td className="capitalize">{k}</td><td className="num text-right">{rm(v as number)}</td></tr>
              ))}
            </tbody></table>
          </Panel>
        </div>
      </div>
    </>
  )
}
