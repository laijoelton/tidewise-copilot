import { CheckCircle2, CircleAlert, Clock } from 'lucide-react'
import { useState } from 'react'
import { Empty, Loading, PageHead, Panel, cx } from '../components/ui'
import { api, shortDate, type Json } from '../lib/api'
import { bump, useLoad } from '../lib/hooks'

const STATUS: Record<string, { label: string; cls: string; icon: typeof CheckCircle2 }> = {
  MATCHED: { label: 'Matches PO and delivery', cls: 'bg-ok/10 text-ok border-ok/25', icon: CheckCircle2 },
  MISMATCH: { label: 'Doesn’t match the PO', cls: 'bg-bad/10 text-bad border-bad/30', icon: CircleAlert },
  AWAITING_APPROVAL: { label: 'Corrected — awaiting customer', cls: 'bg-warn/12 text-warn border-warn/30', icon: Clock },
}

export default function Invoices() {
  const { data, error } = useLoad(api.matching)
  if (!data) return <Loading error={error} />
  return (
    <>
      <PageHead title="Invoice check"
        sub="Your customers only pay an invoice that matches their purchase order and what was delivered. Tidewise runs that three-way match before they do — so a typo is fixed in hours instead of costing you weeks." />
      {data.length === 0 ? <Empty>No open invoices with a purchase order.</Empty> : (
        <div className="flex flex-col gap-5">{data.map((r: Json) => <MatchCard key={r.invoice_id} r={r} />)}</div>
      )}
    </>
  )
}

function MatchCard({ r }: { r: Json }) {
  const [busy, setBusy] = useState(false)
  const st = STATUS[r.status] || STATUS.MATCHED
  const Icon = st.icon
  const money = (v: number | null) => (v == null ? '—' : `${r.currency} ${v.toLocaleString('en-MY', { minimumFractionDigits: 2 })}`)

  return (
    <Panel
      title={<span className="flex flex-wrap items-center gap-3">{r.invoice_id} <span className="text-[15px] font-normal text-ink3">for {r.customer}</span></span>}
      action={<span className={cx('inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-[12.5px] font-semibold', st.cls)}><Icon size={14} />{st.label}</span>}
    >
      <div className="mb-4 grid gap-3 sm:grid-cols-3">
        <Doc label="Customer’s purchase order" value={r.po_number} hint={`total ${money(r.po_total)}`} />
        <Doc label="Delivery note" value={r.delivery_id || '—'} hint={r.delivered_on ? `delivered ${shortDate(r.delivered_on)} · signed ${r.signed_by}` : 'not delivered'} />
        <Doc label="Our invoice" value={r.invoice_id} hint={`total ${money(r.invoice_total)} · due ${shortDate(r.due)}`} tone={r.status === 'MISMATCH' ? 'bad' : undefined} />
      </div>

      <div className="overflow-x-auto rounded-xl border border-edge">
        <table className="data">
          <thead>
            <tr><th>Item</th><th className="text-right">Ordered</th><th className="text-right">Delivered</th><th className="text-right">Invoiced</th><th className="text-right">PO price</th><th className="text-right">Invoice price</th></tr>
          </thead>
          <tbody>
            {r.rows.map((row: Json) => {
              const q = row.flags.includes('qty') || row.flags.includes('over_delivered')
              const p = row.flags.includes('price')
              return (
                <tr key={row.sku} className={cx((q || p) && 'bg-bad/5')}>
                  <td><div className="font-medium">{row.description}</div><div className="mono text-[11.5px] text-ink3">{row.sku}</div></td>
                  <td className="num text-right">{row.ordered ?? '—'}</td>
                  <td className="num text-right">{row.delivered ?? '—'}</td>
                  <td className={cx('num text-right', q && 'font-semibold text-bad')}>{row.invoiced ?? '—'}</td>
                  <td className="num text-right">{row.po_price != null ? row.po_price.toFixed(2) : '—'}</td>
                  <td className={cx('num text-right', p && 'font-semibold text-bad')}>{row.invoice_price != null ? row.invoice_price.toFixed(2) : '—'}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {r.status === 'MISMATCH' && (
        <div className="mt-4 rounded-xl border border-bad/25 bg-bad/5 p-4 text-[13.5px] leading-relaxed">
          {r.discrepancies.map((d: Json, i: number) => (
            <div key={i}><b>{d.label}:</b> {d.detail}{d.impact ? ` (${r.currency} ${Math.abs(d.impact).toLocaleString()} ${d.impact > 0 ? 'over' : 'under'} the PO)` : ''}.</div>
          ))}
          <div className="mt-1.5 text-ink2">Their accounts team will hold this invoice for about {r.expected_delay_days} days. Ask the Copilot “what should I do this week?” — it sends the corrected invoice on its own (T1).</div>
        </div>
      )}
      {r.status === 'AWAITING_APPROVAL' && (
        <div className="mt-4 flex flex-wrap items-center gap-3 rounded-xl border border-warn/30 bg-warn/8 p-4 text-[13.5px]">
          <span className="flex-1">Corrected invoice sent ({r.note}). Payment now waits for the customer’s next run — about {r.expected_delay_days} days.</span>
          <button className="btn" disabled={busy} onClick={async () => { setBusy(true); await api.customerApproves(r.invoice_id); bump(); setBusy(false) }}>
            Simulate customer approving it
          </button>
        </div>
      )}
    </Panel>
  )
}

function Doc({ label, value, hint, tone }: { label: string; value: string; hint: string; tone?: 'bad' }) {
  return (
    <div className="rounded-xl border border-edge bg-canvas/50 p-3.5">
      <div className="eyebrow">{label}</div>
      <div className={cx('mt-1 text-[16px] font-semibold', tone === 'bad' && 'text-bad')}>{value}</div>
      <div className="text-[12.5px] text-ink3">{hint}</div>
    </div>
  )
}
