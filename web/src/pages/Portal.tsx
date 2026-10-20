import { ArrowLeft, CheckCircle2 } from 'lucide-react'
import { useMemo, useState } from 'react'
import { Loading, cx } from '../components/ui'
import { api, type Json } from '../lib/api'
import { bump, useLoad, type Page } from '../lib/hooks'

// The customer-facing booking portal of the (fictional) furniture maker.
// Everything typed here is untrusted: prices come from the server's
// catalogue, and the notes are screened before Tidewise acts on them.
export default function Portal({ go }: { go: (p: Page) => void }) {
  const { data, error } = useLoad(api.orders)
  const [form, setForm] = useState({ customer: '', email: '', po: '', delivery: '', notes: '' })
  const [qty, setQty] = useState<Record<string, number>>({})
  const [sent, setSent] = useState<Json | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const catalogue: Json[] = data?.catalogue || []
  const total = useMemo(() => catalogue.reduce((s, p) => s + (qty[p.sku] || 0) * p.unit_price_usd, 0), [catalogue, qty])
  if (!data) return <Loading error={error} />

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true); setErr(null)
    try {
      const items = Object.entries(qty).filter(([, q]) => q > 0).map(([sku, q]) => ({ sku, qty: q }))
      const r = await api.book({ customer: form.customer, email: form.email, po_number: form.po, items,
        delivery: form.delivery, notes: form.notes })
      setSent(r); bump()
    } catch (x) {
      setErr((x as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const ready = form.customer && form.email && form.po && form.delivery && total > 0

  return (
    <div className="-mx-4 -my-9 min-h-screen bg-[#fafafb] px-4 py-10 sm:-mx-10 sm:px-10">
      <div className="mx-auto max-w-5xl">
        <button className="mb-6 inline-flex items-center gap-1.5 text-[13px] text-ink3 hover:text-ink" onClick={() => go('orders')}>
          <ArrowLeft size={14} /> Back to Tidewise (owner view)
        </button>
        <header className="flex flex-wrap items-end justify-between gap-4 border-b border-edge pb-6">
          <div>
            <div className="eyebrow">Trade orders</div>
            <h1 className="mt-2 text-[40px] leading-none">Mamak Duos Sdn Bhd</h1>
            <p className="mt-2 text-[14.5px] text-ink2">Rattan and teak furniture, made in Muar, Malaysia. Book production for your next order.</p>
          </div>
          <span className="rounded-full border border-edge bg-card px-3 py-1 text-[12px] text-ink3">Demo portal · fictional business</span>
        </header>

        {sent ? (
          <div className="mt-10 panel flex flex-col items-center gap-3 p-10 text-center">
            <CheckCircle2 size={40} className="text-brand" />
            <h2 className="text-[26px]">Booking received — {sent.order_id}</h2>
            <p className="max-w-lg text-ink2">{sent.message} {sent.deposit_pct != null && `A ${Math.round(sent.deposit_pct * 100)}% deposit will be requested before production starts.`}</p>
            <div className="mt-2 flex gap-2">
              <button className="btn" onClick={() => { setSent(null); setQty({}) }}>Book another order</button>
              <button className="btn btn-primary" onClick={() => go('orders')}>See how Tidewise assessed it</button>
            </div>
          </div>
        ) : (
          <form onSubmit={submit} className="mt-8 grid gap-8 lg:grid-cols-[1.4fr_1fr]">
            <div>
              <h2 className="mb-3 text-[20px]">1 · Choose products</h2>
              <div className="grid gap-3 sm:grid-cols-2">
                {catalogue.map((p) => (
                  <label key={p.sku} className={cx('panel flex flex-col gap-2 p-4 transition-colors', (qty[p.sku] || 0) > 0 && '!border-brand/50 !bg-brand/5')}>
                    <div className="flex items-start justify-between gap-2">
                      <div>
                        <div className="font-semibold">{p.name}</div>
                        <div className="mono text-[11.5px] text-ink3">{p.sku} · up to {p.units_per_week}/week</div>
                      </div>
                      <div className="num text-[15px] font-semibold">USD {p.unit_price_usd}</div>
                    </div>
                    <input type="number" min={0} max={5000} className="input num" placeholder="Quantity"
                      value={qty[p.sku] || ''} onChange={(e) => setQty({ ...qty, [p.sku]: Math.max(0, Math.min(5000, Number(e.target.value) || 0)) })} />
                  </label>
                ))}
              </div>
            </div>
            <div className="flex flex-col gap-4">
              <h2 className="text-[20px]">2 · Your details</h2>
              <Field label="Company"><input className="input" value={form.customer} maxLength={80} onChange={(e) => setForm({ ...form, customer: e.target.value })} placeholder="e.g. Kiyomi Living KK" /></Field>
              <Field label="Accounts email"><input className="input" type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} placeholder="accounts@yourcompany.com" /></Field>
              <div className="grid grid-cols-2 gap-3">
                <Field label="Your PO number"><input className="input" value={form.po} maxLength={32} onChange={(e) => setForm({ ...form, po: e.target.value })} /></Field>
                <Field label="Delivery by"><input className="input" type="date" value={form.delivery} onChange={(e) => setForm({ ...form, delivery: e.target.value })} /></Field>
              </div>
              <Field label="Notes (optional)"><textarea className="input min-h-[84px]" maxLength={1000} value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} /></Field>
              <div className="panel flex items-center justify-between p-4">
                <span className="text-ink2">Order total</span>
                <span className="num text-[22px] font-semibold">USD {total.toLocaleString()}</span>
              </div>
              {err && <div className="text-[13px] text-bad">{err}</div>}
              <button className="btn btn-primary justify-center !py-3 text-[14px]" disabled={!ready || busy}>{busy ? 'Sending…' : 'Book this order'}</button>
              <p className="text-[12px] leading-relaxed text-ink3">Prices are set by Mamak Duos, not by this form. New customers are asked for a deposit; the balance is due 30 days after delivery.</p>
            </div>
          </form>
        )}
      </div>
    </div>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <label className="block"><div className="mb-1.5 text-[12.5px] font-medium text-ink2">{label}</div>{children}</label>
}
