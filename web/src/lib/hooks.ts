import { useCallback, useEffect, useState, useSyncExternalStore } from 'react'

// A tiny global "data changed" counter: anything that mutates server state
// (chat, approvals, simulations) bumps it, and every page refetches.
let version = 0
const listeners = new Set<() => void>()
export function bump() {
  version++
  listeners.forEach((l) => l())
}
function useVersion() {
  return useSyncExternalStore(
    (l) => { listeners.add(l); return () => listeners.delete(l) },
    () => version,
  )
}

export function useLoad<T>(fn: () => Promise<T>) {
  const v = useVersion()
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const reload = useCallback(() => {
    fn().then((d) => { setData(d); setError(null) }).catch((e) => setError(String(e.message || e)))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  useEffect(() => { reload() }, [reload, v])
  return { data, error, reload }
}

export type Page =
  | 'today' | 'copilot' | 'approvals' | 'cash' | 'orders' | 'invoices' | 'financing' | 'inbox' | 'health' | 'gateway' | 'security' | 'activity' | 'portal'

export const PAGES: Page[] = ['today', 'copilot', 'approvals', 'cash', 'orders', 'invoices', 'financing', 'inbox', 'health', 'gateway', 'security', 'activity', 'portal']

export function useRoute(): [Page, (p: Page) => void] {
  const parse = () => {
    const p = window.location.hash.replace('#/', '') as Page
    return PAGES.includes(p) ? p : 'today'
  }
  const [page, setPage] = useState<Page>(parse)
  useEffect(() => {
    const h = () => setPage(parse())
    window.addEventListener('hashchange', h)
    return () => window.removeEventListener('hashchange', h)
  }, [])
  return [page, (p: Page) => { window.location.hash = `/${p}` }]
}
