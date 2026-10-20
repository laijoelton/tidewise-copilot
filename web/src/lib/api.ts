// Thin client for the Tidewise API. Approvals send the owner token, which the
// owner pastes once per browser session (it is printed by the API at start-up).

/* eslint-disable @typescript-eslint/no-explicit-any */
export type Json = any

const TOKEN_KEY = 'tidewise.ownerToken'

export function getOwnerToken(): string {
  try { return sessionStorage.getItem(TOKEN_KEY) || '' } catch { return '' }
}
export function setOwnerToken(t: string) {
  try { sessionStorage.setItem(TOKEN_KEY, t) } catch { /* private mode */ }
}

async function call(path: string, init?: RequestInit & { owner?: boolean }): Promise<Json> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  if (init?.owner) headers['X-Owner-Token'] = getOwnerToken()
  const res = await fetch(path, { ...init, headers })
  const body = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(typeof body.detail === 'string' ? body.detail : `HTTP ${res.status}`)
  return body
}

export const api = {
  overview: () => call('/api/overview'),
  forecast: () => call('/api/forecast'),
  analysis: () => call('/api/analysis'),
  matching: () => call('/api/matching'),
  orders: () => call('/api/orders'),
  financing: () => call('/api/financing'),
  inbox: () => call('/api/inbox'),
  ledger: () => call('/api/ledger'),
  policy: () => call('/api/policy'),
  audit: () => call('/api/audit'),
  receipt: (id: number) => call(`/api/audit/receipt/${id}`),
  sandbox: () => call('/api/sandbox'),
  approvals: () => call('/api/approvals'),
  chat: (message: string, lang?: string) => call('/api/chat', { method: 'POST', body: JSON.stringify({ message, lang: lang || null }) }),
  risk: () => call('/api/risk'),
  market: () => call('/api/market'),
  riskPlan: (target: number) => call(`/api/risk/plan?target=${target}`),
  approve: (id: string, confirm_amount?: number) =>
    call(`/api/approvals/${id}/approve`, { method: 'POST', owner: true, body: JSON.stringify({ confirm_amount }) }),
  reject: (id: string) => call(`/api/approvals/${id}/reject`, { method: 'POST', owner: true, body: '{}' }),
  customerApproves: (invoice_id: string) =>
    call('/api/simulate/customer-approves', { method: 'POST', body: JSON.stringify({ invoice_id }) }),
  payDeposit: (order_id: string) =>
    call('/api/simulate/deposit', { method: 'POST', body: JSON.stringify({ order_id }) }),
  book: (booking: Json) => call('/api/bookings', { method: 'POST', body: JSON.stringify(booking) }),
  redteam: () => call('/api/redteam', { method: 'POST', body: '{}' }),
  ml: () => call('/api/ml'),
  whatif: (scenario: Json) => call('/api/whatif', { method: 'POST', body: JSON.stringify(scenario) }),
  setPaused: (paused: boolean) => call('/api/autonomy', { method: 'POST', owner: true, body: JSON.stringify({ paused }) }),
  reset: () => call('/api/reset', { method: 'POST', owner: true, body: '{}' }),
}

export const rm = (n: number | null | undefined, digits = 0) =>
  n == null ? '—' : `RM ${n.toLocaleString('en-MY', { minimumFractionDigits: digits, maximumFractionDigits: digits })}`
export const money = (n: number, ccy: string) => `${ccy} ${n.toLocaleString('en-MY', { maximumFractionDigits: 0 })}`
export const pct = (n: number | null | undefined) => (n == null ? '—' : `${Math.round(n * 1000) / 10}%`)
export const shortDate = (iso: string) =>
  new Date(iso + 'T00:00:00').toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })
