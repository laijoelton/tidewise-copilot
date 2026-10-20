import { ArrowRight, Bot } from 'lucide-react'
import { CashChart } from '../components/CashChart'
import { ImpactPanel } from '../components/ImpactPanel'
import { Badge, Loading, Panel, Stat, cx } from '../components/ui'
import { api, rm, shortDate, type Json } from '../lib/api'
import { useLoad, type Page } from '../lib/hooks'

const KIND: Record<string, { label: string; dot: string }> = {
  cause: { label: 'Cause', dot: 'bg-bad' },
  effect: { label: 'Effect', dot: 'bg-warn' },
  impact: { label: 'Impact', dot: 'bg-bad' },
  fix: { label: 'Fix', dot: 'bg-brand' },
}

export default function Today({ go }: { go: (p: Page) => void }) {
  const { data, error } = useLoad(api.overview)
  if (!data) return <Loading error={error} />
  const fc = data.forecast
  const gap = fc.funding_gap > 0
  const hour = new Date().getHours()
  const greet = hour < 12 ? 'Good morning' : hour < 18 ? 'Good afternoon' : 'Good evening'

  return (
    <>
      <header className="daily-brief mb-6">
        <div className="relative z-10 max-w-2xl">
          <div className="brief-kicker"><span /> YOUR DAILY FINANCIAL BRIEF</div>
          <h1 className="mt-4 text-[28px] leading-[1.2] sm:text-[34px]">{greet}, Operating Team.</h1>
          <p className="mt-3 max-w-xl brief-copy text-[14px] leading-relaxed">
            {gap ? <>Your cash is forecast to fall below your safety floor on <strong className="font-semibold">{shortDate(fc.first_buffer_breach)}</strong>. Review the cause and available options below.</> : <>You’re above your cash floor for the next 13 weeks.</>}
          </p>
          <div className="mt-6 flex flex-wrap items-center gap-4">
            <button className="btn btn-primary !px-5 !py-3" onClick={() => go('copilot')}><Bot size={17} /> Review with Copilot <ArrowRight size={15} /></button>
            <span className="brief-meta text-xs">Mamak Duos Sdn Bhd · {shortDate(data.as_of)}</span>
          </div>
        </div>
      </header>
      <div className="section-heading mb-3 flex items-center justify-between"><h2 className="text-[16px]">Your business at a glance</h2><span className="text-[11px] text-ink3">13-week outlook</span></div>

      {data.paused && (
        <div className="mb-5 rounded-xl border border-warn/40 bg-warn/10 px-4 py-3 text-[13.5px] text-ink">
          <b>Autonomy is paused.</b> Tidewise will propose, but nothing runs until you approve it. Resume it on the Security page.
        </div>
      )}

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Stat label="Cash today" value={rm(data.cash_myr)} hint={data.accounts.map((a: Json) => `${a.currency} ${a.balance.toLocaleString()}`).join(' · ')} />
        <Stat label="Lowest point · 13 weeks" value={rm(fc.trough.balance)} hint={shortDate(fc.trough.date)} tone={fc.trough.balance < 0 ? 'bad' : fc.trough.balance < data.cash_buffer ? 'warn' : 'ok'} />
        <Stat label="Funding gap" value={rm(fc.funding_gap)} tone={gap ? 'bad' : 'ok'}
          hint={data.risk ? <button className="text-left hover:underline" onClick={() => go('cash')}>
            {data.risk.p_below_floor > 0 && data.risk.p_below_floor < 0.1 ? (data.risk.p_below_floor * 100).toFixed(1) : Math.round(data.risk.p_below_floor * 100)}% chance of breaching your floor · 1 in 10 futures needs {rm(data.risk.gap.p90)} →</button>
            : gap ? `to stay above your ${rm(data.cash_buffer)} floor` : 'none — no need to borrow'} />
        <Stat label="Waiting for you" value={data.pending_approvals} hint={<button className="text-brand hover:underline" onClick={() => go('approvals')}>open approvals →</button>} tone={data.pending_approvals ? 'warn' : undefined} />
      </div>

      <ImpactPanel go={go} />

      {(data.chain || []).length > 0 && (
        <Panel className="mt-4" title="How this happened">
          <ol className="grid gap-3 lg:grid-cols-[repeat(auto-fit,minmax(0,1fr))]">
            {data.chain.map((step: Json, i: number) => (
              <li key={i} className="relative flex lg:block">
                <div className={cx('h-full flex-1 rounded-xl border p-4', step.kind === 'fix' ? 'border-brand/40 bg-brand/6' : 'border-edge bg-canvas/50')}>
                  <div className="flex items-center gap-2">
                    <span className={cx('h-2 w-2 rounded-full', KIND[step.kind]?.dot)} />
                    <span className="eyebrow">{KIND[step.kind]?.label}</span>
                  </div>
                  <div className="serif mt-2 text-[16px] font-semibold leading-snug">{step.title}</div>
                  <div className="mt-1 text-[12.5px] text-ink3">{step.detail}</div>
                </div>
                {i < data.chain.length - 1 && (
                  <ArrowRight size={16} className="absolute -right-[13px] top-1/2 z-10 hidden -translate-y-1/2 rounded-full bg-card text-ink3 lg:block" />
                )}
              </li>
            ))}
          </ol>
        </Panel>
      )}

      <div className="mt-4 grid gap-4 xl:grid-cols-[1.6fr_1fr]">
        <Panel title="Cash over the next 13 weeks" action={<button className="text-[13px] font-medium text-brand hover:underline" onClick={() => go('cash')}>Run a what-if →</button>}>
          <CashChart weekly={fc.weekly} buffer={data.cash_buffer} />
        </Panel>
        <Panel title="Needs attention">
          <div className="flex flex-col divide-y divide-edge">
            {data.alerts.map((a: Json, i: number) => (
              <div key={i} className="py-3 first:pt-0 last:pb-0">
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

      <div className="mt-4 flex flex-wrap items-center gap-x-6 gap-y-2 text-[12.5px] text-ink3">
        <span className={data.audit.chain_intact ? 'text-ok' : 'text-bad'}>
          ● {data.audit.chain_intact ? 'Audit chain intact' : 'Audit chain broken'}
        </span>
        <span>{data.audit.records} audit records · {data.audit.batches} sealed batches</span>
        <span>{data.llm ? 'Gemini narration on (numbers checked against facts)' : 'Offline mode — deterministic replies'}</span>
      </div>
    </>
  )
}
