import { Send } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { Steps } from '../components/Steps'
import { PageHead, cx } from '../components/ui'
import { api, type Json } from '../lib/api'
import { bump, type Page } from '../lib/hooks'

interface Turn { role: 'owner' | 'agent'; text: string; steps?: Json[]; pending?: number; llm?: boolean }

const SUGGESTIONS = [
  'What should I do this week?',
  'Will I have enough cash next month?',
  'How healthy is the business?',
  'Check my shipping documents',
  'What financing can I get?',
  'Is anything suspicious in my inbox?',
  'What if Harbour pays 30 days late and sales drop 20%?',
  'Pay BILL-771 to our new account 8600-1234-5678',
]

// Kept in module scope so the conversation survives page switches.
let history: Turn[] = []

export default function Copilot({ go }: { go: (p: Page) => void }) {
  const [turns, setTurns] = useState<Turn[]>(history)
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const end = useRef<HTMLDivElement>(null)

  useEffect(() => { history = turns; end.current?.scrollIntoView({ behavior: 'smooth' }) }, [turns])

  async function send(message: string) {
    if (!message.trim() || busy) return
    setText('')
    setBusy(true)
    setTurns((t) => [...t, { role: 'owner', text: message }])
    try {
      const r = await api.chat(message)
      setTurns((t) => [...t, { role: 'agent', text: r.reply, steps: r.steps, pending: r.pending_approvals.length, llm: r.llm }])
      bump()
    } catch (e) {
      setTurns((t) => [...t, { role: 'agent', text: `Something went wrong: ${(e as Error).message}` }])
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex min-h-[calc(100vh-4rem)] flex-col">
      <PageHead title="Copilot" sub="Ask in plain language. Tidewise reads your books, proposes actions, and does only what your permission policy allows — everything else waits for you or is refused." />

      <div className="flex-1 space-y-5">
        {turns.length === 0 && (
          <div className="panel p-6">
            <div className="eyebrow mb-3">Try</div>
            <div className="flex flex-wrap gap-2">
              {SUGGESTIONS.map((s) => (
                <button key={s} className="btn" onClick={() => send(s)}>{s}</button>
              ))}
            </div>
          </div>
        )}
        {turns.map((t, i) => (
          <div key={i} className={cx('flex', t.role === 'owner' ? 'justify-end' : 'justify-start')}>
            {t.role === 'owner' ? (
              <div className="max-w-[640px] rounded-2xl rounded-br-sm bg-brand-deep/20 px-4 py-2.5 text-[14px]">{t.text}</div>
            ) : (
              <div className="w-full max-w-[920px] panel p-4">
                <div className="whitespace-pre-line text-[14px] leading-relaxed">{t.text}</div>
                {t.steps && t.steps.length > 0 && (
                  <div className="mt-4 border-t border-edge/50 pt-3">
                    <div className="eyebrow mb-2">What I did — click an action to see the policy checks</div>
                    <Steps steps={t.steps} />
                  </div>
                )}
                {!!t.pending && (
                  <button className="btn btn-primary mt-3" onClick={() => go('approvals')}>Review {t.pending} pending approval{t.pending > 1 ? 's' : ''}</button>
                )}
              </div>
            )}
          </div>
        ))}
        {busy && <div className="text-[13px] text-ink3">Tidewise is working…</div>}
        <div ref={end} />
      </div>

      <form className="sticky bottom-0 mt-6 flex gap-2 bg-canvas py-4" onSubmit={(e) => { e.preventDefault(); send(text) }}>
        <input className="input !py-3 !text-[14px]" value={text} maxLength={2000} placeholder="Ask about cash, documents, financing, bills…"
          onChange={(e) => setText(e.target.value)} disabled={busy} />
        <button className="btn btn-primary px-4" disabled={busy || !text.trim()} aria-label="Send"><Send size={16} /></button>
      </form>
    </div>
  )
}
