import { ShieldAlert, Swords } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api, rm, type Json } from '../lib/api'
import { bump } from '../lib/hooks'
import { Badge, Outcome, Panel, cx } from './ui'

type Form = { sender: string; display_name: string; subject: string; body: string }
const EMPTY: Form = { sender: '', display_name: '', subject: '', body: '' }

/** "Try to fool Tidewise": anyone can write an attack email and watch every defence react. */
export function AttackLab() {
  const [presets, setPresets] = useState<Json[]>([])
  const [form, setForm] = useState<Form>(EMPTY)
  const [picked, setPicked] = useState<string | null>(null)
  const [res, setRes] = useState<Json | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => { api.attackPresets().then(setPresets).catch(() => setPresets([])) }, [])

  function pick(p: Json) {
    setPicked(p.id)
    setForm({ sender: p.sender, display_name: p.display_name, subject: p.subject, body: p.body })
    setRes(null)
  }

  async function run() {
    setBusy(true); setErr(null)
    try { setRes(await api.attack(form)); bump() } catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }

  const set = (k: keyof Form) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => { setForm({ ...form, [k]: e.target.value }); setPicked(null) }
  const ok = /^[^@\s]+@[^@\s]+$/.test(form.sender) && form.body.trim().length > 0

  return (
    <Panel title={<span className="inline-flex items-center gap-2"><Swords size={18} /> Try to fool Tidewise</span>}
      action={<span className="hidden text-[12px] text-ink3 sm:inline">runs on a throw-away copy of the books</span>}>
      <p className="-mt-2 mb-4 max-w-3xl text-[13.5px] text-ink2">
        Write any email you like: a fake supplier with new bank details, an instruction hidden for the AI, a polite
        paraphrase, Chinese or Malay. It goes through the real defences, then Tidewise tries to do what the email asks.
      </p>
      <div className="grid gap-5 lg:grid-cols-2">
        <div>
          <div className="mb-3 flex flex-wrap gap-1.5">
            {presets.map((p) => (
              <button key={p.id} onClick={() => pick(p)}
                className={cx('rounded-full border px-2.5 py-1 text-[12px]', picked === p.id ? 'border-brand bg-brand text-card' : 'border-edge text-ink2 hover:bg-sub')}>
                {p.label}
              </button>
            ))}
          </div>
          <div className="grid gap-2 sm:grid-cols-2">
            <input className="input" placeholder="From: someone@domain.example" value={form.sender} onChange={set('sender')} aria-label="Sender email" />
            <input className="input" placeholder="Display name" value={form.display_name} onChange={set('display_name')} aria-label="Display name" />
          </div>
          <input className="input mt-2 w-full" placeholder="Subject" value={form.subject} onChange={set('subject')} aria-label="Subject" />
          <textarea className="input mt-2 h-36 w-full resize-y font-mono text-[12.5px]" placeholder="Write your attack email here…"
            value={form.body} onChange={set('body')} aria-label="Email body" />
          <div className="mt-3 flex items-center gap-3">
            <button className="btn btn-primary" disabled={!ok || busy} onClick={run}>
              <ShieldAlert size={14} /> {busy ? 'Checking…' : 'Send it to Tidewise'}
            </button>
            {err && <span className="text-[12.5px] text-bad">{err}</span>}
          </div>
        </div>

        <div className="rounded-xl border border-edge bg-sub/30 p-4">
          {!res ? (
            <div className="flex h-full min-h-48 items-center justify-center text-center text-[13px] text-ink3">
              Pick an attack above or write your own.<br />Every layer's verdict appears here.
            </div>
          ) : (
            <>
              <div className="flex flex-wrap items-center gap-3">
                <Badge tone={res.verdict}>{res.verdict}</Badge>
                <span className="text-[12px] text-ink3">checked in {res.ms} ms</span>
                <span className="ml-auto text-right">
                  <span className="eyebrow block">Money moved</span>
                  <span className="num text-[22px] font-semibold text-ok">{rm(res.money_moved_myr)}</span>
                </span>
              </div>

              <ol className="mt-3 space-y-2">
                {res.layers.map((l: Json, i: number) => (
                  <li key={l.name} className="flex gap-2.5 text-[13px]">
                    <span className={cx('mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[11px] font-bold',
                      l.caught ? 'bg-bad/15 text-bad' : 'bg-sub text-ink3')}>{i + 1}</span>
                    <span>
                      <span className="font-semibold">{l.name}</span>{' '}
                      <span className={l.caught ? 'text-bad' : 'text-ink3'}>{l.caught ? '· caught it' : '· let it through'}</span>
                      <span className="block text-[12.5px] text-ink2">{l.detail}</span>
                    </span>
                  </li>
                ))}
              </ol>

              <div className="mt-4 border-t border-edge pt-3 text-[13px]">
                {res.attempt ? (
                  <>
                    <div className="eyebrow mb-1">Tidewise then tries what the email asks</div>
                    <div className="flex flex-wrap items-center gap-2"><span>{res.attempt}</span> <Outcome outcome={res.decision.outcome} /></div>
                    {res.why_blocked.length > 0 && <div className="mt-1 text-[12.5px] text-ink3">{res.why_blocked.join(' · ')}</div>}
                    <div className="mt-3 rounded-lg border border-brand/30 bg-brand/5 p-3">
                      <div className="eyebrow mb-1">Even if every filter had missed it</div>
                      <div className="flex flex-wrap items-center gap-2"><span>Same request, email treated as trusted</span> <Outcome outcome={res.if_filters_missed.outcome} /></div>
                      <div className="mt-1 text-[12.5px] text-ink2">{res.why_if_missed.join(' · ')}</div>
                    </div>
                  </>
                ) : (
                  <div className="text-ink2">The email doesn't ask for any payment, so there's nothing for Tidewise to act on.</div>
                )}
              </div>
              {res.hidden_text_removed && <div className="mt-2 text-[12px] text-ink3">Hidden markup was stripped before anyone (or any model) saw it.</div>}
            </>
          )}
        </div>
      </div>
    </Panel>
  )
}
