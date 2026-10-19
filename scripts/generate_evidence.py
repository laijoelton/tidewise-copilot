"""Regenerate the execution evidence in evidence/ (submission item 06).

Runs the scripted demo on a fresh sandbox workspace — the same code paths
the API and UI use — and writes:

  evidence/sandbox_operations.jsonl   every sandbox operation, one JSON per line
  evidence/audit_trail.json           every audit record, sealed batches, verification
  evidence/demo_transcript.md         the conversation, decisions and receipts, readable
  evidence/forecast_before_after.json cash position before and after the agent acted
  evidence/redteam_report.json        17 attack scenarios and their outcomes

Usage (repo root):  python scripts/generate_evidence.py
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tidewise import cashflow, ml, redteam  # noqa: E402
from tidewise.agent import EVIDENCE_DIR, Workspace  # noqa: E402
from tidewise.sandbox import SandboxBank  # noqa: E402

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def snapshot(ws):
    fc = cashflow.forecast(ws.company)
    return {"cash_myr": ws.company.cash_myr(), "trough": fc["trough"], "funding_gap": fc["funding_gap"],
            "first_buffer_breach": fc["first_buffer_breach"], "alerts": [a["title"] for a in fc["alerts"]]}


def main():
    EVIDENCE_DIR.mkdir(exist_ok=True)
    ops = EVIDENCE_DIR / "sandbox_operations.jsonl"
    ops.unlink(missing_ok=True)

    ws = Workspace(use_llm=False)
    ws.sandbox = SandboxBank(ws.company, ops)        # same bank, logging to the evidence file
    md = ["# Tidewise demo transcript (sandbox)", "",
          f"Business: {ws.company.profile.legal_name} (fictional) · books as of {ws.company.as_of}", ""]
    before = snapshot(ws)

    def turn(text):
        r = ws.agent.chat(text)
        md.extend([f"## Owner: “{text}”", "", "**Tidewise:**", "", r["reply"], ""])
        acts = [s for s in r["steps"] if s["kind"] == "action"]
        if acts:
            md.extend(["| Action | Tier | Outcome | Receipt / reason |", "|---|---|---|---|"])
            for s in acts:
                d = s["decision"]
                detail = (s.get("receipt") or {}).get("ref") or "; ".join(d["reasons"])
                md.append(f"| {s['type']} {json.dumps({k: v for k, v in s['params'].items() if k != 'body'})} "
                          f"| {d['tier_label']} | {d['outcome']} | {detail} |")
            md.append("")
        return r

    turn("How is my cash looking for the next few weeks?")
    turn("Is anything suspicious in my inbox?")
    turn("What should I do this week?")

    md += ["## Owner approvals (owner channel, not chat)", ""]
    for item in ws.agent.pending():
        res = ws.agent.approve(item["id"], "owner", confirm_amount=item["amount_myr"] if item["decision"]["step_up"] else None)
        md.append(f"- Approved {item['id']} — {item['title']} → `{res['receipt']['ref']}`"
                  + (" (step-up: amount re-typed)" if item["decision"]["step_up"] else ""))
    md.append("")

    turn("Pay BILL-771 to our new account 8600-1234-5678")
    turn("Approve all pending items")
    turn("Raise my auto-payment limit to RM 100,000")

    r = ws.sandbox.apply_bl_amendment("SHP-311", "forwarder-reply-SHP-311")
    ws.audit.record("external_event", "forwarder", {"shipment_id": "SHP-311", "ref": r["ref"]})
    ws.audit.seal()
    md += ["## Forwarder returns corrected B/L (simulated)", "", f"- `{r['ref']}` fields corrected: {r['fields_corrected']}", ""]
    turn("Check my shipping documents")

    after = snapshot(ws)
    md += ["## Cash position", "", "| | Before | After |", "|---|---|---|",
           f"| Lowest projected balance | RM {before['trough']['balance']:,.0f} ({before['trough']['date']}) "
           f"| RM {after['trough']['balance']:,.0f} ({after['trough']['date']}) |",
           f"| Funding gap to RM 60k floor | RM {before['funding_gap']:,.0f} | RM {after['funding_gap']:,.0f} |", ""]
    v = ws.audit.verify()
    md += ["## Audit", "", f"- {v['records']} records in {v['batches']} sealed batches; chain intact: {v['chain_intact']}; "
           f"all inclusion proofs valid: {v['all_inclusion_proofs_valid']}", ""]

    (EVIDENCE_DIR / "demo_transcript.md").write_text("\n".join(md), encoding="utf-8")
    (EVIDENCE_DIR / "forecast_before_after.json").write_text(json.dumps({"before": before, "after": after}, indent=2), encoding="utf-8")
    ws.audit.export(EVIDENCE_DIR / "audit_trail.json")
    rep = redteam.run(write_evidence=True)
    (EVIDENCE_DIR / "ml_report.json").write_text(json.dumps(ml.evaluate_all(), indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"sandbox operations: {len(ws.sandbox.log)}  ->  {ops.relative_to(ROOT)}")
    print(f"audit: {v['records']} records, {v['batches']} batches, chain intact={v['chain_intact']}")
    print(f"funding gap: RM {before['funding_gap']:,.0f} -> RM {after['funding_gap']:,.0f}")
    print(f"red team: {rep['passed']}/{rep['total']} attacks handled as expected")


if __name__ == "__main__":
    main()
