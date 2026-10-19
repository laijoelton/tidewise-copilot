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
| Domain | `tidewise/models.py`, `tidewise/data.py` | Company, accounts, ledger, invoices (AR), bills (AP), payees, shipments, inbox. Demo data is deterministic (seeded) and fictional. |
| Engine | `tidewise/engine/` | `documents.py` (multi-format loader, document-type detection); `extractor.py` (7 shipping fields with line-level evidence; optional vision for scans); `reconciler.py` (entity/port normalisation, field comparison); `merkle.py` (Merkle-chained log); `ratelimit.py`. |
| Perception | `tidewise/trade_docs.py` | SI vs B/L check; translates a discrepancy into its payment consequence under a letter of credit. |
| | `tidewise/guard.py` | Inbound screening: verified senders, lookalike and homoglyph domains, payment redirection (BEC), prompt injection (English, Malay, Chinese, hidden markup), masking. |
| Reasoning | `tidewise/cashflow.py` | 13-week daily forecast, alerts, what-if simulator, root-cause chain. |
| | `tidewise/analysis.py` | Margins, DSO/DPO, liquidity, runway, trend, insights. |
| | `tidewise/financing.py` | Product catalogue, rule-by-rule eligibility, timing, cost/APR, cheapest plan. |
| Agency | `tidewise/agent.py` | Routing, read tools, what-if parsing, autopilot playbook, propose → decide → execute/queue/block, approvals, replies. |
| | `tidewise/llm.py` | Optional Gemini: intent fallback and reply wording, behind a number-grounding check. |
| Control | `tidewise/policy.py` | T0–T4 tiers, per-action checks, owner limits, provenance/taint, autonomy budget, kill switch, step-up. |
| Execution | `tidewise/sandbox.py` | Sandbox bank: pay, FX, messages, financing, supplier terms, simulated forwarder. |
| Assurance | `tidewise/audit.py` | Audit trail over the Merkle log: inclusion proofs, verification, export. |
| | `tidewise/redteam.py` | 23 attack scenarios against the real code. |
| Interface | `api/main.py`, `web/` | FastAPI with two channels (conversation, owner); React 19 UI with 10 pages. |

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

### 3.1 Trade-document check → cash consequence

- **Extraction.** The SI and B/L texts are parsed by the deterministic extractor. It handles header/colon, dotted-leader and dash/pipe layouts. Each value keeps its 1-indexed source line and snippet. Placeholders ("N/A", "TBA", "____") are flagged, never guessed.
- **Reconciliation.** The reconciler normalises entities and ports, then compares the 7 canonical fields:
  - entities: `SDN. BHD.` = `SDN BHD`, `L.L.C.` = `LLC`;
  - ports: `PORT KELANG` = `PORT KLANG`, and city-plus-country forms;
  - `SAME AS CONSIGNEE`.

  It returns `OK`, `MISMATCH` or `NEEDS_REVIEW`. `NEEDS_REVIEW` covers a wrong document type, a missing document, an unreadable document and a missing value.
- **Consequence.** If the invoice behind the shipment is on **LC terms** and any field conflicts, the presentation will be refused (UCP 600 Art. 14(d)). The receivable is then delayed by `LC_DISCREPANCY_DELAY_DAYS = 21`, which covers the refusal notice, carrier amendment, re-presentation and bank examination (up to 5 banking days each under Art. 14(b)). This number is an assumption and is configurable.

### 3.2 Cash-flow forecast (`cashflow.forecast`)

1. **Customer payment behaviour.** Over six months of paid invoices, `delay_c = mean(paid_on − due)` per customer.
2. **Expected receipt date** = `due + round(delay_c)` (+21 days if LC-blocked). If that date is already in the past but the invoice is still collectible, the receipt is assumed within 3 days.
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
2. **Effects:** the delayed LC receivable, and any doubtful receivables left out.
3. **Impact:** the breach date and trough.
4. **Fix:** the cheapest on-time financing plan.

The owner sees *why*, not just *what*.

### 3.5 Business analysis (`analysis.analyse`)

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

### 3.6 Financing matching (`financing.match`)

For each catalogue product:
1. **Eligibility checks** against the business's own data: years trading, revenue, credit band and collateral, plus product-specific capacity:
   - **Invoice financing:** open-account invoices, not overdue, due within 90 days, buyer country eligible, ≥ RM 20k, not already pledged. 80% advance.
   - **LC discounting:** only complying presentations. A document discrepancy fails this check, and the reason is shown.
   - **Supplier extension:** the largest open bill due within 3 weeks from a supplier paid at least 12 times in 6 months (`supplier_extension_candidate`).
   - **Credit lines and loans:** limit = % of revenue, capped.
2. **Timing:** `funds_by = as_of + max(days_to_fund)`; `on_time = funds_by ≤ need_by`.
3. **Cost:** `draw × (monthly_rate × days/30 + annual_rate × days/365) + draw × upfront_fee`, or `draw × (factor − 1)` for a merchant cash advance.
   - Effective APR = `cost / draw × 365 / days`.
4. **Ranking:** eligible → on time → not high-cost (APR ≤ 36%) → covers the need → lowest APR.
5. **Plan:** a greedy combination of eligible, on-time, not-high-cost products in APR order until `ceil(need × 1.03 / 1000) × 1000` is covered. The 3% margin stops fees from reopening the gap.

When a product is executed in the sandbox:
- **Invoice financing** pledges only as many invoices as the advance needs, latest-due first, and books the lender repayment as a dated outflow, so the forecast stays honest.
- **LC discounting** sells the receivable: it is removed from future inflows.

### 3.7 Inbound guard (`guard.scan`)

- **Sender verification.** The domain must equal a registered counterparty domain (payees, customer AR contacts, forwarder).
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

### 3.8 Policy evaluation (`policy.Policy.evaluate`)

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
- **`send_amendment_request`:** recipient equals the registered forwarder.
- **`fx_convert`:** funds available; ≤ USD 5k auto.
- **`apply_financing`, `request_supplier_extension`:** always T3.
- **`add_payee`, `change_payee_bank`, `transfer_external`, `change_limits`:** always T4.

### 3.9 Sandbox bank

- **Idempotency.** Every operation takes a key, `sha256(type, params, as_of)`. A replay returns the original receipt with `status: duplicate_ignored`.
- **Re-validation.** The bank re-checks open/unpaid, payee verified and non-negative balance on its own, independent of the policy.
- **Receipts.** Each receipt is `SBX-` + 10 hex digits, and each one is appended to the operations log (JSONL).

### 3.10 Audit trail

- **Hashing.** Leaves are SHA-256 hashes of `leaf:` + canonical JSON. Nodes are hashes of `node:` + left + right, and an odd node is paired with itself.
- **Chaining.** Each sealed batch stores `prev_root`, so the batches form a chain.
- **Verification.** `verify()` checks the chain and every record's inclusion proof. Any record's proof can be checked on its own, from the UI or `/api/audit/receipt/{id}`.

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
| Read | `GET /api/overview` (incl. root-cause chain), `/forecast`, `/analysis`, `/documents`, `/financing`, `/inbox`, `/ledger`, `/policy`, `/audit`, `/audit/receipt/{id}`, `/sandbox`, `/approvals` |
| Conversation | `POST /api/chat` (max 2,000 chars), `POST /api/whatif` (bounded inputs) |
| Owner (needs `X-Owner-Token`) | `POST /api/approvals/{id}/approve` (`confirm_amount` for step-up), `/reject`, `POST /api/autonomy` (kill switch), `POST /api/reset` |
| Demo controls | `POST /api/simulate/forwarder-reply`, `POST /api/redteam` |

Hardening:
- per-IP rate limit (60 req/s);
- CORS restricted to the local UI origins;
- the token is compared in constant time;
- Pydantic input validation with numeric bounds.

## 6. Testing

`python run_tests.py` runs **58 tests**:
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
- **`test_engine.py`:** extraction with evidence, placeholders, unreadable scans, reconciliation, the loader, the Merkle chain and tampering, and the rate limiter.

`python -m tidewise.redteam` prints the 23 attack outcomes. `python scripts/generate_evidence.py` regenerates `evidence/`.

## 7. Limitations and next steps

- **Real banking rails** would use open-banking consent APIs. The sandbox interface (`pay_bill`, `fx_convert`, `submit_financing`) is the seam.
- **Real lender panel and pricing:** the catalogue is data.
- **Persistent multi-user state:** each workspace is currently in-memory.
- **Bring-your-own data:** CSV and bank-statement import.
- **Email authentication:** SPF/DKIM/DMARC verification from real headers.
- **Scanned documents:** the vision path exists in the extractor, but the demo uses text documents.
