import type { Json } from '../lib/api'
import { cx } from './ui'

// One action's path through the ten security gates (tidewise/pipeline.py).
export const GATE_STYLE: Record<string, { cell: string; label: string }> = {
  pass: { cell: 'bg-ok text-white border-ok', label: 'passed' },
  escalate: { cell: 'bg-warn text-white border-warn', label: 'sent to owner' },
  waiting: { cell: 'bg-card text-warn border-warn border-dashed', label: 'pending' },
  stop: { cell: 'bg-bad text-white border-bad', label: 'stopped' },
  skip: { cell: 'bg-sub text-ink3 border-edge', label: 'not applicable' },
}

export function GateStrip({ gates, size = 'sm' }: { gates: Json[]; size?: 'sm' | 'md' }) {
  if (!gates?.length) return null
  return (
    <div className="flex items-center gap-[3px]" aria-label="Security gates">
      {gates.map((g: Json) => (
        <span key={g.id} title={`G${g.id} ${g.name}: ${GATE_STYLE[g.status]?.label} — ${g.detail}`}
          className={cx('num inline-flex items-center justify-center rounded-[5px] border font-semibold',
            size === 'sm' ? 'h-[18px] w-[18px] text-[9.5px]' : 'h-7 w-7 text-[11.5px]', GATE_STYLE[g.status]?.cell)}>
          {g.id}
        </span>
      ))}
    </div>
  )
}

export function GateTrace({ gates }: { gates: Json[] }) {
  if (!gates?.length) return null
  const shown = gates.filter((g: Json) => g.status !== 'skip')
  return (
    <div className="rounded-xl border border-edge bg-canvas/50 p-3">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <span className="eyebrow">Security pipeline</span>
        <GateStrip gates={gates} size="md" />
      </div>
      <div className="space-y-1">
        {shown.map((g: Json) => (
          <div key={g.id} className="flex gap-2 text-[12.5px]">
            <span className={cx('num mt-[1px] inline-flex h-[18px] min-w-[34px] items-center justify-center rounded-[5px] border px-1 text-[10px] font-semibold', GATE_STYLE[g.status]?.cell)}>G{g.id}</span>
            <span><span className="font-medium text-ink">{g.name}</span> <span className="text-ink3">— {GATE_STYLE[g.status]?.label}: {g.detail}</span></span>
          </div>
        ))}
      </div>
    </div>
  )
}

export function GateLegend() {
  return (
    <div className="flex flex-wrap gap-x-4 gap-y-1 text-[12px] text-ink3">
      {(['pass', 'escalate', 'waiting', 'stop', 'skip'] as const).map((k) => (
        <span key={k} className="inline-flex items-center gap-1.5">
          <span className={cx('inline-block h-3 w-3 rounded-[3px] border', GATE_STYLE[k].cell)} />{GATE_STYLE[k].label}
        </span>
      ))}
    </div>
  )
}
