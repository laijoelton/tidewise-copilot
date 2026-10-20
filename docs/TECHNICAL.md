# Tidewise — Technical Documentation

Covers: system architecture, core algorithms and security design. The [security self-assessment](SECURITY_SELF_ASSESSMENT.md) covers permission tiers and the known-risk list in more depth.

## 1. Design principles

1. **The agent proposes; rules decide.** A language model is optional and never decides whether an action is safe or executes one. Every action that moves money or sends a message is decided by `policy.Policy`, which is deterministic and explainable: the same state always gives the same decision, with printable reasons.
2. **Outside content is data, never instructions.** Emails and documents are screened (`guard.py`), and every action carries the provenance of its parameters. External provenance can never auto-execute above T1. Anything derived from quarantined content is blocked.
3. **Money only moves in one place.** `sandbox.SandboxBank` is the only code that changes balances or sends messages. It re-checks its own preconditions and is idempotent.
4. **Everything is provable afterwards.** Every proposal, decision, approval and execution is a leaf in a Merkle tree. Each turn's root is chained to the previous root.
5. **Explain in the owner's terms.** The tool reports ringgit, dates and "why" rather than scores.

## 2. Architecture

| Layer | Module | Responsibility |
|---|---|---|
| Domain | `tidewise/models.py`, `tidewise/data.py` | Company, accounts, ledger, customer orders, delivery notes, invoices (AR), bills (AP), payees, product catalogue, inbox. Demo data is deterministic (seeded) and fictional. |
| Engine | `tidewise/engine/` | `merkle.py` (Merkle-chained log with domain-separated hashes and inclusion proofs); `ratelimit.py`. |
| Perception | `tidewise/matching.py` | Three-way match of PO, delivery note and invoice; the hold a mismatch causes; the corrected invoice. |
| | `tidewise/bookings.py` | Customer bookings: screening, identity, ML credit risk, deposit, capacity. |
| | `tidewise/guard.py` | Inbound screening: verified senders, lookalike and homoglyph domains, payment redirection (BEC), prompt injection (English, Malay, Chinese, hidden markup), masking. |
| Reasoning | `tidewise/cashflow.py` | 13-week daily forecast, alerts, what-if simulator, root-cause chain. |
| | `tidewise/analysis.py` | Margins, DSO/DPO, liquidity, runway, trend, insights. |
| | `tidewise/financing.py` | Product catalogue, rule-by-rule eligibility, timing, cost/APR, cheapest plan. |
| Agency | `tidewise/agent.py` | Routing, read tools, what-if parsing, autopilot playbook, propose → decide → execute/queue/block, approvals, replies. |
| | `tidewise/llm.py` | Optional Gemini: intent fallback and reply wording, behind a number-grounding check. |
| Control | `tidewise/policy.py` | T0–T4 tiers, per-action checks, owner limits, provenance/taint, autonomy budget, kill switch, step-up. |
| Execution | `tidewise/sandbox.py` | Sandbox bank: pay, FX, messages, financing, supplier terms, corrected invoices, booking confirmations, simulated customer actions. |
| Assurance | `tidewise/audit.py` | Audit trail over the Merkle log: inclusion proofs, verification, export. |
| | `tidewise/redteam.py` | 28 attack scenarios against the real code. |
| Learning | `tidewise/ml.py` | Inbound threat classifier, payment anomaly detector, collection-risk predictor — signals that can only escalate. |
| Interface | `api/main.py`, `web/` | FastAPI with two channels (conversation, owner); React 19 UI with 10 pages. |

### The ten-gate security pipeline (`tidewise/pipeline.py`)

| Gate | Lane | What it does | What it stops | Code |
|---|---|---|---|---|
| **G1 Front door** | inbound | Rate limit per caller; strict schemas on every request | Floods, oversized or malformed input, forged prices on bookings | `api/main.py` |
| **G2 Sender identity** | inbound | Sender vs registered counterparties; look-alike and homoglyph domains | Fake suppliers and customers | `guard.py` |
| **G3 Content rules** | inbound | Prompt injection (EN/MS/ZH), hidden markup, bank-detail changes; masking | “Ignore your instructions…”, “pay our new account” | `guard.py` |
| **G4 ML threat score** | inbound | Text classifier, tested on unseen phrasings | Paraphrased attacks the rules miss | `ml.py` |
| **G5 Provenance** | action | Who asked: owner, books, outside content, quarantined content | Anything built from quarantined content; outside requests auto-running | `policy.py` |
| **G6 Business rules** | action | Real unpaid bill, verified payee, registered recipient, PO match, capacity | Double payments, changed bank details, exfiltration, inflated invoices | `policy.py` |
| **G7 Limits** | action | Per-payment, daily, FX and booking caps; autonomy budget; kill switch | Split payments, runaway loops | `policy.py` |
| **G8 ML risk check** | action | Payment anomaly vs payee history; customer credit risk | Padded bills under the limit; risky customers | `ml.py` |
| **G9 Owner channel** | action | Owner token, step-up above RM 50k, 48 h expiry, re-check at execution | “Approve all” in chat or email; blind or stale approvals | `agent.py`, `api/main.py` |
| **G10 Execute & seal** | action | Idempotent sandbox bank re-validates; Merkle-chained audit seal | Replays, overdrafts, silent edits to history | `sandbox.py`, `audit.py` |

Every proposed action gets a gate-by-gate trace (`pipeline.trace`): **pass**, **escalate** (to the owner), **waiting** (for the owner), **stop**, or **skip** (not applicable). The trace is shown on every Copilot action and approval card, its summary is sealed into the audit log, and the Security page shows live counts per gate. Each red-team attack is mapped to the gate that catches it first (`redteam.CAUGHT_AT`).

### Request flow (one chat turn)

```
owner text ─► mask ─► audit("owner_message")
          ─► what-if parser ──► simulation only (read-only), or
          ─► owner-command parser (pay BILL-x, remind, convert, apply, attempts to change limits/bank details)
          ─► intent router (regex; Gemini fallback limited to a fixed intent list)
          ─► read tools (T0) ─► audit("read")
          ─► propose(Action{type, params, provenance, source_ref, rationale})
                ─► audit("proposed") ─► Policy.evaluate ─► audit("decision")
                ─► auto: SandboxBank.<op>(idempotency key) ─► audit("executed")
                ─► needs_approval: approvals queue (TTL 48h) ─► audit("queued_for_approval")
                ─► blocked: alert ─► audit("blocked")
          ─► reply (sectioned template; optionally reworded by Gemini, then number-checked)
          ─► audit.seal()  → Merkle root chained to previous root
```

Approvals arrive on a separate API route that requires the owner token header. The policy re-evaluates the action at execution time, because the state may have changed since the proposal, and amounts above RM 50,000 require step-up re-entry. The kill switch is on the same owner channel.

## 3. Core algorithms

### 3.1 Three-way match → cash consequence (`tidewise/matching.py`)

Business customers pay an invoice only after their accounts team has matched it to their purchase order (agreed quantities and prices) and the signed delivery note (what arrived). Tidewise runs the same check on its own invoices first.

- **Per line, by SKU:** an item invoiced but not ordered; invoiced quantity ≠ ordered; invoiced quantity > delivered; unit price outside a 0.5% tolerance of the PO price. Currency is checked too.
- **Statuses:** `MATCHED`, `MISMATCH`, `AWAITING_APPROVAL` (corrected invoice sent), `NO_ORDER`.
- **Consequence:** an unresolved mismatch delays the receipt by `HOLD_DAYS_UNRESOLVED = 21` days (dispute, correction, re-approval, next payment run). After a corrected invoice is sent, the delay is `HOLD_DAYS_CORRECTION_SENT = 14` days, until the customer approves it. Both are assumptions, shown as the "basis" on the forecast line.
- **Correction:** `draft_correction` writes the corrected invoice at the PO's quantities and prices. Sending it is a T1 action, and the policy requires the new total to equal the PO total exactly.

### 3.2 Cash-flow forecast (`cashflow.forecast`)

1. **Customer payment behaviour.** Over six months of paid invoices, `delay_c = mean(paid_on − due)` per customer.
2. **Expected receipt date** = `due + round(delay_c)`, plus the hold if the invoice fails the three-way match (21 days, or 14 once corrected). If that date is already in the past but the invoice is still collectible, the receipt is assumed within 3 days.
3. **Doubtful.** An invoice more than 30 days past its customer's own norm is excluded from cash and listed separately, never silently counted.
4. **Outflows.** Open bills (on their due date), recurring items (rent, loan, utilities, payroll on their day of month) and lender repayments.
5. **Beyond the books' visibility** (day ≥ 35), weekly run-rates are added: trailing-90-day sales × 0.85 haircut, and materials plus logistics. These rows are labelled `projected`.
6. A **daily running balance** over 91 days gives exact dates for the first buffer breach, the first negative day and the trough. Weekly buckets are derived from it.
7. **Funding gap** = `max(0, buffer − trough)`.
8. **Alerts** are generated by rules: `CASH_SHORTFALL`, `BUFFER_BREACH`, `LC_DISCREPANCY`, `OVERDUE_RECEIVABLE`, `DOUBTFUL_RECEIVABLE`, `CONCENTRATION` (>35% of AR from one customer) and `FX_EXPOSURE` (>50% of AR in foreign currency).

### 3.3 What-if simulator (`cashflow.whatif`)

The simulator builds the same event list as the forecast, then transforms a copy:

| Scenario | Transformation |
|---|---|
| `customer_delay` (one customer or everyone, ±days) | Shifts those receipts |
| `lose_customer` | Removes that customer's receipts |
| `sales_change_pct` | Scales projected sales |
| `extra_monthly_cost` | Adds a monthly outflow |
| `usd_change_pct` | Scales USD receipts and USD balances |

It returns the baseline and scenario trough, gap, breach date and closing balance, plus both weekly series for the chart overlay.

- **Read-only by construction:** it never writes to `Company`. Red-team case RT-23 asserts this.
- **Input:** the chat parser (`agent.parse_whatif`) turns plain English such as "what if Harbour pays 30 days late and sales drop 20%" into a scenario. The UI drives the same function with presets and sliders.

### 3.4 Root-cause chain (`cashflow.root_cause_chain`)

When there is a gap, the chain walks from cause to fix:
1. **Cause:** the document discrepancy.
2. **Effects:** the customer's accounts team holding the invoice, and any doubtful receivables left out.
3. **Impact:** the breach date and trough.
4. **Fix:** the cheapest on-time financing plan.

The owner sees *why*, not just *what*.

### 3.5 Customer bookings (`tidewise/bookings.py`)

Customers book orders through a portal (`POST /api/bookings`). Each booking is assessed before anyone commits factory capacity:

| Step | What happens |
|---|---|
| Screening | Booking notes are external content: same guard as email (rules + ML threat classifier). Quarantined → declined. |
| Identity | Known customer *and* booked from the registered contact? A known name from a new address goes to the owner; a look-alike domain is quarantined. |
| Credit | ML collection-risk model on the customer's own history; new customers start at a cautious 50%. |
| Deposit | 30% (risk < 20%), 40% (< 50%), 50% (higher, or new customer). |
| Capacity | Each product's weekly output against everything already in the order book → earliest ready date vs requested delivery. |
| Prices | Always from our catalogue, never from the form. |

Confirming a booking is a T2 action only for a known customer with low risk, ≤ RM 50k, enough capacity and at least the recommended deposit. Anything else is T3, and a quarantined booking can't be confirmed at all (T4). Confirmed orders add their deposit, and any balance inside the visible horizon, to the forecast.

### 3.6 Business analysis (`analysis.analyse`)

Computed over a 90-day window from the ledger (FX sweeps excluded):

| Metric | Formula |
|---|---|
| Gross margin | (revenue − materials) / revenue |
| Operating margin | (revenue − materials − opex) / revenue |
| DSO | open AR ÷ daily revenue |
| DPO | open AP ÷ daily materials |
| Cash-conversion gap | DSO − DPO |
| Current ratio | (cash + AR) / (AP + 12 months of loan instalments) |
| Runway | cash ÷ monthly outflow |

Insights are rule-based readings of these numbers.

### 3.7 Financing matching (`financing.match`)

For each catalogue product:
1. **Eligibility checks** against the business's own data: years trading, revenue, credit band and collateral, plus product-specific capacity:
   - **Invoice financing:** open-account invoices, not overdue, due within 90 days, buyer country eligible, ≥ RM 20k, not already pledged. 80% advance.
   - **Invoice financing** also leaves out any invoice that fails the three-way match, and says why.
   - **Purchase-order financing:** 60% of confirmed orders whose deposit is paid, from known customers; repaid from the order's balance.
   - **Supplier extension:** the largest open bill due within 3 weeks from a supplier paid at least 12 times in 6 months (`supplier_extension_candidate`).
   - **Credit lines and loans:** limit = % of revenue, capped.
2. **Timing:** `funds_by = as_of + max(days_to_fund)`; `on_time = funds_by ≤ need_by`.
3. **Cost:** `draw × (monthly_rate × days/30 + annual_rate × days/365) + draw × upfront_fee`, or `draw × (factor − 1)` for a merchant cash advance.
   - Effective APR = `cost / draw × 365 / days`.
4. **Ranking:** eligible → on time → not high-cost (APR ≤ 36%) → covers the need → lowest APR.
5. **Plan:** a greedy combination of eligible, on-time, not-high-cost products in APR order until `ceil(need × 1.03 / 1000) × 1000` is covered. The 3% margin stops fees from reopening the gap.

When a product is executed in the sandbox:
- **Invoice financing** pledges only as many invoices as the advance needs, latest-due first, and books the lender repayment as a dated outflow, so the forecast stays honest.
- **Purchase-order financing** books its repayment for the orders' balance date.

### 3.8 Inbound guard (`guard.scan`)

- **Sender verification.** The domain must equal a registered counterparty domain (payees, customer AR contacts).
- **Homoglyph check.** The domain is normalised (NFKC plus a confusables map for Cyrillic, Greek and digit look-alikes, `rn→m`). If its skeleton equals a registered domain's but the domain itself differs, that is critical. Punycode (`xn--`) and non-ASCII domains are also critical.
- **Lookalike check.** Edit distance ≤ 3 on the first label, containment, or a display name claiming a registered party. Display-name impersonation of well-known carriers is also flagged.
- **Hidden content.** HTML comments, `display:none` / zero-size spans and zero-width characters are counted, removed from display, and flagged.
- **Prompt injection.** Pattern families in English, Malay/Indonesian and Chinese flag:
  - text addressed to an AI;
  - "ignore previous instructions";
  - claims of authority;
  - "don't tell the user";
  - pushes for an immediate money movement;
  - references to model internals.
- **Payment redirection.** New or changed bank details, a suspended account, an account number paired with a pay verb, "akaun baru", "新账户".
- Any critical finding quarantines the message, and its id is added to `policy.quarantined_sources`.
- **Masking.** Account numbers and IBANs are masked before display, logging or any model call.

### 3.9 Policy evaluation (`policy.Policy.evaluate`)

```
tier = BASE_TIER[action.type]                       (unknown type → T4)
tier = max(tier, per-action checks)                 (may escalate; hard failures → blocked)
if source_ref in quarantined_sources: tier = T4
elif provenance == "external" and tier ≥ T2: tier = max(tier, T3)
if kill switch on and tier ≤ T2 and not a read: tier = T3
if tier ≤ T2 and autonomy budget spent: tier = max(tier, T3)
outcome = blocked if T4 or any hard check failed
        = needs_approval if T3 (step_up if amount > RM 50k)
        = auto otherwise
```

Per-action checks:
- **`pay_bill`:** bill exists, unpaid, approved when booked; payee verified; account override equals the verified account (else T4); per-payment limit RM 10k; daily auto limit RM 25k (tracked per calendar day); not paying more than 5 days early; funds available.
- **`send_reminder`:** invoice open; recipient equals the registered AR contact (else T4); 7-day cooldown.
- **`send_corrected_invoice`:** a mismatch exists, the new total equals the PO total, recipient is the registered AR contact (else T4).
- **`request_deposit`:** deposit outstanding; registered contact.
- **`confirm_booking`:** screening clean (else T4); known customer from the contact on file, ML risk ≤ 30%, ≤ RM 50k, capacity, deposit ≥ recommended — otherwise T3.
- **`decline_booking`:** T1 if the booking was quarantined, else T3.
- **`fx_convert`:** funds available; ≤ USD 5k auto.
- **`apply_financing`, `request_supplier_extension`:** always T3.
- **`add_payee`, `change_payee_bank`, `transfer_external`, `change_limits`:** always T4.

### 3.10 Sandbox bank

- **Idempotency.** Every operation takes a key, `sha256(type, params, as_of)`. A replay returns the original receipt with `status: duplicate_ignored`.
- **Re-validation.** The bank re-checks open/unpaid, payee verified and non-negative balance on its own, independent of the policy.
- **Receipts.** Each receipt is `SBX-` + 10 hex digits, and each one is appended to the operations log (JSONL).

### 3.11 Audit trail

- **Hashing.** Leaves are SHA-256 hashes of `leaf:` + canonical JSON. Nodes are hashes of `node:` + left + right, and an odd node is paired with itself.
- **Chaining.** Each sealed batch stores `prev_root`, so the batches form a chain.
- **Verification.** `verify()` checks the chain and every record's inclusion proof. Any record's proof can be checked on its own, from the UI or `/api/audit/receipt/{id}`.

### 3.12 Machine-learning risk models (`tidewise/ml.py`)

The three models are trained locally on synthetic data with a fixed seed. Each **adds a signal** on top of the deterministic controls: it can quarantine or escalate, but it can never make anything more permissive. `python -m tidewise.ml` prints the held-out evaluation, which is also in `evidence/ml_report.json` and on the Security page.

**1. Inbound threat classifier**
- **Model:** TF-IDF character 2–5-grams plus multinomial logistic regression. Classes: `benign`, `payment_fraud`, `prompt_injection`, in English, Malay and Chinese. Character n-grams handle mixed scripts without tokenisers.
- **Use in the guard:**
  - threat probability ≥ 0.60 adds a critical `ml_threat` finding, which quarantines the message;
  - ≥ 0.35 adds a warning.
- **Honest test:** evaluated on **paraphrase templates never used in training**.

  | Detector | Caught | False alarms |
  |---|---|---|
  | Rules only | 44% | 0% |
  | Rules + model (live) | 96% | 1.3% |

**2. Payment anomaly detector**
- **Model:** an Isolation Forest per business, trained on that business's own outgoing payments. Features: the robust z-score of log-amount against that payee's history, log-amount, and days since the last payment.
- **Use in the policy:** it runs as the `payment pattern (ML)` check in `pay_bill`. An anomalous amount, or fewer than 3 past payments to that payee, escalates T2 to T3, with the reason shown ("3.1× the typical RM 31,003 paid to …").
- **Test:** on 240 normal payments and 240 injected anomalies (3–10× or 0.1–0.3× usual size), it catches 100% with 0.4% false alarms.

**3. Collection-risk predictor**
- **Model:** gradient boosting on a simulated SME receivables portfolio, predicting P(paid 30+ days after due). Features: the customer's own mean and standard deviation of delay, number of prior invoices, amount vs usual, days overdue, reminders sent, whether a deposit was paid.
- **Use:** the probability is attached to every forecast receipt (`late_risk`) and shown in overdue alerts.
- **Test:** on an independent simulated portfolio, ROC AUC 0.88 and Brier score 0.135.

**Limits.** The models are trained on synthetic data, so their numbers show the method works, not production accuracy. In deployment they would be retrained on the business's own history and monitored for drift. Because a model can only escalate, a model failure degrades to "more approvals", never "more autonomy".

## 4. LLM boundary

- **Inputs:** the owner's message, plus structured and masked facts (forecast numbers, alert titles, plan, action outcomes). It never receives raw email or document text.
- **Outputs:**
  - an intent list, filtered to a fixed allow-list;
  - reply wording.
- **Hallucination firewall (`llm.grounded`).** Every number in the model's wording above 31 must match a number in the facts it was given, within 1% or as a "k" rounding. One unsupported figure and the deterministic template is shown instead. Rejections are counted.
- **No authority.** Model output is never parsed into action parameters, and approvals can't be triggered from chat.
- **Fallback.** Without a key, Tidewise is fully functional offline.

## 5. API

| Channel | Endpoints |
|---|---|
| Read | `GET /api/overview` (incl. root-cause chain), `/forecast`, `/analysis`, `/matching`, `/orders`, `/financing`, `/inbox`, `/ledger`, `/policy`, `/audit`, `/audit/receipt/{id}`, `/sandbox`, `/approvals` |
| Conversation | `POST /api/chat` (max 2,000 chars), `POST /api/whatif` (bounded inputs) |
| Owner (needs `X-Owner-Token`) | `POST /api/approvals/{id}/approve` (`confirm_amount` for step-up), `/reject`, `POST /api/autonomy` (kill switch), `POST /api/reset` |
| Customer channel | `POST /api/bookings` (validated; prices from our catalogue; notes screened) |
| Demo controls | `POST /api/simulate/customer-approves`, `POST /api/simulate/deposit`, `POST /api/redteam` |

Hardening:
- per-IP rate limit (60 req/s);
- CORS restricted to the local UI origins;
- the token is compared in constant time;
- Pydantic input validation with numeric bounds.

## 6. Testing

`python run_tests.py` runs **78 tests**:
- **`test_tidewise.py`:**
  - document checks, forecast, analysis and financing;
  - guard, policy and the agent loop end to end;
  - inclusion proofs and the full red-team suite.
- **`test_features.py`:**
  - what-if and its read-only guarantee;
  - natural-language scenario parsing and the root-cause chain;
  - the supplier rule and the kill switch;
  - homoglyph, punycode, Malay and Chinese screening;
  - LLM grounding.
- **`test_ml.py`:** held-out quality thresholds for all three models; demo inbox scores; a paraphrase quarantined by the model alone; a padded bill escalated; collection-risk ranking.
- **`test_engine.py`:** extraction with evidence, placeholders, unreadable scans, reconciliation, the loader, the Merkle chain and tampering, and the rate limiter.

`python -m tidewise.redteam` prints the 28 attack outcomes; `python -m tidewise.ml` prints the model evaluation. `python scripts/generate_evidence.py` regenerates `evidence/`.

## 7. Limitations and next steps

- **Real banking rails** would use open-banking consent APIs. The sandbox interface (`pay_bill`, `fx_convert`, `submit_financing`) is the seam.
- **Real lender panel and pricing:** the catalogue is data.
- **Persistent multi-user state:** each workspace is currently in-memory.
- **Bring-your-own data:** CSV and bank-statement import.
- **Email authentication:** SPF/DKIM/DMARC verification from real headers.
- **Accounting integrations:** read POs, delivery notes and invoices straight from the business's accounting or e-invoicing system.
