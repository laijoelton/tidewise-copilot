import { useState } from 'react'
import { Badge, Loading, PageHead, Panel, cx } from '../components/ui'
import { api, type Json } from '../lib/api'
import { bump, useLoad } from '../lib/hooks'

export default function Documents() {
  const { data, error } = useLoad(api.documents)
  if (!data) return <Loading error={error} />
  return (
    <>
      <PageHead title="Trade documents" sub="Shipping instruction vs. bill of lading, field by field, with the exact line each value came from. Under a letter of credit, any conflict gets the presentation refused — and your payment delayed." />
      <div className="flex flex-col gap-4">{data.map((r: Json) => <Shipment key={r.shipment_id} r={r} />)}</div>
    </>
  )
}

function Shipment({ r }: { r: Json }) {
  const [busy, setBusy] = useState(false)
  const badLines = {
    si: new Set<number>(r.discrepancies.map((d: Json) => d.si_evidence.line)),
    bl: new Set<number>(r.discrepancies.map((d: Json) => d.bl_evidence.line)),
  }
  return (
    <Panel
      title={<span className="flex items-center gap-2">{r.shipment_id} · {r.vessel} {r.status === 'OK' ? <Badge tone="good">documents agree</Badge> : <Badge tone="critical">{r.discrepancies.length} discrepancy</Badge>}</span>}
      action={r.discrepancies.length > 0 && (
        <button className="btn" disabled={busy} onClick={async () => { setBusy(true); await api.forwarderReply(r.shipment_id); bump(); setBusy(false) }}>
          Simulate forwarder sending corrected B/L
        </button>
      )}
    >
      {r.discrepancies.map((d: Json) => (
        <div key={d.field} className="mb-4 rounded-lg border border-bad/30 bg-bad/5 p-3 text-[13px]">
          <b>{d.label}:</b> shipping instruction says <span className="num text-ok">{String(d.si_value)}</span> (line {d.si_evidence.line}),
          draft B/L says <span className="num text-bad">{String(d.bl_value)}</span> (line {d.bl_evidence.line}).
          {r.blocks_lc_payment && <> Under LC {r.lc?.lc_number}, the bank will refuse the presentation — payment for {r.invoice_id} slips ~{r.expected_delay_days} days. <span className="text-ink3">{r.lc?.rule}</span></>}
        </div>
      ))}
      <div className="grid gap-3 lg:grid-cols-2">
        <Doc title="Shipping instruction" text={r.si_text} bad={badLines.si} />
        <Doc title="Draft bill of lading" text={r.bl_text} bad={badLines.bl} />
      </div>
    </Panel>
  )
}

function Doc({ title, text, bad }: { title: string; text: string; bad: Set<number> }) {
  return (
    <div>
      <div className="eyebrow mb-1.5">{title}</div>
      <div className="num overflow-x-auto rounded-lg bg-canvas p-3 text-[11.5px] leading-relaxed">
        {text.trimEnd().split('\n').map((line, i) => (
          <div key={i} className={cx('flex gap-3 whitespace-pre', bad.has(i + 1) && 'rounded bg-bad/15 text-bad')}>
            <span className="w-5 shrink-0 select-none text-right text-ink3">{i + 1}</span>{line}
          </div>
        ))}
      </div>
    </div>
  )
}
