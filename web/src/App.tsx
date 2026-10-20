import { Activity, Banknote, Workflow, Bot, CheckSquare, ClipboardList, FileCheck2, HeartPulse, Inbox, LayoutDashboard, LineChart, ShieldCheck } from 'lucide-react'
import type { ComponentType } from 'react'
import { cx } from './components/ui'
import { api } from './lib/api'
import { useLoad, useRoute, type Page } from './lib/hooks'
import ActivityPage from './pages/Activity'
import Approvals from './pages/Approvals'
import Cash from './pages/Cash'
import Copilot from './pages/Copilot'
import Gateway from './pages/Gateway'
import Invoices from './pages/Invoices'
import Orders from './pages/Orders'
import Portal from './pages/Portal'
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
  { page: 'orders', label: 'Orders & bookings', icon: ClipboardList },
  { page: 'invoices', label: 'Invoice check', icon: FileCheck2 },
  { page: 'financing', label: 'Financing', icon: Banknote },
  { page: 'inbox', label: 'Inbox guard', icon: Inbox },
  { page: 'health', label: 'Business health', icon: HeartPulse },
  { page: 'gateway', label: 'Security gateway', icon: Workflow },
  { page: 'security', label: 'Security', icon: ShieldCheck },
  { page: 'activity', label: 'Activity & audit', icon: Activity },
]

export default function App() {
  const [page, go] = useRoute()
  const { data: approvals } = useLoad(api.approvals)
  const { data: policy } = useLoad(api.policy)
  const pending = (approvals || []).filter((a: { status: string }) => a.status === 'pending').length

  const view = {
    today: <Today go={go} />,
    copilot: <Copilot go={go} />,
    approvals: <Approvals />,
    cash: <Cash go={go} />,
    financing: <Financing />,
    orders: <Orders go={go} />,
    invoices: <Invoices />,
    portal: <Portal go={go} />,
    inbox: <InboxPage />,
    health: <Health />,
    gateway: <Gateway />,
    security: <Security />,
    activity: <ActivityPage />,
  }[page]

  if (page === 'portal')
    return <div className="min-h-full px-4 py-9 sm:px-10">{view}</div>

  return (
    <div className="min-h-full">
      <aside className="fixed inset-y-0 left-0 z-20 flex w-[64px] flex-col bg-[#173f30] text-[#efe8d8] lg:w-[236px]">
        <div className="flex h-[72px] items-center gap-3 px-4">
          {/* Crescent moon over a swell: the moon pulls the tide. */}
          <svg width="32" height="32" viewBox="0 0 32 32" aria-hidden className="shrink-0">
            <defs>
              <clipPath id="tw-tile"><rect width="32" height="32" rx="9" /></clipPath>
              <mask id="tw-moon"><rect width="32" height="32" fill="#fff" /><circle cx="14.8" cy="8.2" r="4.4" fill="#000" /></mask>
            </defs>
            <g clipPath="url(#tw-tile)">
              <rect width="32" height="32" fill="#f5f1e8" />
              <circle cx="12" cy="10.5" r="5" fill="#c98a2b" mask="url(#tw-moon)" />
              <path d="M0 21c3.5 0 4.5-3.5 8-3.5s4.5 3.5 8 3.5 4.5-3.5 8-3.5 4.5 3.5 8 3.5V32H0z" fill="#1f5a43" />
              <path d="M-2 26.5c3.5 0 4.5-2.5 8-2.5s4.5 2.5 8 2.5 4.5-2.5 8-2.5 4.5 2.5 8 2.5 4.5-2.5 8-2.5" stroke="#f5f1e8" strokeWidth="1.5" fill="none" strokeLinecap="round" opacity=".4" />
            </g>
          </svg>
          <div className="hidden lg:block">
            <div className="serif text-[19px] font-semibold leading-none tracking-tight">Tidewise</div>
            <div className="mt-1 text-[11px] text-[#a9bcae]">Finance copilot for exporters</div>
          </div>
        </div>
        <nav className="mt-3 flex flex-col gap-0.5 px-2.5">
          {NAV.map(({ page: p, label, icon: Icon }) => (
            <button
              key={p}
              onClick={() => go(p)}
              title={label}
              className={cx('flex items-center gap-3 rounded-lg px-3 py-2 text-left text-[13.5px] transition-colors',
                page === p ? 'bg-[#f5f1e8] font-medium text-[#173f30]' : 'text-[#cfdccf] hover:bg-white/8 hover:text-white')}
            >
              <Icon size={17} />
              <span className="hidden flex-1 lg:inline">{label}</span>
              {p === 'approvals' && pending > 0 && (
                <span className="num hidden rounded-full bg-[#e0a43a] px-1.5 text-[11px] font-bold text-[#1d1b16] lg:inline">{pending}</span>
              )}
            </button>
          ))}
        </nav>
        <div className="mt-auto hidden space-y-2 p-4 text-[11.5px] leading-relaxed text-[#a9bcae] lg:block">
          {policy?.paused && <div className="rounded-lg bg-[#e0a43a] px-3 py-2 font-semibold text-[#1d1b16]">Autonomy paused — everything waits for you</div>}
          <div className="rounded-lg border border-white/12 px-3 py-2">
            <span className="font-semibold text-[#efe8d8]">Sandbox mode.</span> No real money moves. Seri Rotan Furniture is a fictional business.
          </div>
        </div>
      </aside>
      <main className="pl-[64px] lg:pl-[236px]">
        <div className="mx-auto w-full max-w-[1320px] px-4 py-9 sm:px-10">{view}</div>
      </main>
    </div>
  )
}
