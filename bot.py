#!/usr/bin/env python3
# ============================================================
# bot.py — Telegram Stock Bot với Combo Analysis Engine
# Source: vnstock VCI (primary) → TCBS → KBS fallback
# Tự động nhận diện mã cổ phiếu trong bất kỳ tin nhắn nào
# ============================================================
import os, re, json, time, logging, requests, threading, subprocess, unicodedata
import anthropic
import portfolio as pf

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ── Config — đọc từ biến môi trường / .env ──
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID        = os.environ.get("CHAT_ID", "")
CLAUDE_API_KEY = os.environ.get("CLAUDE_API_KEY", "")
STOCK_API_URL  = os.environ.get("STOCK_API_URL", "http://localhost:5000")

# Kiểm tra credentials bắt buộc khi khởi động
if not TELEGRAM_TOKEN:
    raise RuntimeError("Thieu TELEGRAM_TOKEN trong .env")
if not CLAUDE_API_KEY:
    raise RuntimeError("Thieu CLAUDE_API_KEY trong .env")

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

# Danh sách mã hợp lệ HOSE/HNX phổ biến (dùng để filter false positive)
# Nếu không có trong list này nhưng match pattern → vẫn thử gọi API
KNOWN_SYMBOLS = {
    # Bluechip HOSE
    "VCB","BID","CTG","TCB","MBB","VPB","ACB","STB","MSB","HDB",
    "VIC","VHM","VRE","VNM","SAB","MSN","MWG","FPT","HPG","GAS",
    "PLX","PVD","PVS","PVT","BSR","GAS","DCM","DPM","DGC",
    "GMD","HAH","VSC","DVP","PNJ","REE","CMG","VGI","ELC",
    "KBC","IDC","SZC","BCM","KDH","NVL","PDR","DXG","THD",
    "HHV","LCG","CII","VCI","HCM","SSI","VND","FTS","BSI",
    # VN30
    "VHM","VNM","GAS","SAB","CTD","HSG","NKG","TVS",
    # Mid-cap phổ biến
    "HAG","HNG","QNS","VCS","PPC","BWE","TDM","PHR",
    "TAL","PC1","EVF","VIB","LPB","OCB","BAB","NAB",
    "AAT","ANV","AST","BFC","BMP","BVH","CAV","CHP",
    # Chỉ số
    "VNINDEX","VN30",
}

# Từ khóa lệnh đặc biệt
COMMAND_KEYWORDS = {
    "/start", "/help", "/vnindex", "/sectors", "/macro", "/news",
    "/alert", "/summary", "/phan", "/khuyen", "/stock"
}

# ── Telegram helpers ──
def send_message(chat_id, text, parse_mode="Markdown"):
    try:
        r = requests.post(f"{TELEGRAM_API}/sendMessage", json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True
        }, timeout=30)
        return r.json()
    except Exception as e:
        log.error(f"send_message error: {e}")

def send_typing(chat_id):
    try:
        requests.post(f"{TELEGRAM_API}/sendChatAction",
                      json={"chat_id": chat_id, "action": "typing"}, timeout=5)
    except: pass

def api_get(path, timeout=25):
    try:
        r = requests.get(f"{STOCK_API_URL}{path}", timeout=timeout)
        return r.json()
    except Exception as e:
        return {"error": str(e)}

# ── Pattern nhận diện mã cổ phiếu ──
STOCK_PATTERN = re.compile(r'\b([A-Z]{2,4}[0-9]?[A-Z]?)\b')
PRICE_WORDS   = re.compile(r'(giá|phân tích|mua|bán|check|xem|tra|combo|signal|tín hiệu|xu hướng|kỹ thuật)', re.IGNORECASE)

def extract_symbol(text: str):
    """
    Trích xuất mã cổ phiếu từ text.
    Ưu tiên: known symbols → pattern match với context
    """
    text_upper = text.upper()
    words = re.findall(r'\b[A-Z0-9]+\b', text_upper)

    # Pass 1: tìm trong known symbols
    for w in words:
        if w in KNOWN_SYMBOLS and w not in {"VN30", "VNINDEX"}:
            return w

    # Pass 2: pattern 2-4 ký tự viết hoa, không phải stopword
    STOPWORDS = {
        "BUY","SELL","RSI","EMA","SMA","OBV","CMF","MFI","ATR","BB",
        "OK","NO","YES","THE","AND","BUT","FOR","NOT","CAN","GET","SET",
        "BOT","API","VPS","TG","AI","ML","KBS","VCI","HNX","HOSE","VN",
        "TP","SL","RR","MA","KQ","NV","MUA","BAN","GIA","CO","CHO",
        "NEN","DU","KY","NAM","MOI","MACD","VWAP","ADX","USD","VND",
        "HA","HA","TAN","TANG","GIAM","CAO","THAP","LOG"
    }
    for w in words:
        if (2 <= len(w) <= 4 and
            w.isalpha() and
            w not in STOPWORDS and
            not w.isnumeric()):
            # Thêm điều kiện: text có vẻ hỏi về cổ phiếu
            if PRICE_WORDS.search(text) or len(text.split()) <= 3:
                return w

    return None

# ── Format combo result thành Telegram message ──
def fmt_price(p):
    """vnstock VCI/TCBS/KBS đều trả về nghìn đồng — nhân 1000 để hiển thị đúng"""
    if p is None: return "—"
    return f"{p*1000:,.0f}đ" if p < 1000 else f"{p:,.0f}đ"

def calc_master_decision(mtf: dict, tp: dict, cf: dict) -> tuple[str, str, str]:
    """Tổng hợp MTF + Daily + Confluence → 1 quyết định MASTER duy nhất
    Returns: (decision_vi, reason, action)
    """
    mtf_dec       = mtf.get("decision", "")
    mtf_total     = mtf.get("mtf_total", 0)
    daily_dec     = tp.get("decision", "")
    reversal      = tp.get("reversal_count", 0)
    cf_score      = cf.get("score", 0)
    dca_allowed   = mtf.get("dca_allowed", [])
    monthly_score = mtf.get("monthly_score", 0)

    # ── Rule 1: Thoát cứng — không cần hỏi thêm ──
    if daily_dec == "THOAT_LENH" or (reversal >= 3):
        return (
            "🔴🔴 THOÁT LỆNH",
            f"Daily xác nhận đảo chiều {reversal}/5 tín hiệu",
            "Giảm tỷ trọng mạnh hoặc thoát toàn bộ"
        )

    # ── Rule 2: Monthly rất xấu ──
    if monthly_score < 20:
        return (
            "🔴 KHÔNG DCA — Monthly xấu",
            f"Monthly {monthly_score}/100 — xu hướng lớn không ủng hộ",
            "Giữ nguyên, không mua thêm, theo dõi chặt"
        )

    # ── Rule 3: MTF tốt + Confluence đủ → DCA ──
    if mtf_total >= 60 and cf_score >= 20 and reversal <= 1:
        dca_str = mtf.get("dca_note", "theo MTF")
        return (
            "🟢 DCA — Đủ xác nhận",
            f"MTF {mtf_total}/100 tốt + Confluence {cf_score}/40 đủ mạnh",
            f"DCA tại: {dca_str}"
        )

    # ── Rule 4: MTF tốt nhưng Confluence yếu → chờ ──
    if mtf_total >= 60 and cf_score < 20 and reversal <= 1:
        return (
            "⚪ NẮM GIỮ — Chờ xác nhận",
            f"MTF {mtf_total}/100 tốt nhưng Confluence {cf_score}/40 chưa đủ",
            "Giữ nguyên — chờ Confluence ≥20 hoặc giá về vùng DCA tốt hơn"
        )

    # ── Rule 5: Daily cảnh báo (2 reversal) dù MTF tốt ──
    if reversal == 2 and mtf_total >= 55:
        return (
            "🟡 THEO DÕI CHẶT — Rủi ro ngắn hạn",
            f"Daily có {reversal}/5 tín hiệu xấu — MTF {mtf_total}/100 vẫn ổn",
            "Không DCA thêm. Thoát nếu thêm 1 tín hiệu đảo chiều"
        )

    # ── Rule 6: MTF yếu ──
    if mtf_total < 50:
        return (
            "🔴 CẢNH BÁO — MTF yếu",
            f"MTF chỉ {mtf_total}/100 — xu hướng không đủ mạnh",
            "Không DCA. Chờ MTF cải thiện trên 55"
        )

    # ── Default: NẮM GIỮ ──
    return (
        "⚪ NẮM GIỮ",
        f"MTF {mtf_total}/100 — chưa đủ điều kiện DCA",
        "Tiếp tục giữ, không hành động"
    )


def format_combo_message(d: dict) -> str:
    sym     = d["symbol"]
    price   = d["price"]
    chg     = d["change"]
    chg_pct = d["change_pct"]
    date    = d["date"]
    score   = d["total_score"]
    tp      = d.get("trade_plan", {})
    mtf     = d.get("mtf", {})
    cf      = d.get("confluence", {})
    sdca    = d.get("smart_dca", {})

    chg_arrow = "📈" if chg >= 0 else "📉"
    chg_sign  = "+" if chg >= 0 else ""
    reversal  = tp.get("reversal_count", 0)
    mtf_total = mtf.get("mtf_total", 0)
    cf_score  = cf.get("score", 0)

    # Progress bars
    filled_mtf   = int(mtf_total / 10)
    bar_mtf      = "█" * filled_mtf + "░" * (10 - filled_mtf)
    filled_daily = int(score / 10)
    bar_daily    = "█" * filled_daily + "░" * (10 - filled_daily)

    # MASTER decision
    master_vi, master_reason, master_action = calc_master_decision(mtf, tp, cf)

    # Smart DCA
    regime       = sdca.get("regime", "—")
    regime_note  = sdca.get("regime_note", "")
    best_zone    = sdca.get("best_zone_str", "—")
    best_stars   = sdca.get("best_zone_stars", "")
    best_sources = sdca.get("best_zone_sources", [])
    best_dist    = sdca.get("best_zone_dist_pct", 0)
    breakout     = sdca.get("breakout_entry")
    adx_val      = sdca.get("adx", 0)

    # Format sources ngắn gọn
    src_str = " + ".join(best_sources[:4]) + (" ..." if len(best_sources) > 4 else "")

    # Khoảng cách đến vùng DCA
    if abs(best_dist) < 1:
        dist_str = "🎯 Đang ở trong vùng DCA"
    elif best_dist < 0:
        dist_str = f"Cách {abs(best_dist):.1f}% — chờ pullback"
    else:
        dist_str = f"Giá đã về vùng DCA (+{best_dist:.1f}%)"

    lines = [
        f"{'━'*34}",
        f"📊 *{sym}* | {date}",
        f"{chg_arrow} Giá: *{fmt_price(price)}* ({chg_sign}{chg_pct:.1f}%)",
        f"📦 Vol: {d['volume']/1e6:.1f}M ({d['vol_ratio']:.1f}x TB20)",
        f"{'━'*34}",
        f"",
        # ── MASTER DECISION ──
        f"🏁 *QUYẾT ĐỊNH: {master_vi}*",
        f"_{master_reason}_",
        f"➡️ *{master_action}*",
        f"",
        f"{'━'*34}",
        f"",
        # ── Smart DCA ──
        f"💰 *VÙNG DCA TỐT NHẤT* [{regime} | ADX {adx_val:.0f}]",
        f"  *{best_zone}* {best_stars}",
        f"  _{src_str}_",
        f"  {dist_str}",
    ]

    # Breakout entry nếu zone xa >8%
    if breakout:
        lines += [
            f"  ⚡ *Breakout entry:* {fmt_price(breakout['price'])}",
            f"  _{breakout['note']}_",
        ]

    lines += [
        f"",
        f"{'─'*34}",
        # ── MTF ──
        f"🌐 *MTF: {mtf_total}/100* `{bar_mtf}`",
        f"  M *{mtf.get('monthly_score',0)}* | W *{mtf.get('weekly_score',0)}* | D *{score}*",
        f"  M: _{mtf.get('monthly_note','')}_",
        f"  W: _{mtf.get('weekly_note','')}_",
    ]

    if mtf.get("monthly_warning"):
        lines.append(f"  {mtf.get('monthly_warning')}")

    lines += [
        f"",
        # ── Confluence ──
        f"🔍 *Confluence: {cf_score}/45 — {cf.get('level','')}*",
    ]
    for sig_val in cf.get("signals", {}).values():
        lines.append(f"  {sig_val}")

    # ── Kelly Sizing (v4.0) ──
    ks = d.get("kelly_size", {})
    kelly_tier = ks.get("tier", "NONE")
    kelly_emoji = {"STRONG": "💰💰", "NORMAL": "💰", "WEAK": "⚠️", "NONE": "🚫"}.get(kelly_tier, "⚪")
    if kelly_tier != "NONE":
        lines += [
            f"",
            f"{'─'*34}",
            f"{kelly_emoji} *Phân bổ vốn (Kelly)*: {ks.get('pct_per_dca','—')}/lần | Max {ks.get('max_position','—')}",
            f"  _{ks.get('note','')}_ | {ks.get('allocation_40_35_25','') or ''}",
        ]

    # ── Dynamic Weight Note (v4.0) ──
    w_note = d.get("weight_note", "")
    if w_note:
        lines.append(f"  ⚖️ _{w_note}_")

    lines += [
        f"",
        f"{'─'*34}",
        # ── Hỗ trợ & Kháng cự ──
        f"📐 *Hỗ trợ:* {fmt_price(tp.get('support_1'))} → {fmt_price(tp.get('support_2'))} → {fmt_price(tp.get('support_3'))}",
        f"📐 *Kháng cự:* {fmt_price(tp.get('resistance'))}",
        f"",
    ]

    # Tín hiệu thoát
    if reversal >= 1:
        bad = [sig for sig, st in tp.get("reversal_detail", {}).items() if "XẤU" in st]
        lines += [
            f"⚠️ *Cảnh báo thoát ({reversal}/5):* {' | '.join(bad)}",
            f"",
        ]

    lines += [
        f"{'━'*34}",
        f"📈 *Daily: {score}/100* `{bar_daily}` | ST: *{d['supertrend']}* | RSI {d['rsi14']:.0f}",
        f"C1:{d['combo1']['score']}% C2:{d['combo2']['score']}% C3:{d['combo3']['score']}% C4:{d['combo4']['score']}% C5:{d['combo5']['score']}%",
        f"EMA {d['ema9']:.1f}/{d['ema20']:.1f}/{d['ema50']:.1f} | OBV:{d['obv_trend']} CMF:{d['cmf20']:+.3f}",
        f"Dòng tiền: *{tp.get('money_flow','—')}* | Trend: *{tp.get('trend_quality','—')}*",
    ]

    return "\n".join(lines)


    return "\n".join(lines)


# ── Claude AI — Long-term Investment Assistant ──
def ask_claude(prompt: str) -> str:
    try:
        client = anthropic.Anthropic(api_key=CLAUDE_API_KEY)
        msg = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=800,
            system=(
                "Bạn là chuyên gia đầu tư dài hạn chứng khoán Việt Nam. "
                "Nhà đầu tư nắm giữ nhiều tháng đến nhiều năm, không giao dịch ngắn hạn. "
                "KHÔNG đề cập ATR, cắt lỗ theo giá cố định, hay chốt lời ngắn hạn. "
                "Tập trung vào: xu hướng dài hạn, sức mạnh chỉ báo, dòng tiền tổ chức, "
                "vùng DCA theo EMA, và khi nào thoát dựa trên đảo chiều chỉ báo. "
                "Ra quyết định rõ: MUA THÊM / NẮM GIỮ / THOÁT. "
                "Dùng tiếng Việt. Ngắn gọn, thực tế, không dài dòng."
            ),
            messages=[{"role": "user", "content": prompt}]
        )
        return msg.content[0].text
    except Exception as e:
        return f"❌ Claude API lỗi: {str(e)[:100]}"

def claude_with_combo(symbol: str, d: dict) -> str:
    tp   = d.get("trade_plan", {})
    mtf  = d.get("mtf", {})
    cf   = d.get("confluence", {})
    sdca = d.get("smart_dca", {})

    # Tính MASTER decision để truyền vào Claude — bắt buộc nhất quán
    master_vi, master_reason, master_action = calc_master_decision(mtf, tp, cf)
    # Rút gọn về action keyword
    if "DCA" in master_vi:
        master_action_key = "MUA THÊM / DCA"
    elif "THOÁT" in master_vi:
        master_action_key = "THOÁT LỆNH"
    else:
        master_action_key = "NẮM GIỮ"

    reversal_lines = "\n".join(
        f"  {status}: {sig}" for sig, status in tp.get("reversal_detail", {}).items()
    )
    cf_lines = "\n".join(f"  {v}" for v in cf.get("signals", {}).values())

    regime      = sdca.get("regime", "—")
    best_zone   = sdca.get("best_zone_str", "—")
    best_stars  = sdca.get("best_zone_stars", "")
    best_sources= ", ".join(sdca.get("best_zone_sources", []))
    best_dist   = sdca.get("best_zone_dist_pct", 0)
    breakout    = sdca.get("breakout_entry")
    fib         = sdca.get("fib") or {}
    pivot       = sdca.get("pivot") or {}
    vp          = sdca.get("vp") or {}

    breakout_line = (f"- Breakout entry: {fmt_price(breakout['price'])} — {breakout['note']}"
                     if breakout else "")
    fib_line  = (f"- Fib 38.2%/50%/61.8%: {fmt_price(fib.get('fib_382'))}/{fmt_price(fib.get('fib_500'))}/{fmt_price(fib.get('fib_618'))}"
                 if fib else "")
    pivot_line = (f"- Pivot S1/S2: {fmt_price(pivot.get('s1'))}/{fmt_price(pivot.get('s2'))}"
                  if pivot else "")
    poc_line  = (f"- Volume POC: {fmt_price(vp.get('poc'))} | VAL: {fmt_price(vp.get('val'))}"
                 if vp else "")

    # Liệt kê 5 tín hiệu thoát rõ ràng để Claude dùng đúng
    exit_5_signals = (
        "Hệ thống 5 tín hiệu thoát (cần ≥3/5 để xác nhận):\n"
        "  1. SuperTrend đổi BEAR\n"
        "  2. EMA20 cắt xuống EMA50 (death cross)\n"
        "  3. Giá đóng cửa dưới EMA50\n"
        "  4. RSI < 40 + Momentum 5 ngày < -3%\n"
        "  5. OBV giảm + MFI < 45\n"
        f"  Hiện tại: {tp.get('reversal_count',0)}/5 tín hiệu đang xấu"
    )

    prompt = (
        f"Phân tích dài hạn {symbol} ngày {d['date']}:\n"
        f"- Giá: {fmt_price(d['price'])} | Thay đổi: {d['change_pct']:+.1f}%\n"
        f"- EMA9/20/50: {fmt_price(d['ema9'])}/{fmt_price(d['ema20'])}/{fmt_price(d['ema50'])}\n"
        f"- BB Upper/Lower: {fmt_price(d.get('bb_upper'))}/{fmt_price(d.get('bb_lower'))}\n\n"
        f"[MASTER DECISION — BẮT BUỘC NHẤT QUÁN]\n"
        f"- Quyết định hệ thống: {master_vi}\n"
        f"- Lý do: {master_reason}\n"
        f"- Action: {master_action}\n"
        f"⚠️ Quyết định của bạn PHẢI là '{master_action_key}' — không được mâu thuẫn với hệ thống.\n\n"
        f"[MTF: {mtf.get('mtf_total',0)}/100]\n"
        f"- Monthly {mtf.get('monthly_score',0)}: {mtf.get('monthly_note','')}\n"
        f"- Weekly  {mtf.get('weekly_score',0)}: {mtf.get('weekly_note','')}\n"
        f"- Daily   {d['total_score']}: {tp.get('decision_vi','')}\n"
        f"{('- ⚠️ ' + mtf.get('monthly_warning','')) if mtf.get('monthly_warning') else ''}\n\n"
        f"[CONFLUENCE: {cf.get('score',0)}/40 — {cf.get('level','')}]\n"
        f"{cf_lines}\n\n"
        f"[KỸ THUẬT]\n"
        f"- ST: {d['supertrend']} | RSI: {d['rsi14']:.1f} | MACD: {d['macd']:+.3f} | OBV: {d['obv_trend']}\n"
        f"{exit_5_signals}\n\n"
        f"[SMART DCA — Regime: {regime} | {best_stars}]\n"
        f"- Vùng DCA tốt nhất: {best_zone} ({best_dist:+.1f}% từ giá)\n"
        f"- Chỉ báo hội tụ: {best_sources}\n"
        f"{fib_line}\n{pivot_line}\n{poc_line}\n"
        f"{breakout_line}\n"
        f"- Hỗ trợ: {fmt_price(tp.get('support_1'))} / {fmt_price(tp.get('support_2'))} / {fmt_price(tp.get('support_3'))}\n"
        f"- Kháng cự: {fmt_price(tp.get('resistance'))}\n\n"
        f"Yêu cầu output CHÍNH XÁC format sau:\n"
        f"Quyết định: {master_action_key} [emoji]\n"
        f"📌 Giá mua / DCA: [vùng {best_zone} — ghi rõ chỉ báo hội tụ]\n"
        f"📌 Giá vào lệnh mới: [vùng DCA tốt nhất nếu chưa có vị thế]\n"
        f"📌 Chốt lời một phần: [kháng cự {fmt_price(tp.get('resistance'))} hoặc 'Chưa — tiếp tục giữ']\n"
        f"📌 Thoát lệnh khi: [dùng đúng 5 tín hiệu hệ thống, nêu cụ thể tín hiệu nào cần kích hoạt]\n"
        f"[1-2 câu lý do chính và rủi ro lớn nhất cần theo dõi]"
    )
    return ask_claude(prompt)


# ── Alert integration ──
ALERT_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "alert.py")


# ══════════════════════════════════════════════════════════════
# CONVERSATION STATE MACHINE
# ══════════════════════════════════════════════════════════════
# CONV_STATE[chat_id] = {"step": str, "data": dict}
CONV_STATE: dict = {}

def get_state(chat_id):
    return CONV_STATE.get(str(chat_id), {})

def set_state(chat_id, step, data=None):
    CONV_STATE[str(chat_id)] = {"step": step, "data": data or {}}

def clear_state(chat_id):
    CONV_STATE.pop(str(chat_id), None)


# ══════════════════════════════════════════════════════════════
# TELEGRAM — INLINE KEYBOARD & CALLBACK HELPERS
# ══════════════════════════════════════════════════════════════

def send_keyboard(chat_id, text, buttons, parse_mode="Markdown"):
    """Gửi message kèm inline keyboard.
    buttons = [[("Label", "callback_data"), ...], ...]  (list of rows)
    """
    inline_keyboard = [
        [{"text": label, "callback_data": cb} for label, cb in row]
        for row in buttons
    ]
    try:
        r = requests.post(f"{TELEGRAM_API}/sendMessage", json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
            "reply_markup": {"inline_keyboard": inline_keyboard}
        }, timeout=30)
        return r.json()
    except Exception as e:
        log.error(f"send_keyboard error: {e}")

def edit_keyboard(chat_id, message_id, text, buttons, parse_mode="Markdown"):
    """Sửa message + keyboard cũ"""
    inline_keyboard = [
        [{"text": label, "callback_data": cb} for label, cb in row]
        for row in buttons
    ]
    try:
        requests.post(f"{TELEGRAM_API}/editMessageText", json={
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": parse_mode,
            "reply_markup": {"inline_keyboard": inline_keyboard}
        }, timeout=10)
    except Exception as e:
        log.error(f"edit_keyboard error: {e}")

def answer_callback(callback_query_id, text=""):
    """Tắt loading spinner trên button"""
    try:
        requests.post(f"{TELEGRAM_API}/answerCallbackQuery", json={
            "callback_query_id": callback_query_id,
            "text": text
        }, timeout=5)
    except: pass

def get_updates(offset=None, timeout=30):
    params = {
        "timeout": timeout,
        "allowed_updates": ["message", "callback_query"],
    }
    if offset:
        params["offset"] = offset
    try:
        r = requests.get(f"{TELEGRAM_API}/getUpdates", params=params, timeout=timeout+5)
        return r.json().get("result", [])
    except: return []


# ══════════════════════════════════════════════════════════════
# MENUS
# ══════════════════════════════════════════════════════════════

def show_main_menu(chat_id, text="Chọn chức năng:"):
    send_keyboard(chat_id, f"🤖 *Stock Bot v4.0*\n{text}", [
        [("📊 Tra cứu cổ phiếu",  "menu:lookup"),
         ("📁 Tư vấn danh mục",    "menu:portfolio")],
        [("🎯 Khuyến nghị hôm nay","menu:recommend")],
        [("📈 VN-Index",           "menu:vnindex"),
         ("🏭 Dòng tiền ngành",    "menu:sectors")],
        [("ℹ️ Hướng dẫn",          "menu:help")],
    ])

def show_portfolio_menu(chat_id):
    summary = pf.format_watchlist_summary(chat_id)
    send_keyboard(chat_id, summary, [
        [("➕ Thêm mã",           "pf:add"),
         ("➖ Xóa mã",            "pf:remove_list")],
        [("🔍 Phân tích danh mục","pf:analyze"),
         ("🔔 Alert danh mục",    "pf:alert")],
        [("📊 Tóm tắt",           "pf:summary"),
         ("⚙️ Quản lý danh mục",  "pf:manage")],
        [("🔙 Menu chính",         "menu:main")],
    ])

def show_watchlist_mgmt(chat_id):
    wls  = pf.get_watchlists(chat_id)
    act  = pf.get_active_wl_id(chat_id)
    lines = ["⚙️ *Quản lý danh mục*\n"]
    buttons = []
    for wl_id, wl in wls.items():
        marker = "✅ " if wl_id == act else ""
        count  = len(wl.get("symbols", {}))
        lines.append(f"{marker}*{wl['name']}* ({count} mã)")
        if wl_id != act:
            buttons.append([(f"▶️ Chọn: {wl['name']}", f"pf:sel_wl:{wl_id}")])
        buttons.append([(f"🗑️ Xóa: {wl['name']}", f"pf:del_wl:{wl_id}")])
    buttons.append([("➕ Tạo danh mục mới", "pf:create_wl")])
    buttons.append([("🔙 Quay lại",          "pf:back")])
    send_keyboard(chat_id, "\n".join(lines), buttons)

def show_remove_list(chat_id):
    """Hiển thị danh sách mã để xóa"""
    syms = pf.get_symbols(chat_id)
    if not syms:
        send_message(chat_id, "📋 Danh mục trống, không có gì để xóa.")
        show_portfolio_menu(chat_id)
        return
    buttons = []
    for sym in syms:
        cp  = syms[sym].get("cost_price", 0)
        qty = syms[sym].get("qty", 0)
        label = sym
        if cp > 0: label += f" | vốn {pf.fmt_price(cp)}"
        if qty > 0: label += f" | {qty:,}CP"
        buttons.append([(f"🗑️ {label}", f"pf:del_sym:{sym}")])
    buttons.append([("🔙 Quay lại", "pf:back")])
    send_keyboard(chat_id, "➖ *Chọn mã cần xóa:*", buttons)


# ══════════════════════════════════════════════════════════════
# ALERT integration
# ══════════════════════════════════════════════════════════════
ALERT_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "alert.py")

def handle_alert_scan(chat_id, manual_chat_id=None):
    cid = manual_chat_id or chat_id
    send_message(cid, "🔔 Đang quét danh mục để tìm tín hiệu mới...")
    try:
        result = subprocess.run(
            ["python3", ALERT_SCRIPT, "--once", "--chat-id", str(cid)],
            capture_output=True, text=True, timeout=300
        )
        if result.returncode != 0 and result.stderr:
            send_message(cid, f"⚠️ Alert scan lỗi: {result.stderr[-200:]}")
    except subprocess.TimeoutExpired:
        send_message(cid, "⏱️ Alert scan timeout (>5 phút)")
    except Exception as e:
        send_message(cid, f"❌ Không chạy được alert: {e}")

def handle_alert_summary(chat_id):
    send_message(chat_id, "📊 Đang tổng hợp danh mục...")
    try:
        subprocess.run(
            ["python3", ALERT_SCRIPT, "--summary", "--chat-id", str(chat_id)],
            capture_output=True, text=True, timeout=300
        )
    except Exception as e:
        send_message(chat_id, f"❌ Lỗi: {e}")


# ══════════════════════════════════════════════════════════════
# CALLBACK QUERY HANDLER
# ══════════════════════════════════════════════════════════════

def handle_callback_query(cbq):
    """Xử lý tất cả inline button clicks"""
    cqid    = cbq["id"]
    chat_id = str(cbq["from"]["id"])
    data    = cbq.get("data", "")
    answer_callback(cqid)

    parts = data.split(":")

    # ── MAIN MENU ──
    if parts[0] == "menu":
        action = parts[1] if len(parts) > 1 else ""
        if action == "main":
            clear_state(chat_id)
            show_main_menu(chat_id)
        elif action == "portfolio":
            clear_state(chat_id)
            show_portfolio_menu(chat_id)
        elif action == "lookup":
            clear_state(chat_id)
            set_state(chat_id, "lookup_sym")
            send_message(chat_id, "📊 Nhập mã cổ phiếu cần tra cứu:\n_(VD: HPG, FPT, VCB)_")
        elif action == "recommend":
            send_typing(chat_id)
            resp = ask_claude(
                "Hãy đề xuất 3 cổ phiếu Việt Nam đáng chú ý nhất hôm nay, "
                "mỗi cái 1-2 câu lý do cụ thể, thực tế."
            )
            send_message(chat_id, f"🎯 *Khuyến nghị hôm nay:*\n\n{resp}")
            show_main_menu(chat_id)
        elif action == "vnindex":
            handle_vnindex(chat_id)
        elif action == "sectors":
            handle_sectors(chat_id)
        elif action == "help":
            handle_help(chat_id)
        return

    # ── PORTFOLIO ──
    if parts[0] == "pf":
        action = parts[1] if len(parts) > 1 else ""

        if action == "back":
            clear_state(chat_id)
            show_portfolio_menu(chat_id)

        elif action == "add":
            clear_state(chat_id)
            set_state(chat_id, "pf_add_sym")
            send_message(chat_id,
                "➕ *Thêm mã vào danh mục*\n\n"
                "Cú pháp: `MÃ GIÁVỐN KL` — nhập nhiều mã cách nhau bằng khoảng trắng\n\n"
                "VD:\n"
                "`HPG 27100 1000 FPT 100000 100`\n"
                "`HPG 27100 1000` — 1 mã đầy đủ\n"
                "`VCB` — không nhập giá vốn"
            )

        elif action == "remove_list":
            clear_state(chat_id)
            show_remove_list(chat_id)

        elif action == "del_sym":
            sym = parts[2] if len(parts) > 2 else ""
            if sym:
                ok = pf.remove_symbol(chat_id, sym)
                if ok:
                    send_message(chat_id, f"✅ Đã xóa *{sym}* khỏi danh mục")
                else:
                    send_message(chat_id, f"❌ Không tìm thấy {sym}")
            show_portfolio_menu(chat_id)

        elif action == "analyze":
            clear_state(chat_id)
            send_message(chat_id, "⏳ Đang phân tích danh mục...\n_(có thể mất 30-60 giây)_")
            threading.Thread(
                target=_run_analysis,
                args=(chat_id,),
                daemon=True
            ).start()

        elif action == "alert":
            threading.Thread(
                target=handle_alert_scan,
                args=(chat_id,),
                daemon=True
            ).start()

        elif action == "summary":
            threading.Thread(
                target=handle_alert_summary,
                args=(chat_id,),
                daemon=True
            ).start()

        elif action == "manage":
            clear_state(chat_id)
            show_watchlist_mgmt(chat_id)

        elif action == "create_wl":
            set_state(chat_id, "pf_create_wl")
            send_message(chat_id, "📁 Nhập tên danh mục mới:\n_(VD: Dài hạn, Swing, Đầu cơ)_")

        elif action == "del_wl":
            wl_id = parts[2] if len(parts) > 2 else ""
            if wl_id:
                ok, msg = pf.delete_watchlist(chat_id, wl_id)
                if ok:
                    send_message(chat_id, f"🗑️ Đã xóa danh mục *{msg}*")
                else:
                    send_message(chat_id, f"❌ {msg}")
            show_watchlist_mgmt(chat_id)

        elif action == "sel_wl":
            wl_id = parts[2] if len(parts) > 2 else ""
            if wl_id and pf.set_active_wl(chat_id, wl_id):
                wls  = pf.get_watchlists(chat_id)
                name = wls.get(wl_id, {}).get("name", wl_id)
                send_message(chat_id, f"✅ Đang xem: *{name}*")
            show_portfolio_menu(chat_id)

        return


def _run_analysis(chat_id):
    result = pf.format_analysis_result(chat_id)
    send_message(chat_id, result)
    show_portfolio_menu(chat_id)


# ══════════════════════════════════════════════════════════════
# STATE-AWARE TEXT HANDLER
# ══════════════════════════════════════════════════════════════

def process_state_input(chat_id, text):
    """Xử lý input khi đang trong 1 flow có state.
    Trả về True nếu đã xử lý, False nếu không có state."""
    state = get_state(chat_id)
    step  = state.get("step")
    data  = state.get("data", {})

    if not step:
        return False

    # ── LOOKUP ──
    if step == "lookup_sym":
        sym = text.strip().upper()
        if re.match(r'^[A-Z]{2,4}[0-9]?$', sym):
            clear_state(chat_id)
            handle_stock_combo(chat_id, sym)
        else:
            send_message(chat_id, "❓ Mã không hợp lệ. VD: HPG, FPT, VCB")
        return True

    # ── ADD SYMBOL: nhập nhiều mã "MÃ GIÁVỐN KHỐILƯỢNG MÃ2 GIÁVỐN2 ..." ──
    if step == "pf_add_sym":
        tokens = text.strip().upper().split()

        # Parse thành list các (sym, cost, qty)
        entries = []
        i = 0
        while i < len(tokens):
            tok = tokens[i]
            if re.match(r'^[A-Z]{2,4}[0-9]?$', tok):
                sym  = tok
                cost = 0
                qty  = 0
                # Token tiếp theo: giá vốn?
                if i+1 < len(tokens):
                    try:
                        cost = float(tokens[i+1].replace(",", ""))
                        if cost < 0: cost = 0
                        i += 1
                        # Token tiếp theo nữa: khối lượng?
                        if i+1 < len(tokens):
                            try:
                                qty = int(tokens[i+1].replace(",", ""))
                                if qty < 0: qty = 0
                                i += 1
                            except ValueError:
                                pass
                    except ValueError:
                        pass
                entries.append((sym, cost, qty))
            i += 1

        if not entries:
            send_message(chat_id,
                "❓ Cú pháp: `MÃ GIÁVỐN KL MÃ2 GIÁVỐN2 KL2 ...`\n"
                "VD: `HPG 27100 1000 FPT 100000 100`"
            )
            return True

        results = []
        for sym, cost, qty in entries:
            ok, msg = pf.add_symbol(chat_id, sym, cost_price=cost, qty=qty)
            if ok:
                action_txt = "cập nhật" if msg == "updated" else "thêm"
                cost_txt   = f" | {pf.fmt_price(cost)}" if cost > 0 else ""
                qty_txt    = f" | {qty:,}CP" if qty > 0 else ""
                results.append(f"✅ {action_txt} *{sym}*{cost_txt}{qty_txt}")
            else:
                results.append(f"❌ *{sym}*: {msg}")

        clear_state(chat_id)
        send_message(chat_id, "\n".join(results))
        send_keyboard(chat_id, "Tiếp tục?", [
            [("➕ Thêm mã khác", "pf:add"),
             ("📋 Xem danh mục", "pf:back")],
        ])
        return True

    # ── CREATE WATCHLIST ──
    if step == "pf_create_wl":
        name = text.strip()
        if len(name) < 1 or len(name) > 30:
            send_message(chat_id, "❓ Tên danh mục 1-30 ký tự. Nhập lại:")
            return True
        ok, result = pf.create_watchlist(chat_id, name)
        clear_state(chat_id)
        if ok:
            send_message(chat_id, f"✅ Đã tạo danh mục *{name}*")
        else:
            send_message(chat_id, f"❌ {result}")
        show_portfolio_menu(chat_id)
        return True

    return False


# ══════════════════════════════════════════════════════════════
# HANDLERS (kept from v4)
# ══════════════════════════════════════════════════════════════

def handle_help(chat_id):
    msg = (
        "🤖 *Stock Bot v4.0 — Hướng dẫn*\n"
        "─────────────────────\n"
        "📌 *Menu chính:* gõ `/menu` hoặc `/start`\n\n"
        "📊 *Tra cứu cổ phiếu:*\n"
        "  Gõ thẳng mã: `HPG`, `FPT`, `VCB`\n"
        "  Hoặc: `phân tích HPG`\n\n"
        "📁 *Danh mục:*\n"
        "  → Chọn *Tư vấn danh mục* từ menu\n"
        "  Hỗ trợ: thêm/xóa mã, giá vốn, KL,\n"
        "  phân tích P&L, alert tín hiệu\n\n"
        "⚡ *Score:* ≥70 Mua mạnh | 55-69 Mua | <45 Tránh\n"
        "💰 *Kelly:* STRONG → 5-8% vốn | NORMAL → 3-5%\n\n"
        "🔔 *Alert tự động:* 9:30 | 12:00 | 15:15 | 20:00"
    )
    send_message(chat_id, msg)

def handle_vnindex(chat_id):
    send_typing(chat_id)
    d = api_get("/vnindex")
    if "error" in d:
        send_message(chat_id, f"❌ {d['error']}")
        return
    arrow = "📈" if d.get("change", 0) >= 0 else "📉"
    sign  = "+" if d.get("change", 0) >= 0 else ""
    send_message(chat_id,
        f"{arrow} *VN-Index*\n"
        f"Điểm: *{d['close']:,.2f}*\n"
        f"Thay đổi: *{sign}{d.get('change_pct', 0):.2f}%*\n"
        f"Ngày: {d.get('date','')}"
    )

def handle_sectors(chat_id):
    send_typing(chat_id)
    d = api_get("/sectors", timeout=60)
    if "error" in d:
        send_message(chat_id, f"❌ {d['error']}")
        return
    lines = ["📊 *Dòng tiền ngành hôm nay:*\n"]
    for s in d.get("sectors", []):
        sig_emoji = {"DONG TIEN VAO": "🟢", "DONG TIEN RA": "🔴"}.get(s["signal"], "⚪")
        pct  = s['avg_change_pct']
        sign = "+" if pct >= 0 else ""
        lines.append(f"{sig_emoji} *{s['sector']}*: {sign}{pct:.1f}%")
    send_message(chat_id, "\n".join(lines))


def handle_stock_combo(chat_id, symbol: str):
    """Handler chính — phân tích đầy đủ 5 combo"""
    send_typing(chat_id)
    send_message(chat_id, f"⏳ Đang phân tích *{symbol}* với 5 Combo chỉ báo...\n_(mất 5-10 giây)_")

    data = api_get(f"/combo/{symbol}", timeout=45)
    if "error" in data:
        # Fallback sang /stock nếu không đủ data
        fallback = api_get(f"/stock/{symbol}", timeout=20)
        if "error" in fallback:
            send_message(chat_id, f"❌ Không tìm thấy mã *{symbol}*. Vui lòng kiểm tra lại.")
            return
        msg = (
            f"⚠️ *{symbol}* — Dữ liệu cơ bản\n"
            f"Giá: *{fallback.get('close',0):,.0f}đ* ({fallback.get('change_pct',0):+.1f}%)\n"
            f"RSI(14): {fallback.get('rsi_14',50):.1f}\n"
            f"_(Không đủ dữ liệu lịch sử để chạy Combo Analysis)_"
        )
        send_message(chat_id, msg)
        return

    # Gửi bảng combo
    combo_msg = format_combo_message(data)
    send_message(chat_id, combo_msg)

    # Thêm dòng tiền thực intraday nếu có (chỉ trong giờ GD)
    mf = api_get(f"/moneyflow/{symbol}", timeout=10)
    if mf and "buy_pct" in mf:
        net_sign = "+" if mf["net_vol"] >= 0 else ""
        mf_msg = (
            f"💹 *Dòng tiền thực ({mf.get('source','')}) — Intraday*\n"
            f"  Mua chủ động: *{mf['buy_pct']:.1f}%* | Bán: {mf['sell_pct']:.1f}%\n"
            f"  Net: *{net_sign}{mf['net_vol']/1e6:.1f}M CP* | {mf['dominant']}"
        )
        send_message(chat_id, mf_msg)

    # Nếu score >= 40, thêm Claude insight
    if data.get("total_score", 0) >= 40:
        time.sleep(1)
        send_typing(chat_id)
        ai_insight = claude_with_combo(symbol, data)
        send_message(chat_id, f"🧠 *Claude AI nhận định:*\n\n{ai_insight}")

def handle_free_chat(chat_id, text: str):
    """Xử lý chat tự do — hỏi Claude"""
    send_typing(chat_id)
    response = ask_claude(text)
    send_message(chat_id, response)


# ══════════════════════════════════════════════════════════════
# MAIN DISPATCHER
# ══════════════════════════════════════════════════════════════

def process_message(msg):
    chat_id = str(msg["chat"]["id"])
    text    = msg.get("text", "").strip()
    if not text:
        return

    log.info(f"MSG [{chat_id}]: {text[:80]}")
    import unicodedata
    text       = unicodedata.normalize("NFC", text)
    text_lower = text.lower()
    text_upper = unicodedata.normalize("NFC", text.upper())

    # ── 1. State machine — xử lý input đang trong flow ──
    if process_state_input(chat_id, text):
        return

    # ── 2. Lệnh menu ──
    if text_lower in ["/start", "/menu", "menu", "start"]:
        clear_state(chat_id)
        show_main_menu(chat_id)
        return

    if text_lower in ["/help", "help", "giup", "huong dan"]:
        handle_help(chat_id)
        return

    if text_lower in ["/vnindex", "vnindex"]:
        handle_vnindex(chat_id)
        return

    if text_lower in ["/sectors", "sectors", "nganh", "dong tien nganh"]:
        handle_sectors(chat_id)
        return

    if text_lower in ["/alert", "alert", "quet", "quét"]:
        threading.Thread(target=handle_alert_scan, args=(chat_id,), daemon=True).start()
        return

    if text_lower in ["/summary", "summary", "tom tat", "tóm tắt"]:
        threading.Thread(target=handle_alert_summary, args=(chat_id,), daemon=True).start()
        return

    if text_lower.startswith("/macro") or text_lower == "macro":
        d = api_get("/macro")
        if "XAUUSD" in d:
            send_message(chat_id, f"🥇 Vàng XAUUSD: *${d['XAUUSD']['price']:,.2f}*")
        return

    if text_lower.startswith("/news") or text_lower == "news":
        d = api_get("/news")
        if "news" in d:
            lines = ["📰 *Tin tức mới nhất:*\n"]
            for a in d["news"]:
                lines.append(f"• {a['title']}\n  _{a['source']} | {a['published']}_")
            send_message(chat_id, "\n".join(lines))
        return

    # ── 3. Auto-detect mã cổ phiếu ──
    words = text.split()
    if 1 <= len(words) <= 2:
        candidate = words[0].upper()
        if re.match(r'^[A-Z]{2,4}[0-9]?[A-Z]?$', candidate):
            if candidate in KNOWN_SYMBOLS or len(candidate) in [2, 3]:
                if candidate not in {"NO", "OK", "BN", "TK", "TP", "SL", "GD", "KQ"}:
                    handle_stock_combo(chat_id, candidate)
                    return

    symbol = extract_symbol(text)
    if symbol and symbol not in {"VN", "OK", "NO", "THE"}:
        if not any(text_lower.startswith(cmd) for cmd in ["/phan", "/khuyen", "khuyen nghi"]):
            handle_stock_combo(chat_id, symbol)
            return

    phan_match = re.search(r'(?:phan tich|phân tích|analyze|check)\s+([A-Z]{2,4})', text_upper)
    if phan_match:
        handle_stock_combo(chat_id, phan_match.group(1))
        return

    if any(kw in text_lower for kw in ["khuyen nghi", "khuyến nghị", "nen mua gi", "nên mua gì"]):
        send_typing(chat_id)
        resp = ask_claude(
            f"Câu hỏi từ nhà đầu tư: {text}\n\n"
            "Đề xuất 2-3 cổ phiếu đáng chú ý với lý do cụ thể."
        )
        send_message(chat_id, f"💡 *Gợi ý đầu tư:*\n\n{resp}")
        return

    # ── 4. Fallback: show menu thay vì free chat ──
    show_main_menu(chat_id, "Không hiểu lệnh. Chọn chức năng:")


# ══════════════════════════════════════════════════════════════
# BOT MAIN LOOP
# ══════════════════════════════════════════════════════════════

def main():
    log.info("🤖 Stock Bot v5 khởi động — Menu-driven + Portfolio v5")
    try:
        requests.post(f"{TELEGRAM_API}/deleteWebhook", timeout=10)
    except: pass

    show_main_menu(CHAT_ID)

    offset = None
    while True:
        try:
            updates = get_updates(offset=offset, timeout=30)
            for upd in updates:
                offset = upd["update_id"] + 1
                # Callback query (button click)
                if "callback_query" in upd:
                    try:
                        handle_callback_query(upd["callback_query"])
                    except Exception as e:
                        log.error(f"callback error: {e}")
                # Text message
                elif "message" in upd and "text" in upd["message"]:
                    try:
                        process_message(upd["message"])
                    except Exception as e:
                        log.error(f"message error: {e}")
        except KeyboardInterrupt:
            break
        except Exception as e:
            log.error(f"Main loop error: {e}")
            time.sleep(5)

if __name__ == "__main__":
    main()
