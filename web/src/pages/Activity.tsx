import { useState } from 'react'
import { Empty, Loading, PageHead, Panel } from '../components/ui'
import { api, type Json } from '../lib/api'
import { useLoad } from '../lib/hooks'

export default function Activity() {
  const { data: sb, error } = useLoad(api.sandbox)
  const { data: audit } = useLoad(api.audit)
  const [receipt, setReceipt] = useState<Json | null>(null)
  if (!sb || !audit) return <Loading error={error} />
  const v = audit.verification

  return (
    <>
      <PageHead title="Activity & audit" sub="Every sandbox operation has an idempotency key and an SBX reference. Every proposal, decision, approval and execution is a leaf in a Merkle tree; each turn's root is chained to the last." />
      <div className="grid gap-4 xl:grid-cols-2">
        <Panel title={`Sandbox operations (${sb.log.length})`}>
          {sb.log.length === 0 ? <Empty>No operations yet.</Empty> : (
            <div className="max-h-[420px] overflow-y-auto">
              <table className="data">
                <thead><tr><th>Ref</th><th>Operation</th><th>Detail</th></tr></thead>
                <tbody>
                  {[...sb.log].reverse().map((r: Json) => (
                    <tr key={r.ref + r.at}>
                      <td className="num text-[12px] text-brand">{r.ref}</td>
                      <td>{r.op.replaceAll('_', ' ')}</td>
                      <td className="text-[12px] text-ink3">{r.bill_id || r.about || r.product || r.shipment_id || ''} {r.amount_myr ? `RM ${r.amount_myr.toLocaleString()}` : r.disbursed ? `RM ${r.disbursed.toLocaleString()}` : ''} {r.to || ''}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>
        <Panel title={`Outbox (${sb.outbox.length})`}>
          {sb.outbox.length === 0 ? <Empty>No messages sent.</Empty> : (
            <div className="flex max-h-[420px] flex-col gap-3 overflow-y-auto">
              {sb.outbox.map((m: Json, i: number) => (
                <details key={i} className="rounded-lg border border-edge/50 bg-canvas/40 p-3">
                  <summary className="cursor-pointer text-[13px]"><span className="text-ink3">to {m.to}</span> — {m.subject}</summary>
                  <pre className="mt-2 whitespace-pre-wrap text-[12px] text-ink2">{m.body}</pre>
                </details>
              ))}
            </div>
          )}
        </Panel>
      </div>

      <Panel className="mt-4" title="Audit trail"
        action={<span className={`text-[13px] ${v.chain_intact && v.all_inclusion_proofs_valid ? 'text-ok' : 'text-bad'}`}>
          {v.chain_intact && v.all_inclusion_proofs_valid ? '✓' : '✕'} {v.detail} · {v.records} records</span>}>
        <div className="grid gap-4 lg:grid-cols-[1.6fr_1fr]">
          <div className="max-h-[480px] overflow-y-auto">
            <table className="data">
              <thead><tr><th>#</th><th>Kind</th><th>Actor</th><th>Summary</th></tr></thead>
              <tbody>
                {[...audit.records].reverse().map((r: Json) => (
                  <tr key={r.id} className="cursor-pointer hover:bg-sub/50" onClick={async () => setReceipt(await api.receipt(r.id).catch(() => null))}>
                    <td className="num text-ink3">{r.id}</td>
                    <td>{r.kind.replaceAll('_', ' ')}</td>
                    <td className="text-ink3">{r.actor}</td>
                    <td className="max-w-[420px] truncate text-[12px] text-ink3">{r.data.type || r.data.tool || r.data.text || r.data.approval_id || ''} {r.data.outcome ? `→ ${r.data.outcome}` : ''}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div>
            <div className="eyebrow mb-2">Inclusion proof (click a record)</div>
            {receipt ? (
              <div className="num rounded-lg bg-canvas p-3 text-[11px] text-ink2">
                <div className={receipt.verified ? 'text-ok' : 'text-bad'}>{receipt.verified ? '✓ proof verifies against root' : '✕ proof failed'}</div>
                <div className="mt-2 break-all">leaf {receipt.leaf_hash}</div>
                {receipt.proof.map(([side, h]: [string, string], i: number) => <div key={i} className="break-all text-ink3">{side} {h}</div>)}
                <div className="mt-2 break-all">root {receipt.root} (batch {receipt.batch_index})</div>
              </div>
            ) : <Empty>Pick a record to see its Merkle proof.</Empty>}
          </div>
        </div>
      </Panel>
    </>
  )
}
