import { ChevronRight } from 'lucide-react'
import { useState } from 'react'
import type { Json } from '../lib/api'
import { Check, Outcome, TierBadge, cx } from './ui'

// Renders the agent's trace for one turn: what it read, what it proposed,
// what the policy decided and why, and the sandbox receipt.
export function Steps({ steps }: { steps: Json[] }) {
  return (
    <div className="flex flex-col gap-1.5">
      {steps.map((s, i) => <StepRow key={i} s={s} />)}
    </div>
  )
}

function StepRow({ s }: { s: Json }) {
  const [open, setOpen] = useState(false)
  if (s.kind === 'read')
    return (
      <div className="flex items-start gap-2 text-[12.5px] text-ink3">
        <TierBadge tier={0} />
        <span><span className="text-ink2">{s.tool}</span> — {s.summary}</span>
      </div>
    )
  if (s.kind === 'note')
    return <div className="rounded-md border-l-2 border-warn/60 bg-warn/5 px-3 py-1.5 text-[12.5px] text-ink2">{s.text}</div>

  const d = s.decision
  return (
    <div className={cx('rounded-lg border bg-canvas/40', d.outcome === 'blocked' ? 'border-bad/30' : 'border-edge/60')}>
      <button onClick={() => setOpen(!open)} className="flex w-full items-center gap-2 px-3 py-2 text-left text-[13px]">
        <ChevronRight size={14} className={cx('shrink-0 text-ink3 transition-transform', open && 'rotate-90')} />
        <TierBadge tier={d.tier} />
        <span className="flex-1 truncate">
          <span className="font-medium">{s.type.replaceAll('_', ' ')}</span>
          <span className="text-ink3"> — {s.rationale}</span>
        </span>
        {s.receipt && <span className="num hidden text-[11px] text-ink3 sm:inline">{s.receipt.ref}</span>}
        <Outcome outcome={d.outcome} />
      </button>
      {open && (
        <div className="space-y-1 border-t border-edge/50 px-4 py-3">
          <div className="mb-1 text-[12px] text-ink3">{d.tier_label} · provenance: {s.provenance}</div>
          {d.checks.map((c: Json, i: number) => <Check key={i} {...c} />)}
          {s.receipt && (
            <pre className="num mt-2 overflow-x-auto rounded bg-canvas p-2 text-[11px] text-ink2">{JSON.stringify(s.receipt, null, 2)}</pre>
          )}
          {s.error && <div className="text-[12px] text-bad">Sandbox error: {s.error}</div>}
        </div>
      )}
    </div>
  )
}
