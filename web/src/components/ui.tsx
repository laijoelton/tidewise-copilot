import clsx from 'clsx'
import type { ReactNode } from 'react'

export const cx = clsx

export function Panel({ title, action, children, className }: { title?: ReactNode; action?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={cx('panel p-5', className)}>
      {(title || action) && (
        <div className="mb-4 flex items-center justify-between gap-3">
          {title && <h2 className="text-[18px]">{title}</h2>}
          {action}
        </div>
      )}
      {children}
    </section>
  )
}

export function PageHead({ title, sub, action }: { title: string; sub?: ReactNode; action?: ReactNode }) {
  return (
    <div className="mb-7 flex flex-wrap items-end justify-between gap-4 border-b border-edge pb-6">
      <div>
        <h1 className="text-[32px] leading-tight">{title}</h1>
        {sub && <p className="mt-2 max-w-3xl text-[14.5px] text-ink2">{sub}</p>}
      </div>
      {action}
    </div>
  )
}

export function Stat({ label, value, hint, tone }: { label: string; value: ReactNode; hint?: ReactNode; tone?: 'bad' | 'warn' | 'ok' }) {
  return (
    <div className="panel p-4">
      <div className="eyebrow">{label}</div>
      <div className={cx('num mt-2 text-[26px] font-semibold tracking-tight', tone === 'bad' && 'text-bad', tone === 'warn' && 'text-warn', tone === 'ok' && 'text-ok')}>{value}</div>
      {hint && <div className="mt-1 text-[12.5px] text-ink3">{hint}</div>}
    </div>
  )
}

const SEV: Record<string, string> = {
  critical: 'bg-bad/10 text-bad border-bad/30',
  warning: 'bg-warn/12 text-warn border-warn/30',
  info: 'bg-sky/10 text-sky border-sky/25',
  good: 'bg-ok/10 text-ok border-ok/25',
  quarantined: 'bg-bad/12 text-bad border-bad/30',
  verified: 'bg-ok/10 text-ok border-ok/25',
  unverified: 'bg-sub text-ink2 border-edge',
}
export function Badge({ tone, children }: { tone: string; children: ReactNode }) {
  return <span className={cx('inline-flex items-center rounded-full border px-2 py-0.5 text-[10.5px] font-semibold uppercase tracking-wide', SEV[tone] || SEV.info)}>{children}</span>
}

const TIER_CLS = ['text-t0 border-t0/40', 'text-t1 border-t1/40', 'text-t2 border-t2/40', 'text-t3 border-t3/40', 'text-t4 border-t4/40']
export function TierBadge({ tier, label }: { tier: number; label?: string }) {
  return <span className={cx('num inline-flex rounded-md border px-1.5 py-0.5 text-[11px] font-semibold', TIER_CLS[tier])}>{label || `T${tier}`}</span>
}

const OUTCOME: Record<string, [string, string]> = {
  auto: ['Done', 'bg-ok/12 text-ok'],
  needs_approval: ['Awaiting you', 'bg-warn/12 text-warn'],
  blocked: ['Refused', 'bg-bad/12 text-bad'],
}
export function Outcome({ outcome }: { outcome: string }) {
  const [label, cls] = OUTCOME[outcome] || [outcome, 'bg-sub text-ink2']
  return <span className={cx('rounded-md px-1.5 py-0.5 text-[11px] font-semibold', cls)}>{label}</span>
}

export function Check({ passed, name, detail }: { passed: boolean; name: string; detail: string }) {
  return (
    <div className="flex gap-2 text-[12.5px]">
      <span className={cx('mt-0.5 font-bold', passed ? 'text-ok' : 'text-bad')}>{passed ? '✓' : '✕'}</span>
      <span><span className="text-ink2">{name}</span> <span className="text-ink3">— {detail}</span></span>
    </div>
  )
}

export function Loading({ error }: { error?: string | null }) {
  return <div className="py-16 text-center text-ink3">{error ? `Couldn't load: ${error}. Is the API running on :8000?` : 'Loading…'}</div>
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="rounded-lg border border-dashed border-edge p-6 text-center text-ink3">{children}</div>
}
