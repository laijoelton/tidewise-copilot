import { ArrowDown, ArrowRight } from 'lucide-react'
import { useState } from 'react'
import type { Json } from '../lib/api'
import { api } from '../lib/api'
import { useLoad } from '../lib/hooks'
import { GateLegend } from './GateTrace'
import { Panel, cx } from './ui'

// The ten-gate security pipeline, drawn as two lanes: outside content is
// screened (G1–G4) and labelled; every action then runs G5–G10.
export function Pipeline() {
  const { data } = useLoad(api.pipeline)
  const [open, setOpen] = useState<number | null>(null)
  if (!data) return null
  const gates: Json[] = data.gates
  const inbound = gates.filter((g) => g.lane === 'inbound')
  const action = gates.filter((g) => g.lane === 'action')
  const sel = gates.find((g) => g.id === open)

  return (
    <Panel title="The 10-gate security pipeline" action={<div className="flex flex-wrap items-center gap-3"><GateLegend /><a href="#/gateway" className="btn btn-primary !py-1.5">▶ Run it live</a></div>}>
      <p className="-mt-1 mb-5 max-w-3xl text-[13.5px] text-ink2">
        Everything from outside passes gates 1–4 and gets a trust label. Every action the agent proposes then passes gates 5–10.
        A gate can let it through, send it to the owner, or stop it — and every action in the Copilot shows its own path through these gates.
      </p>

      <Lane title="Outside content — emails, customer bookings" gates={inbound} stats={data.stats} open={open} setOpen={setOpen} />
      <div className="my-3 flex items-center gap-2 pl-4 text-[12.5px] text-ink3">
        <ArrowDown size={16} /> trusted, outside or quarantined — this label travels with every action built from it
      </div>
      <Lane title="Actions — anything that moves money or sends a message" gates={action} stats={data.stats} open={open} setOpen={setOpen} />

      {sel && (
        <div className="mt-5 grid gap-4 rounded-xl border border-brand/30 bg-brand/5 p-5 lg:grid-cols-[1.2fr_1fr]">
          <div>
            <div className="eyebrow">Gate {sel.id} · {sel.module}</div>
            <div className="serif mt-1 text-[20px] font-semibold">{sel.name}</div>
            <p className="mt-2 text-[13.5px] text-ink2"><b className="text-ink">Does:</b> {sel.does}</p>
            <p className="mt-1 text-[13.5px] text-ink2"><b className="text-ink">Stops:</b> {sel.stops}</p>
          </div>
          <div>
            <div className="eyebrow mb-2">Proven by red-team attacks</div>
            {(data.attacks[sel.id] || []).length === 0
              ? <p className="text-[13px] text-ink3">No attack is caught first at this gate.</p>
              : (data.attacks[sel.id] as Json[]).map((a) => (
                <div key={a.id} className="text-[13px]"><span className="num text-ink3">{a.id}</span> {a.name}</div>
              ))}
          </div>
        </div>
      )}
    </Panel>
  )
}

function Lane({ title, gates, stats, open, setOpen }: { title: string; gates: Json[]; stats: Json; open: number | null; setOpen: (n: number | null) => void }) {
  return (
    <div>
      <div className="eyebrow mb-2">{title}</div>
      <div className="flex flex-col gap-2 lg:flex-row lg:items-stretch">
        {gates.map((g, i) => {
          const s = stats[g.id] || {}
          return (
            <div key={g.id} className="flex flex-1 items-center gap-2 lg:min-w-0">
              <button onClick={() => setOpen(open === g.id ? null : g.id)}
                className={cx('flex h-full w-full flex-col rounded-xl border bg-card p-3 text-left transition-colors hover:border-brand/50',
                  open === g.id ? 'border-brand ring-2 ring-brand/15' : 'border-edge')}>
                <div className="flex items-center gap-2">
                  <span className="num inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-[#173f30] text-[11px] font-bold text-[#f5f1e8]">{g.id}</span>
                  <span className="text-[13.5px] font-semibold leading-tight">{g.name}</span>
                </div>
                <div className="mt-2 line-clamp-3 text-[11.5px] leading-snug text-ink3">{g.stops}</div>
                <div className="mt-auto flex gap-2 pt-2 text-[11px]">
                  {s.stop > 0 && <span className="rounded-full bg-bad/10 px-1.5 font-semibold text-bad">{s.stop} stopped</span>}
                  {s.escalate > 0 && <span className="rounded-full bg-warn/12 px-1.5 font-semibold text-warn">{s.escalate} to owner</span>}
                  {s.waiting > 0 && <span className="rounded-full border border-dashed border-warn px-1.5 font-semibold text-warn">{s.waiting} waiting</span>}
                  {!s.stop && !s.escalate && !s.waiting && <span className="text-ink3">{s.pass ? `${s.pass} passed` : 'idle'}</span>}
                </div>
              </button>
              {i < gates.length - 1 && <ArrowRight size={14} className="hidden shrink-0 text-ink3 lg:block" />}
            </div>
          )
        })}
      </div>
    </div>
  )
}
