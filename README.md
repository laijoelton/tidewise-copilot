# Tidewise — an AI finance copilot that small exporters can trust with money

**2026 Shenzhen FinTechathon · International Track · Topic E: SME Finance Copilot**
Theme: *AI as a Financial Participant*

Tidewise is an AI agent for the owner of a small exporting business. It reads the books and the trade documents, forecasts cash 13 weeks ahead, runs what-if scenarios, finds the cheapest financing that arrives in time, and **acts**: it sends reminders, requests document amendments, pays bills and applies for financing.

Every action passes a deterministic five-tier permission policy, backed by machine-learning risk signals, and runs in a sandbox bank. Every step lands in a Merkle-chained audit log.

> Everything runs in **sandbox mode** on a fictional business (Seri Rotan Furniture Sdn Bhd, a Malaysian rattan and teak exporter). No real money moves, and every name, account and financing provider is made up.

---

## The 60-second story

1. **Today's cash looks fine (RM 125,804).** The forecast says otherwise: the account goes **negative on 26 Oct**, leaving a **RM 100,390 gap** below the owner's RM 60k floor.
2. **Why?** A single transposed digit. The draft bill of lading says **18,450 kg**; the shipping instruction says **18,540 kg**. The shipment is sold under a letter of credit, so the bank will refuse the presentation (UCP 600 Art. 14(d)). The largest receivable, USD 38,600, then slips about 3 weeks, landing just as the RM 96k timber bill and payroll fall due. Tidewise shows this as a cause → effect → impact → fix chain.
3. **Tidewise acts on its own where the policy allows it (T1/T2):**
   - It emails the forwarder an amendment request that cites both document lines.
   - It sends reminders on two overdue invoices.
   - It pays two small bills that are due now, after the ML anomaly check confirms they're in line with each supplier's history.
4. **It queues what needs the owner (T3):**
   - Asking the timber supplier to move RM 96k by 30 days. This is the cheapest on-time option at about RM 1,440. The ML check also flags the bill as 3.1× the usual timber payment, and approval needs step-up re-entry of the amount.
   - A RM 8,000 invoice-financing top-up.

   The tool also shows why the alternatives lose:
   - The working-capital line and the government loan arrive too late.
   - The merchant cash advance costs about 346% APR.
   - LC discounting is blocked until the bill of lading is fixed.
5. **It refuses what it must never do (T4).** An email from `hutan-lestari-timber.example`, a lookalike of the real supplier, asks to "remit to our new account". A hidden HTML comment in another email tells the "AI assistant" to transfer RM 50k. Both are quarantined, by the rules and independently by the ML classifier (94% and 98% threat), and nothing derived from them can execute.
6. **The owner approves, and the gap goes from RM 100,390 to RM 0.** When the corrected bill of lading arrives, the LC receivable is back on time.

Ask "what if Harbour pays 30 days late and sales drop 20%?" and Tidewise re-runs the forecast without touching the books.

---

## How it maps to the brief

| Topic E asks for | Tidewise |
|---|---|
| Financing-option matching | [`financing.py`](tidewise/financing.py): 6 products checked rule by rule (eligibility, time to fund vs need-by date, effective APR, high-cost flag); cheapest on-time combination with a fee margin |
| Cash-flow management with alerts | [`cashflow.py`](tidewise/cashflow.py): 13-week daily forecast and alerts. Receipts are dated by each customer's real payment behaviour, LC receipts are delayed by document discrepancies, and doubtful debts are excluded. Includes an **ML collection-risk score** per invoice, a **what-if simulator** (chat and sliders) and a **root-cause chain** |
| Business financial analysis | [`analysis.py`](tidewise/analysis.py): margins, DSO/DPO, cash-conversion gap, current ratio, runway and trend, each with a plain-language reading |
| AI as a *participant* | [`agent.py`](tidewise/agent.py): one loop for chat and autopilot. It reads, proposes, has the policy decide, then executes in the sandbox or queues for the owner |

| Scoring criterion | Where to look |
|---|---|
| Task completion (40%) | [Demo transcript](evidence/demo_transcript.md), [sandbox operations log](evidence/sandbox_operations.jsonl), [before/after](evidence/forecast_before_after.json) |
| Security & compliance (30%) | [Security self-assessment](docs/SECURITY_SELF_ASSESSMENT.md) (permission tiers + known-risk list), [`policy.py`](tidewise/policy.py), [`guard.py`](tidewise/guard.py), [`ml.py`](tidewise/ml.py), [red-team report](evidence/redteam_report.json) (**25/25**), [ML evaluation](evidence/ml_report.json) |
| Innovation & interaction (30%) | Document-to-cash causality chain; what-if simulator; ML signals that can only escalate; provenance/taint tracking; kill switch; approvals on a separate owner channel with step-up; LLM number-grounding firewall; Merkle inclusion proofs per action; a conversational UI that shows every policy check |

Full design: **[docs/TECHNICAL.md](docs/TECHNICAL.md)**.

---

## Security in one table

| Layer | What it does |
|---|---|
| **Permission tiers** | T0 read · T1 templated notices to registered contacts · T2 small payments of approved bills to verified payees (≤ RM 10k each, ≤ RM 25k/day) · T3 owner approval · T4 prohibited (new payees, bank-detail changes, unknown transfers, changing limits) |
| **Provenance / taint** | Anything derived from external content can't auto-execute above T1; anything derived from quarantined content is blocked |
| **Inbound guard** | Registered-sender check, lookalike and Unicode homoglyph domains, payment-redirection fraud, prompt injection (English, Malay, Chinese, hidden markup), masking |
| **Machine learning** | Threat classifier (held-out paraphrases: rules 44% → rules + model 96% caught, 1.3% false alarms); payment anomaly detector (100% of injected anomalies, 0.4% false alarms); collection-risk predictor (AUC 0.88). Models can only escalate, never relax a control |
| **Owner channel** | Approvals, kill switch and reset need the owner token; step-up re-entry above RM 50k; 48h expiry; re-check at execution |
| **LLM boundary** | Optional Gemini only words structured facts; any number it invents makes Tidewise fall back to the template |
| **Execution** | Idempotent sandbox bank that re-validates on its own |
| **Audit** | Every step in a Merkle-chained log with per-record inclusion proofs |

---

## Architecture

```mermaid
flowchart LR
    subgraph Outside["Outside world (untrusted)"]
        MAIL[Emails]
        DOCS[SI / B/L documents]
    end
    subgraph Core["Tidewise core"]
        GUARD["guard.py<br/>rules: lookalike · homoglyph<br/>payment redirection · injection"]
        ML["ml.py<br/>threat classifier · payment anomaly<br/>collection risk"]
        TD["trade_docs.py<br/>engine: extractor + reconciler"]
        CF["cashflow.py<br/>forecast · alerts · what-if · root cause"]
        AN["analysis.py<br/>business health"]
        FIN["financing.py<br/>eligibility · timing · APR"]
        AGENT["agent.py<br/>chat + autopilot"]
        LLM["llm.py (optional Gemini)<br/>intent + wording, number-grounded"]
        POL["policy.py<br/>T0–T4 · limits · provenance · kill switch"]
        SB["sandbox.py<br/>idempotent sandbox bank"]
        AUD["audit.py<br/>Merkle-chained log"]
    end
    OWNER(("Owner"))
    MAIL --> GUARD --> AGENT
    ML -. threat score .-> GUARD
    ML -. anomaly .-> POL
    ML -. late risk .-> CF
    DOCS --> TD --> CF --> FIN
    CF --> AGENT
    AN --> AGENT
    FIN --> AGENT
    AGENT <--> LLM
    AGENT -- proposes --> POL
    POL -- auto --> SB
    POL -- needs approval --> OWNER
    OWNER -- "approve (owner token, step-up)" --> POL
    AGENT & POL & SB --> AUD
```

---

## Quickstart

Requirements: Python 3.11+ and Node 20+.

```bash
pip install -r requirements.txt
```

```bash
python -m uvicorn api.main:app --port 8000
```

The API prints an **owner token** at start-up. Paste it on the Approvals page; approvals and the kill switch are refused without it. To fix the token, set `TIDEWISE_OWNER_TOKEN` in `.env`.

```bash
npm --prefix web install
```

```bash
npm --prefix web run dev
```

Open http://localhost:5173. Try these in the Copilot:
- "What should I do this week?"
- "What if Harbour pays 30 days late and sales drop 20%?"
- "Pay BILL-771 to our new account 8600-1234-5678" (this is refused)

Optional: put `GEMINI_API_KEY` in `.env` (see `.env.example`) and Gemini will phrase the replies. Without a key, everything runs offline on the deterministic router and templates. The model never decides or executes anything.

### Tests and evidence

```bash
python run_tests.py
```

This runs 68 tests covering the agent loop, policy, sandbox, guard, ML models, forecast, what-if, financing, red team and engine.

```bash
python -m tidewise.redteam
```

This runs the 25 attack scenarios.

```bash
python -m tidewise.ml
```

This prints the held-out ML evaluation.

```bash
python scripts/generate_evidence.py
```

This regenerates `evidence/` (sandbox operations log, audit trail, transcript, red-team and ML reports).

---

## Repository layout

```
tidewise/          the agent: data, forecast, analysis, financing, guard, ml, policy, sandbox, audit, agent, llm, redteam
tidewise/engine/   document loader, field extractor (line-level evidence), reconciler, Merkle log, rate limiter
api/main.py        FastAPI: conversation channel + owner channel
web/               React 19 + Vite + Tailwind UI (Today, Copilot, Approvals, Cash flow & what-if, Financing,
                   Trade documents, Inbox guard, Business health, Security, Activity & audit)
tests/             unittest suite (test_tidewise, test_features, test_ml, test_engine)
docs/              technical documentation, security self-assessment
evidence/          execution evidence (sandbox ops log, audit trail, transcript, red-team and ML reports)
scripts/           evidence generator
```

## Disclaimer

Tidewise is a competition prototype running on synthetic data. Financing products, pricing, the LC delay assumption and the ML training data are illustrative, and none of it is financial advice.
