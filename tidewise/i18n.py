"""English, Simplified Chinese and Bahasa Malaysia for the Copilot.

Works offline: the owner can ask in Chinese or Malay and the deterministic
router still understands (we map the question onto the English intent
vocabulary — only for routing; the owner's original words are what get
logged), and the main reply sentences and section titles come back in their
language. With Gemini configured, llm.narrate() writes the whole reply in
that language, and the number-grounding firewall still checks every figure.

Detection is deliberately simple and explainable: any CJK character means
Chinese; two or more distinctive Malay words means Malay; else English. The
UI can also force a language.
"""

import re
from contextvars import ContextVar
from typing import Any, Dict, List, Tuple

LANGS = ("en", "zh", "ms")
LANG_NAME = {"en": "English", "zh": "Simplified Chinese (简体中文)", "ms": "Bahasa Malaysia"}
_current: ContextVar[str] = ContextVar("tidewise_lang", default="en")

MALAY_WORDS = {
    "saya", "kami", "apa", "berapa", "patut", "perlu", "buat", "minggu", "ini", "tunai", "wang", "duit",
    "aliran", "ramalan", "bagaimana", "jika", "kalau", "pembiayaan", "pinjaman", "invois", "pelanggan",
    "boleh", "adakah", "tolong", "untuk", "dengan", "bayar", "hari", "lewat", "jualan", "turun", "naik",
    "tertunggak", "peti", "masuk", "emel", "tempahan", "pesanan", "pembekal", "kebarangkalian", "peluang",
    "risiko", "selamat", "kesihatan", "perniagaan", "tunjukkan", "senarai", "mana", "siapa", "hutang",
    "luluskan", "semua", "pembayaran", "sekarang", "akaun", "baru", "jumlah", "terima", "kasih",
}


def detect(text: str) -> str:
    if re.search(r"[一-鿿]", text):
        return "zh"
    words = set(re.findall(r"[a-z]+", text.lower()))
    return "ms" if len(words & MALAY_WORDS) >= 2 else "en"


def set_lang(lang: str) -> None:
    _current.set(lang if lang in LANGS else "en")


def get_lang() -> str:
    return _current.get()


# -- understanding: map ZH / MS questions onto the English routing vocabulary --------
# (pattern, replacement) applied in order; replacements use \1-style groups.
_ZH: List[Tuple[str, str]] = [
    (r"(批准|同意|通过)(所有|全部|一切)", " approve all pending "),
    (r"(让|使|帮)?我?(有)?\s*(\d{2}(?:\.\d)?)\s*[%％]\s*(的)?(安全|把握|放心|确定)", r" make me \3% safe "),
    (r"(这周|本周|这个星期|这礼拜).*(做什么|该做|应该做|怎么办)|帮我(处理|搞定)|自动驾驶", " what should i do this week "),
    (r"如果\s*(\S+?)\s*(晚|迟|延迟)(付款?|付)?\s*(\d+)\s*天", r" what if \1 pays \4 days late "),
    (r"如果\s*(\S+?)\s*(早|提前)(付款?|付)?\s*(\d+)\s*天", r" what if \1 pays \4 days early "),
    (r"(如果)?.*(销售|销量|营业额)(下降|下跌|减少)\s*(\d+(?:\.\d+)?)\s*[%％]", r" what if sales drop \4% "),
    (r"(如果)?.*(销售|销量|营业额)(上升|增长|增加)\s*(\d+(?:\.\d+)?)\s*[%％]", r" what if sales rise \4% "),
    (r"(概率|几率|可能性|机率|风险有多大|有多大可能|最坏情况|蒙特卡洛)", " chances "),
    (r"(现金流?|余额|够不够钱|资金缺口|预测)", " cash forecast "),
    (r"(融资|贷款|借钱|借款|资金方案)", " financing "),
    (r"(发票|采购订单|对账|三方匹配)", " check invoices "),
    (r"(订单|预订|定金|订金|产能)", " orders "),
    (r"(收件箱|邮件|诈骗|钓鱼|可疑)", " inbox "),
    (r"(谁欠|欠款|应收|逾期)", " overdue receivable "),
    (r"(账单|应付|供应商)", " bills "),
    (r"(经营状况|财务分析|利润率|毛利)", " business health "),
]
_MS: List[Tuple[str, str]] = [
    (r"\b(luluskan|setujui|sahkan) (semua|kesemua|segala)\b", " approve all pending "),
    (r"\b(jadikan|buat|pastikan)\b.{0,20}?(\d{2}(?:\.\d)?)\s*%\s*(selamat|pasti|yakin)", r" make me \2% safe "),
    (r"\bapa (yang )?(patut|perlu|harus) (saya|kami) (buat|lakukan)\b|\buruskan\b|\bautopilot\b", " what should i do "),
    (r"\b(bagaimana|macam mana) (jika|kalau)\b|\bjika\b|\bkalau\b|\bsimulasi\b", " what if "),
    (r"\bbayar (\d+) hari (lewat|lambat)\b", r" pays \1 days late "),
    (r"\bbayar (\d+) hari (awal|lebih awal)\b", r" pays \1 days early "),
    (r"\bjualan (turun|jatuh|merosot|berkurang)( sebanyak)? (\d+(?:\.\d+)?)\s*%", r" sales drop \3% "),
    (r"\bjualan (naik|meningkat|bertambah)( sebanyak)? (\d+(?:\.\d+)?)\s*%", r" sales rise \3% "),
    (r"\b(kebarangkalian|peluang|risiko|kemungkinan|senario terburuk)\b", " chances "),
    (r"\b(aliran tunai|tunai|baki|ramalan|cukup duit|cukup wang)\b", " cash forecast "),
    (r"\b(pembiayaan|pinjaman|meminjam|modal kerja)\b", " financing "),
    (r"\b(invois|pesanan belian)\b", " check invoices "),
    (r"\b(tempahan|pesanan|deposit|kapasiti)\b", " orders "),
    (r"\b(peti masuk|e-?mel|penipuan|mencurigakan)\b", " inbox "),
    (r"\b(tertunggak|hutang pelanggan|siapa (yang )?berhutang)\b", " overdue receivable "),
    (r"\b(bil|pembekal|pembayaran akan datang)\b", " bills "),
    (r"\b(kesihatan perniagaan|analisis|margin|keuntungan)\b", " business health "),
]


# Currency conversion ("把10000令吉换成人民币", "tukar RM 10,000 ke yuan"), applied first and only
# when the message names a currency, so the generic words (把, ke) don't touch other questions.
_ZH_FX_HINT = re.compile(r"人民币|令吉|马币|林吉特|美元|美金|rmb|RMB|cny|CNY|usd|USD|rm\s*\d|RM\s*\d")
_ZH_FX: List[Tuple[str, Any]] = [
    (r"(\d+(?:\.\d+)?)\s*万", lambda m: str(int(round(float(m.group(1)) * 10_000)))),
    (r"人民币|RMB|rmb", " cny "),
    (r"令吉|马币|林吉特", " myr "),
    (r"美元|美金", " usd "),
    (r"(兑换成|转换成|换成|兑成|转成|兑换为|换为)", " to "),
    (r"(cny|myr|usd)\s*(为|成)", r"\1 to "),
    (r"(兑换|换汇|换|把)", " convert "),
]
_MS_FX_HINT = re.compile(r"\b(tukar(kan)?|tukaran)\b")
_MS_FX: List[Tuple[str, str]] = [
    (r"\b(tukar(kan)?|tukaran)\b", " convert "),
    (r"\b(ke|kepada)\b", " to "),
    (r"\bdolar( as| amerika)?\b", " usd "),
]


def to_routing_text(text: str, lang: str) -> str:
    """English keywords the deterministic router understands. Customer names
    and numbers pass through untouched."""
    if lang == "en":
        return text
    rules = _ZH if lang == "zh" else _MS
    t = text if lang == "zh" else text.lower()
    if lang == "zh" and _ZH_FX_HINT.search(t):
        for pat, rep in _ZH_FX:
            t = re.sub(pat, rep, t)
    if lang == "ms" and _MS_FX_HINT.search(t):
        for pat, rep in _MS_FX:
            t = re.sub(pat, rep, t)
    out = t
    for pat, rep in rules:
        out = re.sub(pat, rep, out)
    return out


# -- replying ---------------------------------------------------------------------------
T: Dict[str, Dict[str, str]] = {
    "fx_rate": {
        "en": "Rate: 1 {frm} = {rate} {to} ({src}). {frm} {amount} → about {to} {out}.",
        "zh": "汇率：1 {frm} = {rate} {to}（{src}）。{frm} {amount} → 约 {to} {out}。",
        "ms": "Kadar: 1 {frm} = {rate} {to} ({src}). {frm} {amount} → kira-kira {to} {out}.",
    },
    "fx_ask": {
        "en": "Convert RM {amount} into which currency: USD or yuan (CNY)?",
        "zh": "RM {amount} 要换成哪种货币：美元（USD）还是人民币（CNY）？",
        "ms": "Tukar RM {amount} kepada mata wang apa: USD atau yuan (CNY)?",
    },
    "help": {
        "en": "I can forecast your cash, check trade documents, screen your inbox, analyse the business, match financing, "
              "run what-if scenarios, tell you the odds of running short, send reminders and pay approved bills within your "
              "limits. Try “what should I do this week?” or “what if Harbour pays 30 days late?”",
        "zh": "我可以预测现金流、核对发票与采购订单、筛查收件箱、分析经营状况、匹配融资方案、做假设模拟、计算资金短缺的概率，"
              "并在您的权限范围内发送催款提醒和支付已批准的账单。试试“这周我该做什么？”或“如果 Harbour 晚付30天会怎样？”",
        "ms": "Saya boleh meramal aliran tunai, menyemak invois dengan pesanan belian, menapis peti masuk, menganalisis perniagaan, "
              "memadankan pembiayaan, menjalankan simulasi “bagaimana jika”, mengira peluang kekurangan tunai, menghantar "
              "peringatan dan membayar bil yang diluluskan dalam had anda. Cuba “apa patut saya buat minggu ini?”",
    },
    "whatif_head": {"en": "What if {changes}: lowest point {b} ({bd}) → {w} ({wd}).",
                    "zh": "假设{changes}：最低余额 {b}（{bd}）→ {w}（{wd}）。",
                    "ms": "Jika {changes}: baki terendah {b} ({bd}) → {w} ({wd})."},
    "impact": {"en": "Impact", "zh": "影响", "ms": "Kesan"},
    "gap_line": {"en": "Funding gap {a} → {b}", "zh": "资金缺口 {a} → {b}", "ms": "Jurang dana {a} → {b}"},
    "close_line": {"en": "Balance in 13 weeks {a} → {b}", "zh": "13周后余额 {a} → {b}", "ms": "Baki dalam 13 minggu {a} → {b}"},
    "first_floor": {"en": "You'd first drop below your floor on {d}", "zh": "将在 {d} 首次跌破您的安全线",
                    "ms": "Anda akan jatuh bawah paras minimum pada {d}"},
    "sim_only": {"en": "Nothing was changed in your books — this is a simulation.",
                 "zh": "您的账目没有任何改动——这只是模拟。",
                 "ms": "Tiada apa-apa diubah dalam akaun anda — ini hanya simulasi."},
    "cash_dips": {"en": "Cash dips to {low} on {d} — {gap} below your {floor} floor.",
                  "zh": "现金将在 {d} 跌至 {low}，比您的安全线 {floor} 低 {gap}。",
                  "ms": "Tunai jatuh ke {low} pada {d} — {gap} di bawah paras minimum {floor} anda."},
    "cash_ok": {"en": "Cash stays above your {floor} floor for the next 13 weeks (low point {low} on {d}).",
                "zh": "未来13周现金都高于您的安全线 {floor}（最低 {low}，在 {d}）。",
                "ms": "Tunai kekal di atas paras minimum {floor} untuk 13 minggu akan datang (terendah {low} pada {d})."},
    "why": {"en": "Why", "zh": "原因", "ms": "Sebab"},
    "health": {"en": "Business health", "zh": "经营状况", "ms": "Kesihatan perniagaan"},
    "mismatch": {"en": "Invoices that don't match the purchase order", "zh": "与采购订单不符的发票",
                 "ms": "Invois yang tidak sepadan dengan pesanan belian"},
    "all_match": {"en": "Every invoice matches its purchase order and delivery note.",
                  "zh": "所有发票都与采购订单和送货单一致。",
                  "ms": "Setiap invois sepadan dengan pesanan belian dan nota penghantaran."},
    "orders": {"en": "Order book & bookings", "zh": "订单与预订", "ms": "Buku pesanan & tempahan"},
    "fin_title": {"en": "Cheapest way to cover {need} by {d}", "zh": "在 {d} 前补足 {need} 的最低成本方案",
                  "ms": "Cara termurah untuk menampung {need} sebelum {d}"},
    "no_gap": {"en": "No funding gap in the next 13 weeks — no need to borrow.",
               "zh": "未来13周没有资金缺口——无需借款。",
               "ms": "Tiada jurang dana dalam 13 minggu akan datang — tidak perlu meminjam."},
    "quarantined": {"en": "Quarantined", "zh": "已隔离", "ms": "Dikuarantin"},
    "inbox_clean": {"en": "Inbox is clean.", "zh": "收件箱安全。", "ms": "Peti masuk bersih."},
    "receivables": {"en": "{n} open invoices, {m} overdue", "zh": "{n} 张未收发票，{m} 张逾期",
                    "ms": "{n} invois belum dibayar, {m} tertunggak"},
    "next_bills": {"en": "Next bills", "zh": "即将到期的账单", "ms": "Bil seterusnya"},
    "done": {"en": "Done on my own", "zh": "我已自动完成", "ms": "Telah saya lakukan sendiri"},
    "waiting": {"en": "Waiting for your approval", "zh": "等待您批准", "ms": "Menunggu kelulusan anda"},
    "refused": {"en": "Refused", "zh": "已拒绝", "ms": "Ditolak"},
    "heads_up": {"en": "Heads-up", "zh": "提醒", "ms": "Makluman"},
    "risk_head": {
        "en": "In {n} simulated futures, {p} dip below your floor. The single forecast says the gap is {det}; "
              "the typical shortfall is {p50}, and 1 in 10 futures needs {p90}.",
        "zh": "在 {n} 次模拟的未来中，有 {p} 会跌破您的安全线。单一预测给出的缺口是 {det}；"
              "典型缺口为 {p50}，十分之一的情况下需要 {p90}。",
        "ms": "Dalam {n} masa depan yang disimulasi, {p} jatuh di bawah paras minimum anda. Ramalan tunggal memberi jurang "
              "{det}; kekurangan biasa ialah {p50}, dan 1 dalam 10 masa depan memerlukan {p90}."},
    "risk_head_nogap": {
        "en": "The single forecast shows no gap, but in {n} simulated futures {p} still dip below your floor — "
              "1 in 10 futures needs {p90}.",
        "zh": "单一预测显示没有资金缺口，但在 {n} 次模拟的未来中，仍有 {p} 会跌破您的安全线——十分之一的情况下需要 {p90}。",
        "ms": "Ramalan tunggal tidak menunjukkan jurang, tetapi dalam {n} masa depan yang disimulasi, {p} masih jatuh di "
              "bawah paras minimum anda — 1 dalam 10 memerlukan {p90}."},
    "risk_classic": {
        "en": "The usual plan (cover the single forecast's gap) costs {cost} but still leaves a {p} chance of breaching your floor.",
        "zh": "常规方案（只补足单一预测的缺口）成本 {cost}，但仍有 {p} 的概率跌破安全线。",
        "ms": "Pelan biasa (tampung jurang ramalan tunggal) berharga {cost} tetapi masih ada {p} peluang jatuh bawah paras minimum."},
    "risk_plan_title": {"en": "Cheapest plan to be {target} safe — {cost} in total, risk → {p}",
                        "zh": "达到 {target} 安全的最低成本方案——共 {cost}，风险降至 {p}",
                        "ms": "Pelan termurah untuk {target} selamat — jumlah {cost}, risiko → {p}"},
    "risk_not_reached": {"en": "That's the lowest risk the available options can reach ({p}).",
                         "zh": "这是现有选项能达到的最低风险（{p}）。",
                         "ms": "Itulah risiko terendah yang boleh dicapai dengan pilihan sedia ada ({p})."},
    "risk_offer": {"en": "Nothing has been done yet. Say “make me 95% safe” and I'll propose these steps — your policy decides "
                         "which run on their own and which wait for your approval.",
                   "zh": "目前尚未执行任何操作。说“让我95%安全”，我会提出这些步骤——由您的权限策略决定哪些自动执行、哪些等待您批准。",
                   "ms": "Belum ada tindakan diambil. Katakan “jadikan saya 95% selamat” dan saya akan mencadangkan langkah ini — "
                         "polisi anda menentukan yang mana berjalan sendiri dan yang mana menunggu kelulusan."},
    "risk_acted": {"en": "I've proposed each step; the permission policy decided what runs now and what waits for you (below).",
                   "zh": "我已提出每个步骤；权限策略已决定哪些立即执行、哪些等待您批准（见下文）。",
                   "ms": "Saya telah mencadangkan setiap langkah; polisi kebenaran menentukan apa yang berjalan sekarang dan apa yang menunggu anda (di bawah)."},
    "plan_now": {"en": "Today, if you do nothing", "zh": "如果今天什么都不做", "ms": "Hari ini, jika anda tidak berbuat apa-apa"},
    "plan_fix": {"en": "Send corrected {inv} to {cust} (matches their PO)", "zh": "向 {cust} 发送更正后的 {inv}（与其采购订单一致）",
                 "ms": "Hantar {inv} yang diperbetulkan kepada {cust} (sepadan dengan PO mereka)"},
    "plan_remind": {"en": "Remind {cust} about {inv} ({days} days overdue)", "zh": "提醒 {cust} 支付 {inv}（逾期 {days} 天）",
                    "ms": "Ingatkan {cust} tentang {inv} (tertunggak {days} hari)"},
    "plan_ext": {"en": "Ask {name} to move {bill} ({amt}) by 30 days, 1.5% surcharge",
                 "zh": "请 {name} 将 {bill}（{amt}）延后30天，附加1.5%费用",
                 "ms": "Minta {name} menangguhkan {bill} ({amt}) selama 30 hari, caj tambahan 1.5%"},
    "plan_fin": {"en": "Draw {amt} of {name}", "zh": "提取 {amt} 的{name}", "ms": "Ambil {amt} daripada {name}"},
    "fin_invoice": {"en": "Invoice financing", "zh": "发票融资", "ms": "pembiayaan invois"},
    "fin_po": {"en": "Purchase-order financing", "zh": "订单融资", "ms": "pembiayaan pesanan belian"},
}


def L(key: str, **kw) -> str:
    entry = T[key]
    return entry.get(get_lang(), entry["en"]).format(**kw)
