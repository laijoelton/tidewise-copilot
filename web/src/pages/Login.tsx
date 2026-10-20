import { useState, type FormEvent } from 'react'
import { ArrowRight, Eye, EyeOff, KeyRound, ShieldCheck, ChartNoAxesCombined, ClipboardCheck, History } from 'lucide-react'
import { setOwnerToken, verifyOwnerToken } from '../lib/api'

export default function Login({ onLogin }: { onLogin: () => void }) {
  const [token, setToken] = useState('')
  const [visible, setVisible] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    if (busy) return
    setBusy(true)
    setError('')
    try {
      await verifyOwnerToken(token.trim())
      setOwnerToken(token.trim())
      onLogin()
    } catch (e) {
      setError(e instanceof TypeError ? 'Cannot reach Tidewise. Check the API connection and try again.' : e instanceof Error ? e.message : 'Unable to sign in. Please try again.')
    } finally { setBusy(false) }
  }

  return (
    <main className="login-page">
      <header className="login-brand"><img src="/tunku-logo.png" alt="Tunku Abdul Rahman portrait, Tidewise logo" width="48" height="48" /><div><strong>Tidewise</strong><span>Finance copilot</span></div></header>
      <div className="login-layout">
        <section className="login-intro" aria-labelledby="intro-title">
          <div className="login-identity">
            <img className="login-intro-logo" src="/tunku-logo.png" alt="Tunku Abdul Rahman illustrated Tidewise logo" width="80" height="80" />
            <div><span className="login-identity-name">Tidewise</span><span className="login-identity-sub">Business finance copilot</span></div>
          </div>
          <div className="login-intro-body">
            <div className="login-company-label">Mamak Duos Sdn Bhd</div>
            <h1 id="intro-title">A clear view.<br /><span>A confident next move.</span></h1>
            <p>Stay on top of cash flow, manage approvals, and keep your team moving.</p>
            <div className="login-capabilities">
              <div><ChartNoAxesCombined size={19} aria-hidden="true" /><span>Cash flow</span><small>13-week outlook</small></div>
              <div><ClipboardCheck size={19} aria-hidden="true" /><span>Approvals</span><small>Your team in control</small></div>
              <div><History size={19} aria-hidden="true" /><span>Audit trail</span><small>Every step recorded</small></div>
            </div>
          </div>
          <div className="login-team-strip"><span className="login-team-avatar">OT</span><div><strong>Operating Team</strong><span>Mamak Duos Sdn Bhd</span></div><span className="login-team-label">TEAM ACCESS</span></div>
        </section>
        <section className="login-card" aria-labelledby="login-title">
          <div className="login-card-header"><KeyRound size={19} /><span>Team workspace</span></div>
          <form onSubmit={submit} className="p-6 sm:p-8">
            <h2 id="login-title" className="text-[26px]">Sign in</h2>
            <p className="mt-2 mb-7 text-ink2">Access the Operating Team workspace.</p>
            <label htmlFor="access-token" className="mb-2 block text-[13px] font-semibold">Owner access token</label>
            <div className="login-token-field">
              <input id="access-token" className="input !pr-12" type={visible ? 'text' : 'password'} value={token} onChange={e => setToken(e.target.value)} required autoComplete="off" autoCapitalize="none" spellCheck={false} placeholder="Enter your access token" aria-describedby="token-help" disabled={busy} />
              <button type="button" onClick={() => setVisible(!visible)} aria-label={visible ? 'Hide access token' : 'Show access token'} aria-pressed={visible} className="login-reveal">{visible ? <EyeOff size={18} /> : <Eye size={18} />}</button>
            </div>
            <p id="token-help" className="mt-2 text-xs text-ink3">Use the owner token shown in the Tidewise API terminal at startup.</p>
            {error && <p role="alert" className="mt-4 rounded border border-bad/25 bg-bad/5 p-3 text-[13px] text-bad">{error}</p>}
            <button type="submit" className="btn btn-primary mt-6 w-full justify-center !py-3" disabled={busy || !token.trim()}>{busy ? 'Signing in…' : 'Sign in'}<ArrowRight size={16} /></button>
            <div className="mt-6 flex items-start gap-2 border-t border-edge pt-5 text-xs text-ink3"><ShieldCheck size={16} className="shrink-0" /><p>Sandbox workspace. No real money moves. Your token is kept in this browser tab for the session.</p></div>
          </form>
        </section>
      </div>
      <footer className="login-footer">Tidewise · Mamak Duos Sdn Bhd</footer>
    </main>
  )
}
