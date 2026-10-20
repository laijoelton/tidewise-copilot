import { Send } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { Steps } from '../components/Steps'
import { PageHead, cx } from '../components/ui'
import { api, type Json } from '../lib/api'
import { bump, type Page } from '../lib/hooks'

interface Turn { role: 'owner' | 'agent'; text: string; steps?: Json[]; pending?: number; llm?: boolean }

type Lang = 'auto' | 'en' | 'zh' | 'ms'
const LANGS: [Lang, string][] = [['auto', 'Auto'], ['en', 'EN'], ['zh', '中文'], ['ms', 'BM']]

const SUGGESTIONS_ZH = [
  '这周我该做什么？',
  '我跌破安全线的概率有多大？',
  '让我95%安全',
  '如果 Harbour 晚付30天会怎样？',
  '收件箱里有可疑邮件吗？',
  '我能获得什么融资？',
]
const SUGGESTIONS_MS = [
  'Apa patut saya buat minggu ini?',
  'Berapa peluang tunai saya jatuh bawah paras minimum?',
  'Jadikan saya 95% selamat',
  'Bagaimana jika Harbour bayar 30 hari lewat?',
  'Ada e-mel mencurigakan dalam peti masuk?',
  'Pembiayaan apa yang saya boleh dapat?',
]

const SUGGESTIONS = [
  'What should I do this week?',
  'How likely am I to run short of cash?',
  'Make me 95% safe',
  'Will I have enough cash next month?',
  'How healthy is the business?',
  'Check my invoices against purchase orders',
  'Show me new bookings',
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
  const [lang, setLang] = useState<Lang>('auto')
  const end = useRef<HTMLDivElement>(null)

  useEffect(() => { history = turns; end.current?.scrollIntoView({ behavior: 'smooth' }) }, [turns])

  async function send(message: string) {
    if (!message.trim() || busy) return
    setText('')
    setBusy(true)
    setTurns((t) => [...t, { role: 'owner', text: message }])
    try {
      const r = await api.chat(message, lang === 'auto' ? undefined : lang)
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
      <PageHead title="Copilot" sub="Ask in plain language — English, 中文 or Bahasa Malaysia. Tidewise reads your books, proposes actions, and does only what your permission policy allows — everything else waits for you or is refused."
        action={
          <div className="flex items-center gap-1 rounded-lg border border-edge p-0.5 text-[12.5px]" role="group" aria-label="Reply language">
            {LANGS.map(([l, label]) => (
              <button key={l} onClick={() => setLang(l)} aria-pressed={lang === l}
                className={cx('rounded-md px-2.5 py-1', lang === l ? 'bg-brand text-card' : 'text-ink2 hover:bg-sub')}>{label}</button>
            ))}
          </div>} />

      <div className="flex-1 space-y-5">
        {turns.length === 0 && (
          <div className="panel p-6">
            <div className="eyebrow mb-3">Try</div>
            <div className="flex flex-wrap gap-2">
              {(lang === 'zh' ? SUGGESTIONS_ZH : lang === 'ms' ? SUGGESTIONS_MS : SUGGESTIONS).map((s) => (
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
