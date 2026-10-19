"""Inbound content guard.

Everything that arrives from outside — emails, documents, attachments — is
data, never instructions. This module screens it before the agent reads it
and labels it with a trust level that travels with anything derived from it
(see policy.Provenance). Four checks:

1. Sender verification — the sender's domain must match a registered
   counterparty domain; lookalikes (edit distance, extra hyphens/words) and
   display-name impersonation are flagged.
2. Payment-instruction change (business email compromise) — any message
   that introduces new bank details or asks to redirect a payment.
3. Prompt injection — text addressed to an AI, instructions to ignore rules,
   to approve or transfer, or to hide things from the user, including inside
   HTML comments and other hidden markup.
4. Sensitive-data masking — account numbers and IBANs are masked before the
   text is shown to a model or written to logs.
"""

import re
import unicodedata
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List

from .models import Company, InboundMessage

# Carriers and banks whose names are commonly borrowed in display names.
IMPERSONATED_BRANDS = ("maersk", "msc", "cma cgm", "hapag", "evergreen", "dhl", "fedex")
THROWAWAY_DOMAINS = {"mailinator.com", "tempmail.com", "yopmail.com", "guerrillamail.com"}

INJECTION_PATTERNS = [
    (r"ignore (all |any |your )?(previous|prior|above) (instructions|rules)", "asks the agent to ignore its instructions"),
    (r"\b(ai|assistant|agent|copilot|llm|chatbot)\b\s*[:,]", "text addressed to an AI system"),
    (r"you are (now )?(authori[sz]ed|allowed|permitted)", "claims to grant the agent authority"),
    (r"(do not|don't|never) (tell|mention|inform|notify)", "asks to hide activity from the user"),
    (r"\b(approve|execute|transfer|send|wire|remit)\b[^.]{0,60}\b(all|immediately|now|without)\b", "pushes an immediate money movement"),
    (r"system prompt|developer mode|jailbreak", "references model internals"),
    # Malay / Indonesian
    (r"abaikan (semua )?(arahan|perintah|instruksi)", "asks the agent to ignore its instructions (Malay/Indonesian)"),
    (r"jangan (beritahu|maklumkan|bagitahu|kasih tahu)", "asks to hide activity from the user (Malay/Indonesian)"),
    # Chinese
    (r"(忽略|无视|忽视).{0,6}(之前|以上|先前)?.{0,4}(指令|指示|规则)", "asks the agent to ignore its instructions (Chinese)"),
    (r"(不要|别|勿).{0,4}(告诉|通知|透露)", "asks to hide activity from the user (Chinese)"),
    (r"(立即|马上).{0,8}(转账|付款|汇款)", "pushes an immediate money movement (Chinese)"),
]
HIDDEN_MARKUP = [r"<!--.*?-->", r"<[^>]*style=[\"'][^\"']*(display:\s*none|font-size:\s*0)[^>]*>.*?</[^>]+>", r"[​-‏⁠]"]
BANK_CHANGE_PATTERNS = [
    r"(new|updated|changed?|different)\s+(bank|account|banking)\s*(details|account|information|no)?",
    r"(remit|pay|transfer|send)\b.{0,80}\b(to|into)\s+(our|the)?\s*new\s+account",
    r"account (is )?(suspended|frozen|closed|under audit)",
    r"akaun (bank )?(baru|yang baru)|butiran bank (baru|dikemas ?kini)",       # Malay
    r"(新|更改|变更).{0,4}(账户|帐户|银行账号|收款账户)",                       # Chinese
]
ACCOUNT_NUMBER = re.compile(r"\b\d{4}[- ]?\d{4}[- ]?\d{4,6}\b")
IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]){11,30}\b")
URGENCY = re.compile(r"\b(urgent|immediately|today|asap|do not call|lines are down)\b", re.I)


def mask(text: str) -> str:
    text = IBAN.sub(lambda m: m.group(0)[:4] + " **** " + m.group(0)[-4:], text)
    return ACCOUNT_NUMBER.sub(lambda m: "****-" + re.sub(r"\D", "", m.group(0))[-4:], text)


def _edit_distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


# Characters that render like Latin letters (Cyrillic, Greek, digits) — the
# classic way to fake a domain such as "hutanlestari.example" with a Cyrillic "а".
CONFUSABLES = str.maketrans({
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "х": "x", "у": "y", "і": "i", "ј": "j", "ѕ": "s",
    "ԁ": "d", "һ": "h", "ӏ": "l", "к": "k", "м": "m", "т": "t", "в": "b", "н": "h",
    "α": "a", "ο": "o", "ρ": "p", "ε": "e", "ι": "i", "κ": "k", "ν": "v", "τ": "t", "υ": "u",
    "0": "o", "1": "l",
})


def skeleton(domain: str) -> str:
    d = unicodedata.normalize("NFKC", domain.lower()).translate(CONFUSABLES)
    return d.replace("rn", "m").replace("vv", "w")


def _squash(domain: str) -> str:
    return re.sub(r"[^a-z0-9]", "", domain.split(".")[0].lower())


@dataclass
class Finding:
    check: str
    severity: str        # info | warning | critical
    detail: str


@dataclass
class ScanResult:
    message_id: str
    sender: str
    trust: str           # verified | unverified | quarantined
    counterparty: str = ""
    findings: List[Finding] = field(default_factory=list)
    masked_body: str = ""
    hidden_text_removed: bool = False
    ml: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def known_domains(company: Company) -> Dict[str, str]:
    out = {p.domain: p.name for p in company.payees if p.domain}
    for customer, email in company.customer_contacts.items():
        out[email.split("@")[-1]] = customer
    if company.forwarder_contact:
        out[company.forwarder_contact.split("@")[-1]] = "Freight forwarder"
    return out


def scan(company: Company, msg: InboundMessage) -> ScanResult:
    domain = msg.sender.split("@")[-1].lower()
    registry = known_domains(company)
    res = ScanResult(msg.id, msg.sender, "unverified")
    findings = res.findings

    # 1. Sender verification
    homoglyph = next((k for k in registry if skeleton(domain) == skeleton(k) and domain != k), None)
    if homoglyph or domain.startswith("xn--") or ".xn--" in domain or not domain.isascii():
        target = registry.get(homoglyph, "a registered counterparty") if homoglyph else "a registered counterparty"
        findings.append(Finding("sender_homoglyph", "critical",
                                f"'{domain}' uses look-alike or non-Latin characters to imitate {target}"))
    elif domain in registry:
        res.counterparty = registry[domain]
        res.trust = "verified"
    else:
        for known, name in registry.items():
            close = _edit_distance(_squash(domain), _squash(known)) <= 3
            contains = _squash(known) in _squash(domain) or _squash(domain) in _squash(known)
            named = name.split()[0].lower() in msg.display_name.lower()
            if close or contains or named:
                findings.append(Finding("sender_lookalike", "critical",
                                        f"'{domain}' imitates the registered domain of {name} ('{known}')"))
                break
        else:
            findings.append(Finding("sender_unknown", "info", f"'{domain}' is not a registered counterparty"))
    shown = msg.display_name.lower()
    for brand in IMPERSONATED_BRANDS:
        if brand in shown and brand.replace(" ", "") not in domain.replace("-", ""):
            findings.append(Finding("display_name_impersonation", "warning",
                                    f"display name claims '{msg.display_name}' but the domain is '{domain}'"))
    if domain in THROWAWAY_DOMAINS:
        findings.append(Finding("sender_throwaway", "warning", f"'{domain}' is a disposable mailbox provider"))

    # 3. Prompt injection (scan the raw body, hidden markup included)
    body = msg.subject + "\n" + msg.body
    hidden = []
    for pat in HIDDEN_MARKUP:
        hidden += re.findall(pat, body, flags=re.S | re.I)
    if hidden:
        findings.append(Finding("hidden_content", "warning", f"{len(hidden)} hidden markup block(s) removed before display"))
    lowered = body.lower()
    for pat, why in INJECTION_PATTERNS:
        if re.search(pat, lowered, flags=re.S):
            findings.append(Finding("prompt_injection", "critical", why))

    # 2. Payment-instruction change
    if any(re.search(p, lowered) for p in BANK_CHANGE_PATTERNS) or (
            ACCOUNT_NUMBER.search(body) and re.search(r"\b(remit|transfer|pay)\b", lowered)):
        findings.append(Finding("payment_redirect", "critical",
                                "asks to send money to bank details that are not on file — classic invoice-redirection fraud"))
    if URGENCY.search(body) and any(f.severity == "critical" for f in findings):
        findings.append(Finding("pressure_tactics", "warning", "urgency / 'do not call' language alongside a payment request"))

    visible = body
    for pat in HIDDEN_MARKUP:
        visible = re.sub(pat, "[hidden content removed]", visible, flags=re.S | re.I)
    # 5. ML threat classifier (catches paraphrases the patterns above miss).
    from . import ml
    res.ml = ml.threat_model().score(body)
    if res.ml["threat"] >= ml.QUARANTINE_AT:
        findings.append(Finding("ml_threat", "critical",
                                f"ML classifier: {res.ml['label'].replace('_', ' ')} (threat probability {res.ml['threat']:.0%})"))
    elif res.ml["threat"] >= ml.REVIEW_AT:
        findings.append(Finding("ml_threat", "warning",
                                f"ML classifier: possible {res.ml['label'].replace('_', ' ')} ({res.ml['threat']:.0%}) — read carefully"))

    res.masked_body = mask(visible)
    res.hidden_text_removed = bool(hidden)
    if any(f.severity == "critical" for f in findings):
        res.trust = "quarantined"
    return res


def scan_inbox(company: Company) -> List[ScanResult]:
    return [scan(company, m) for m in company.inbox]
