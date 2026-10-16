import { CashChart } from '../components/CashChart'
import { Badge, Loading, PageHead, Panel } from '../components/ui'
import { api, rm, shortDate, type Json } from '../lib/api'
import { useLoad } from '../lib/hooks'

export default function Cash() {
  const { data: fc, error } = useLoad(api.forecast)
  if (!fc) return <Loading error={error} />
  const known = fc.events.filter((e: Json) => !e.projected)

  return (
    <>
      <PageHead title="Cash flow" sub="Receipts are dated by how each customer actually pays (their 6-month average delay), not the invoice due date. Beyond what's booked, the forecast uses run-rates with a 15% sales haircut." />
      <Panel title="13-week forecast">
        <CashChart weekly={fc.weekly} buffer={fc.cash_buffer} height={320} />
      </Panel>

      <div className="mt-4 grid gap-4 xl:grid-cols-[1.4fr_1fr]">
        <Panel title="Booked cash movements">
          <div className="max-h-[520px] overflow-y-auto">
            <table className="data">
              <thead><tr><th>Date</th><th>Item</th><th>Basis</th><th className="text-right">Amount</th></tr></thead>
              <tbody>
                {known.map((e: Json, i: number) => (
                  <tr key={i}>
                    <td className="num whitespace-nowrap text-ink2">{shortDate(e.date)}</td>
                    <td>{e.label}</td>
                    <td className="text-[12px] text-ink3">{e.basis}</td>
                    <td className={`num whitespace-nowrap text-right ${e.direction === 'in' ? 'text-ok' : 'text-ink'}`}>
                      {e.direction === 'in' ? '+' : '−'}{rm(e.amount_myr)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
        <div className="flex flex-col gap-4">
          <Panel title="Customer payment behaviour">
            <table className="data">
              <thead><tr><th>Customer</th><th className="text-right">Avg days late</th></tr></thead>
              <tbody>
                {Object.entries(fc.customer_delays).sort((a, b) => (b[1] as number) - (a[1] as number)).map(([c, d]) => (
                  <tr key={c}><td>{c}</td><td className={`num text-right ${(d as number) > 14 ? 'text-warn' : ''}`}>{String(d)}</td></tr>
                ))}
              </tbody>
            </table>
          </Panel>
          <Panel title="Left out as doubtful">
            {fc.excluded.length === 0 ? <div className="text-ink3">Nothing excluded.</div> : fc.excluded.map((e: Json) => (
              <div key={e.source} className="text-[13px]">
                <Badge tone="warning">doubtful</Badge> <span className="ml-1">{e.label}</span> · <span className="num">{rm(e.amount_myr)}</span>
                <div className="mt-1 text-[12px] text-ink3">{e.why}</div>
              </div>
            ))}
          </Panel>
          <Panel title="Weekly totals">
            <table className="data">
              <thead><tr><th>Week of</th><th className="text-right">In</th><th className="text-right">Out</th><th className="text-right">Closing</th></tr></thead>
              <tbody>
                {fc.weekly.map((w: Json) => (
                  <tr key={w.week}>
                    <td className="num text-ink2">{shortDate(w.start)}</td>
                    <td className="num text-right text-ok">{Math.round(w.inflow / 1000)}k</td>
                    <td className="num text-right">{Math.round(w.outflow / 1000)}k</td>
                    <td className={`num text-right ${w.closing < 0 ? 'text-bad' : w.closing < fc.cash_buffer ? 'text-warn' : ''}`}>{Math.round(w.closing / 1000)}k</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Panel>
        </div>
      </div>
    </>
  )
}
