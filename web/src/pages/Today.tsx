import { Bot } from 'lucide-react'
import { CashChart } from '../components/CashChart'
import { Badge, Loading, PageHead, Panel, Stat } from '../components/ui'
import { api, rm, shortDate, type Json } from '../lib/api'
import { useLoad, type Page } from '../lib/hooks'

export default function Today({ go }: { go: (p: Page) => void }) {
  const { data, error } = useLoad(api.overview)
  if (!data) return <Loading error={error} />
  const fc = data.forecast
  const gap = fc.funding_gap > 0

  return (
    <>
      <PageHead
        title={`Good morning, ${data.profile.owner.split(' ')[0]}`}
        sub={<>{data.profile.legal_name} · books as of {shortDate(data.as_of)} · {data.llm ? 'Gemini narration on' : 'offline mode (deterministic)'}</>}
        action={<button className="btn btn-primary" onClick={() => go('copilot')}><Bot size={15} /> Ask Tidewise what to do</button>}
      />

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Stat label="Cash today" value={rm(data.cash_myr)} hint={data.accounts.map((a: Json) => `${a.currency} ${a.balance.toLocaleString()}`).join(' · ')} />
        <Stat label="Lowest point (13 wks)" value={rm(fc.trough.balance)} hint={shortDate(fc.trough.date)} tone={fc.trough.balance < 0 ? 'bad' : fc.trough.balance < data.cash_buffer ? 'warn' : 'ok'} />
        <Stat label="Funding gap" value={rm(fc.funding_gap)} hint={gap ? `to stay above your ${rm(data.cash_buffer)} floor from ${shortDate(fc.first_buffer_breach)}` : 'none — you stay above your floor'} tone={gap ? 'bad' : 'ok'} />
        <Stat label="Awaiting your approval" value={data.pending_approvals} hint={<button className="text-brand hover:underline" onClick={() => go('approvals')}>open approvals →</button>} tone={data.pending_approvals ? 'warn' : undefined} />
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-[1.6fr_1fr]">
        <Panel title="Cash over the next 13 weeks" action={<button className="text-[13px] text-brand hover:underline" onClick={() => go('cash')}>details →</button>}>
          <CashChart weekly={fc.weekly} buffer={data.cash_buffer} />
        </Panel>
        <Panel title="What needs attention">
          <div className="flex flex-col gap-3">
            {data.alerts.map((a: Json, i: number) => (
              <div key={i} className="rounded-lg border border-edge/50 bg-canvas/40 p-3">
                <div className="flex items-start gap-2">
                  <Badge tone={a.severity}>{a.severity}</Badge>
                  <div className="text-[13.5px] font-medium leading-snug">{a.title}</div>
                </div>
                <div className="mt-1.5 text-[12.5px] text-ink3">{a.detail}</div>
              </div>
            ))}
          </div>
        </Panel>
      </div>

      <div className="mt-4 panel flex flex-wrap items-center gap-x-6 gap-y-2 px-5 py-3 text-[12.5px] text-ink3">
        <span className={data.audit.chain_intact ? 'text-ok' : 'text-bad'}>
          {data.audit.chain_intact ? '● Audit chain intact' : '● Audit chain broken'}
        </span>
        <span>{data.audit.records} audit records in {data.audit.batches} sealed batches</span>
        <span>Every action passes the T0–T4 permission policy before the sandbox bank executes it.</span>
      </div>
    </>
  )
}
