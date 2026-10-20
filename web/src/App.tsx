import { ChevronRight, Activity, Banknote, Workflow, Bot, CheckSquare, ClipboardList, FileCheck2, HeartPulse, Inbox, LayoutDashboard, LineChart, ShieldCheck } from 'lucide-react'
import { useEffect, useState, type ComponentType } from 'react'
import { cx } from './components/ui'
import { api, clearOwnerToken, getOwnerToken, verifyOwnerToken } from './lib/api'
import Login from './pages/Login'
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
  const [page] = useRoute()
  const [signedIn, setSignedIn] = useState(false)
  const [checking, setChecking] = useState(() => Boolean(getOwnerToken()))
  useEffect(() => {
    const token = getOwnerToken()
    if (!token) return
    let active = true
    verifyOwnerToken(token).then(() => { if (active) setSignedIn(true) })
      .catch(() => { if (active) clearOwnerToken() })
      .finally(() => { if (active) setChecking(false) })
    return () => { active = false }
  }, [])
  if (page === 'portal') return <div className="min-h-full px-4 py-9 sm:px-10"><Portal go={p => { window.location.hash = `/${p}` }} /></div>
  if (checking) return <div className="min-h-screen grid place-items-center text-ink2" role="status">Checking your session…</div>
  if (!signedIn) return <Login onLogin={() => setSignedIn(true)} />
  return <Workspace onSignOut={() => { clearOwnerToken(); setSignedIn(false) }} />
}

function Workspace({ onSignOut }: { onSignOut: () => void }) {
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
      <aside className="app-sidebar fixed inset-y-0 left-0 z-20 flex w-[64px] flex-col lg:w-[248px]">
        <div className="sidebar-brand flex h-[84px] items-center gap-3 px-4">
          <img src="/tunku-logo.png" alt="Tunku Abdul Rahman portrait, Tidewise logo" className="brand-portrait" width="40" height="40" />
          <div className="hidden lg:block">
            <div className="text-[19px] font-extrabold leading-none tracking-tight">Tidewise</div>
            <div className="mt-1 sidebar-tagline text-[11px]">Finance copilot</div>
          </div>
        </div>
        <nav aria-label="Main navigation" className="sidebar-nav mt-1 flex flex-col gap-1 px-3">
          {NAV.map(({ page: p, label, icon: Icon }) => (
            <div key={p}>
            {["today", "cash", "gateway"].includes(p) && <div className="nav-group hidden lg:block">{p === "today" ? "WORKSPACE" : p === "cash" ? "FINANCIAL OPERATIONS" : "TRUST & CONTROL"}</div>}
            <button
              key={p}
              onClick={() => go(p)}
              title={label}
              aria-current={page === p ? 'page' : undefined}
              className={cx('sidebar-link w-full flex items-center gap-3 rounded-lg px-3 py-2 text-left text-[13.5px] transition-colors',
                page === p ? 'nav-active font-semibold' : '')}
            >
              <Icon size={17} />
              <span className="hidden flex-1 lg:inline">{label}</span>
              {p === 'approvals' && pending > 0 && (
                <span className="nav-count num hidden rounded-sm px-1.5 text-[11px] font-bold lg:inline">{pending}</span>
              )}
            </button>
            </div>
          ))}
        </nav>
        <div className="sidebar-note mt-auto hidden space-y-2 p-4 text-[11.5px] leading-relaxed lg:block">
          {policy?.paused && <div className="rounded-lg bg-white px-3 py-2 font-semibold text-[#dc791b]">Autonomy paused — everything waits for you</div>}
          <div className="sidebar-note-box px-3 py-2">
            <strong>Sandbox mode.</strong> No real money moves. Mamak Duos Sdn Bhd is a fictional business.
          </div>
        </div>
      </aside>
      <main className="pl-[64px] lg:pl-[248px]">
        <div className="workspace-bar flex min-h-[70px] flex-wrap items-center justify-between gap-3 border-b border-edge px-5 py-3 sm:px-10">
          <div className="flex items-center gap-2 text-xs text-ink3"><span className="hidden sm:inline">Workspace</span><ChevronRight size={13} className="hidden sm:block" /><span className="font-semibold text-ink">{NAV.find(n => n.page === page)?.label}</span></div>
          <div className="flex items-center gap-4"><span className="sandbox-pill"><span />Sandbox workspace</span><span className="owner-avatar" title="Operating Team">OT</span><button className="btn text-xs" onClick={onSignOut}>Sign out</button></div>
        </div>
        <div className="mx-auto w-full max-w-[1440px] px-4 py-8 sm:px-9">{view}</div>
      </main>
    </div>
  )
}
