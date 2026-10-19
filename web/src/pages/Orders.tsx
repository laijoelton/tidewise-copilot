import { ExternalLink, ShieldAlert } from 'lucide-react'
import { useState } from 'react'
import { Badge, Check, Empty, Loading, PageHead, Panel, cx } from '../components/ui'
import { api, rm, shortDate, type Json } from '../lib/api'
import { bump, useLoad, type Page } from '../lib/hooks'

const STAGES = [
  ['pending', 'New bookings'],
  ['confirmed', 'Confirmed'],
  ['in_production', 'In production'],
  ['invoiced', 'Invoiced'],
] as const

const VERDICT: Record<string, [string, string]> = {
  confirm: ['Tidewise can confirm this', 'good'],
  owner: ['Needs your decision', 'warning'],
  decline: ['Decline — suspicious', 'critical'],
}

export default function Orders({ go }: { go: (p: Page) => void }) {
  const { data, error } = useLoad(api.orders)
  if (!data) return <Loading error={error} />
  const orders: Json[] = data.orders
  const pending = orders.filter((o) => o.status === 'pending')
  const book = orders.filter((o) => ['confirmed', 'in_production'].includes(o.status))

  return (
    <>
      <PageHead title="Orders & bookings"
        sub="Customers book through your portal. Every booking is screened, credit-checked by the ML model and checked against factory capacity before anyone commits — then confirmed orders feed your cash forecast."
        action={<button className="btn" onClick={() => go('portal')}><ExternalLink size={14} /> Open customer portal</button>} />

      <div className="mb-5 grid grid-cols-2 gap-3 lg:grid-cols-4">
        {STAGES.map(([k, label]) => {
          const list = orders.filter((o) => o.status === k)
          const value = list.reduce((s, o) => s + o.total_myr, 0)
          return (
            <div key={k} className="panel p-4">
              <div className="eyebrow">{label}</div>
              <div className="num mt-1.5 text-[24px] font-semibold">{list.length}</div>
              <div className="text-[12.5px] text-ink3">{rm(value)}</div>
            </div>
          )
        })}
      </div>

      <h2 className="mb-3 text-[20px]">New bookings</h2>
      {pending.length === 0 ? <Empty>No bookings waiting. Ask the Copilot “what should I do this week?” to process new ones.</Empty> : (
        <div className="grid gap-4 xl:grid-cols-2">{pending.map((o) => <Booking key={o.id} o={o} />)}</div>
      )}

      <h2 className="mb-3 mt-8 text-[20px]">Order book</h2>
      <Panel>
        {book.length === 0 ? <Empty>No confirmed orders.</Empty> : (
          <table className="data">
            <thead><tr><th>Order</th><th>Customer</th><th>Status</th><th className="text-right">Value</th><th>Deposit</th><th>Delivery</th><th /></tr></thead>
            <tbody>{book.map((o) => <BookRow key={o.id} o={o} />)}</tbody>
          </table>
        )}
      </Panel>

      {orders.some((o) => o.status === 'declined') && (
        <Panel className="mt-4" title="Declined">
          {orders.filter((o) => o.status === 'declined').map((o) => (
            <div key={o.id} className="flex items-start gap-2 text-[13px]">
              <ShieldAlert size={15} className="mt-0.5 text-bad" />
              <span><b>{o.id}</b> {o.customer} — {o.assessment?.declined_because || o.assessment?.why}</span>
            </div>
          ))}
        </Panel>
      )}
    </>
  )
}

function Booking({ o }: { o: Json }) {
  const a = o.assessment || {}
  const [label, tone] = VERDICT[a.verdict] || ['Not assessed yet', 'info']
  return (
    <div className={cx('panel p-5', a.verdict === 'decline' && '!border-bad/40')}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <div className="text-[16px] font-semibold">{o.customer}</div>
          <div className="text-[12.5px] text-ink3">{o.id} · {o.po_number} · via {o.channel} · {o.contact}</div>
        </div>
        <Badge tone={tone}>{label}</Badge>
      </div>
      <div className="mt-3 flex flex-wrap gap-x-5 gap-y-1 text-[13px]">
        {o.lines.map((l: Json) => <span key={l.sku}>{l.qty} × {l.description}</span>)}
      </div>
      <div className="mt-3 grid grid-cols-3 gap-3">
        <Fact label="Value" value={`${o.currency} ${o.total.toLocaleString()}`} />
        <Fact label="Late-payment risk (ML)" value={a.risk != null ? `${Math.round(a.risk * 100)}%` : '—'} tone={a.risk > 0.3 ? 'warn' : 'ok'} />
        <Fact label="Deposit asked" value={a.deposit_pct != null ? `${Math.round(a.deposit_pct * 100)}%` : '—'} />
      </div>
      {o.notes && <div className="mt-3 rounded-lg border border-edge bg-[#fbf8f0] p-3 text-[12.5px] text-ink2">“{o.notes}”</div>}
      <div className="mt-3 space-y-1">
        <Check passed={a.screening?.trust !== 'quarantined'} name="Screening" detail={a.screening?.trust === 'quarantined' ? (a.screening.findings || []).slice(0, 2).join('; ') : 'notes are clean'} />
        <Check passed={!!a.contact_matches} name="Identity" detail={a.contact_matches ? 'booked from the contact on file' : a.known_customer ? 'known name, new email address' : 'new customer'} />
        <Check passed={!!a.feasible} name="Capacity" detail={a.ready_by ? `ready by ${shortDate(a.ready_by)} for delivery ${shortDate(o.delivery_date)}` : 'unknown product'} />
      </div>
      <div className="mt-3 text-[12.5px] text-ink3">{a.why}</div>
    </div>
  )
}

function Fact({ label, value, tone }: { label: string; value: string; tone?: 'warn' | 'ok' }) {
  return (
    <div className="rounded-lg bg-canvas/60 px-3 py-2">
      <div className="text-[11px] font-semibold uppercase tracking-wide text-ink3">{label}</div>
      <div className={cx('num text-[15px] font-semibold', tone === 'warn' && 'text-warn', tone === 'ok' && 'text-ok')}>{value}</div>
    </div>
  )
}

function BookRow({ o }: { o: Json }) {
  const [busy, setBusy] = useState(false)
  return (
    <tr>
      <td><div className="font-medium">{o.id}</div><div className="text-[12px] text-ink3">{o.po_number}</div></td>
      <td>{o.customer}</td>
      <td><Badge tone={o.status === 'in_production' ? 'info' : 'good'}>{o.status.replace('_', ' ')}</Badge></td>
      <td className="num text-right">{o.currency} {o.total.toLocaleString()}</td>
      <td className="text-[13px]">
        {o.deposit_due > 0
          ? <span className="text-warn">{o.currency} {o.deposit_due.toLocaleString()} due</span>
          : <span className="text-ok">{o.deposit_pct > 0 ? 'paid' : 'not required'}</span>}
      </td>
      <td className="num">{shortDate(o.delivery_date)}</td>
      <td className="text-right">
        {o.deposit_due > 0 && (
          <button className="btn !py-1 text-[12px]" disabled={busy} onClick={async () => { setBusy(true); await api.payDeposit(o.id); bump(); setBusy(false) }}>
            Simulate deposit paid
          </button>
        )}
      </td>
    </tr>
  )
}
