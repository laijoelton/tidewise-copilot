import { Activity, Banknote, Bot, CheckSquare, FileSearch, HeartPulse, Inbox, LayoutDashboard, LineChart, ShieldCheck } from 'lucide-react'
import type { ComponentType } from 'react'
import { cx } from './components/ui'
import { api } from './lib/api'
import { useLoad, useRoute, type Page } from './lib/hooks'
import ActivityPage from './pages/Activity'
import Approvals from './pages/Approvals'
import Cash from './pages/Cash'
import Copilot from './pages/Copilot'
import Documents from './pages/Documents'
import Financing from './pages/Financing'
import Health from './pages/Health'
import InboxPage from './pages/Inbox'
import Security from './pages/Security'
import Today from './pages/Today'

const NAV: { page: Page; label: string; icon: ComponentType<{ size?: number }> }[] = [
  { page: 'today', label: 'Today', icon: LayoutDashboard },
  { page: 'copilot', label: 'Copilot', icon: Bot },
  { page: 'approvals', label: 'Approvals', icon: CheckSquare },
  { page: 'cash', label: 'Cash flow', icon: LineChart },
  { page: 'financing', label: 'Financing', icon: Banknote },
  { page: 'documents', label: 'Trade documents', icon: FileSearch },
  { page: 'inbox', label: 'Inbox guard', icon: Inbox },
  { page: 'health', label: 'Business health', icon: HeartPulse },
  { page: 'security', label: 'Security', icon: ShieldCheck },
  { page: 'activity', label: 'Activity & audit', icon: Activity },
]

export default function App() {
  const [page, go] = useRoute()
  const { data: approvals } = useLoad(api.approvals)
  const pending = (approvals || []).filter((a: { status: string }) => a.status === 'pending').length

  const view = {
    today: <Today go={go} />,
    copilot: <Copilot go={go} />,
    approvals: <Approvals />,
    cash: <Cash />,
    financing: <Financing />,
    documents: <Documents />,
    inbox: <InboxPage />,
    health: <Health />,
    security: <Security />,
    activity: <ActivityPage />,
  }[page]

  return (
    <div className="min-h-full">
      <aside className="fixed inset-y-0 left-0 z-20 flex w-[64px] flex-col border-r border-edge/50 bg-card lg:w-[232px]">
        <div className="flex h-16 items-center gap-2.5 px-4">
          <svg width="28" height="28" viewBox="0 0 32 32" aria-hidden>
            <rect width="32" height="32" rx="8" fill="#14b8a6" />
            <path d="M5 19c3.5 0 3.5-4 7-4s3.5 4 7 4 3.5-4 7-4" stroke="#04211d" strokeWidth="2.6" fill="none" strokeLinecap="round" />
            <path d="M5 24c3.5 0 3.5-4 7-4s3.5 4 7 4 3.5-4 7-4" stroke="#04211d" strokeWidth="2.6" fill="none" strokeLinecap="round" opacity=".45" />
          </svg>
          <div className="hidden lg:block">
            <div className="text-[15px] font-semibold leading-none">Tidewise</div>
            <div className="mt-1 text-[11px] text-ink3">SME finance copilot</div>
          </div>
        </div>
        <nav className="mt-2 flex flex-col gap-0.5 px-2">
          {NAV.map(({ page: p, label, icon: Icon }) => (
            <button
              key={p}
              onClick={() => go(p)}
              title={label}
              className={cx('flex items-center gap-3 rounded-lg px-3 py-2 text-left text-[13.5px] transition-colors',
                page === p ? 'bg-brand/12 text-brand' : 'text-ink2 hover:bg-sub hover:text-ink')}
            >
              <Icon size={17} />
              <span className="hidden flex-1 lg:inline">{label}</span>
              {p === 'approvals' && pending > 0 && (
                <span className="num hidden rounded-full bg-warn px-1.5 text-[11px] font-bold text-canvas lg:inline">{pending}</span>
              )}
            </button>
          ))}
        </nav>
        <div className="mt-auto hidden p-4 text-[11.5px] leading-relaxed text-ink3 lg:block">
          Sandbox mode — no real money moves. Demo business: Seri Rotan Furniture Sdn Bhd (fictional).
        </div>
      </aside>
      <main className="pl-[64px] lg:pl-[232px]">
        <div className="mx-auto w-full max-w-[1400px] px-4 py-8 sm:px-8">{view}</div>
      </main>
    </div>
  )
}
