#!/usr/bin/env python3
# ============================================================
# bot.py — Telegram Stock Bot với Combo Analysis Engine
# Tự động nhận diện mã cổ phiếu trong bất kỳ tin nhắn nào
# ============================================================
import os, re, json, time, logging, requests
import anthropic

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
    "/phan", "/khuyen", "/stock"
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

def get_updates(offset=None, timeout=30):
    params = {"timeout": timeout, "allowed_updates": ["message"]}
    if offset:
        params["offset"] = offset
    try:
        r = requests.get(f"{TELEGRAM_API}/getUpdates", params=params, timeout=timeout+5)
        return r.json().get("result", [])
    except: return []

# ── Stock API ──
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
    """KBS trả về nghìn đồng — nhân 1000 để hiển thị đúng"""
    if p is None: return "—"
    return f"{p*1000:,.0f}đ" if p < 1000 else f"{p:,.0f}đ"

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

    chg_arrow = "📈" if chg >= 0 else "📉"
    chg_sign  = "+" if chg >= 0 else ""
    filled    = int(score / 10)
    bar       = "█" * filled + "░" * (10 - filled)
    decision  = tp.get("decision", "")
    reversal  = tp.get("reversal_count", 0)

    # MTF bar
    mtf_total = mtf.get("mtf_total", 0)
    mtf_filled = int(mtf_total / 10)
    mtf_bar    = "█" * mtf_filled + "░" * (10 - mtf_filled)

    lines = [
        f"{'━'*34}",
        f"📊 *{sym}* | {date}",
        f"{chg_arrow} Giá: *{fmt_price(price)}* ({chg_sign}{chg_pct:.1f}%)",
        f"📦 Vol: {d['volume']/1e6:.1f}M ({d['vol_ratio']:.1f}x TB20)",
        f"{'━'*34}",
        f"",
    ]

    # ── Block MTF Decision (ưu tiên cao nhất) ──
    if mtf:
        mtf_warn = mtf.get("monthly_warning")
        lines += [
            f"🌐 *MTF DECISION: {mtf_total}/100* `{mtf_bar}`",
            f"{mtf.get('decision_vi','—')}",
            f"_{mtf.get('weights','')}_",
        ]
        if mtf_warn:
            lines.append(f"{mtf_warn}")
        lines += [
            f"💰 DCA: _{mtf.get('dca_note','—')}_",
            f"",
            f"  Monthly *{mtf.get('monthly_score',0)}* | Weekly *{mtf.get('weekly_score',0)}* | Daily *{score}*",
            f"  M: _{mtf.get('monthly_note','')}_",
            f"  W: _{mtf.get('weekly_note','')}_",
            f"",
        ]

    # ── Block Confluence ──
    if cf:
        cf_score = cf.get("score", 0)
        cf_level = cf.get("level", "")
        lines += [
            f"{'─'*34}",
            f"🔍 *CONFLUENCE: {cf_score}/40 — {cf_level}*",
        ]
        for sig_name, sig_val in cf.get("signals", {}).items():
            lines.append(f"  {sig_val}")
        lines.append("")

    # ── Block Trade Plan (dài hạn) ──
    lines += [
        f"{'─'*34}",
        f"🎯 *QUYẾT ĐỊNH DÀI HẠN (Daily)*",
        f"{tp.get('decision_vi', '—')}",
        f"_{tp.get('decision_note', '')}_",
        f"",
        f"➡️ {tp.get('action_detail', '')}",
        f"",
    ]

    # Trạng thái nắm giữ
    lines += [
        f"📌 *NẮM GIỮ:* {tp.get('hold_status', '')}",
        f"  _{tp.get('hold_reason', '')}_",
        f"",
    ]

    # DCA zones
    if decision in ["MUA_MANH", "TICH_LUY", "NAM_GIU", "THEO_DOI"]:
        lines += [
            f"💰 *VÙNG DCA:*",
            f"  🟢 Tầng 1 (EMA20): *{tp.get('dca_zone1', '—')}*",
            f"  🟡 Tầng 2 (EMA50): *{tp.get('dca_zone2', '—')}*",
            f"  🔵 Tầng 3 (BB Low): *{tp.get('dca_zone3', '—')}*",
            f"  📍 {tp.get('dca_current_zone', '')}",
            f"  _{tp.get('dist_note', '')}_",
            f"",
        ]

    # Hỗ trợ & kháng cự
    lines += [
        f"📐 S1 *{fmt_price(tp.get('support_1'))}* S2 *{fmt_price(tp.get('support_2'))}* S3 *{fmt_price(tp.get('support_3'))}* | R *{fmt_price(tp.get('resistance'))}*",
        f"",
    ]

    # Tín hiệu thoát (chỉ hiện khi có cảnh báo)
    if reversal >= 1:
        lines += [
            f"⚠️ *THOÁT ({reversal}/5 tín hiệu):*",
        ]
        for sig_name, status in tp.get("reversal_detail", {}).items():
            if "XẤU" in status:
                lines.append(f"  {status}  {sig_name}")
        lines.append("")

    # ── Block kỹ thuật daily ──
    lines += [
        f"{'━'*34}",
        f"📈 *KỸ THUẬT DAILY: {score}/100* `{bar}`",
        f"",
        f"C1 EMA+MACD+Vol   [{d['combo1']['score']:3d}%] {d['combo1']['signal']}",
        f"C2 VWAP+OBV+BB    [{d['combo2']['score']:3d}%] {d['combo2']['signal']}",
        f"C3 ST+StochRSI    [{d['combo3']['score']:3d}%] {d['combo3']['signal']}",
        f"C4 MACD X+RSI+MFI [{d['combo4']['score']:3d}%] {d['combo4']['signal']}",
        f"C5 Trend Strength [{d['combo5']['score']:3d}%] {d['combo5']['signal']}",
        f"",
        f"RSI {d['rsi14']:.0f} | MACD {d['macd']:+.3f} | ST: *{d['supertrend']}*",
        f"EMA9/20/50: {d['ema9']:.1f}/{d['ema20']:.1f}/{d['ema50']:.1f}",
        f"OBV: *{d['obv_trend']}* | CMF: {d['cmf20']:+.3f} | MFI: {d['mfi14']:.0f}",
        f"Dòng tiền: *{tp.get('money_flow','—')}* | Trend: *{tp.get('trend_quality','—')}*",
    ]

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
    tp  = d.get("trade_plan", {})
    mtf = d.get("mtf", {})
    cf  = d.get("confluence", {})

    reversal_lines = "\n".join(
        f"  {status}: {sig}" for sig, status in tp.get("reversal_detail", {}).items()
    )
    cf_lines = "\n".join(
        f"  {v}" for v in cf.get("signals", {}).values()
    )
    prompt = (
        f"Phân tích dài hạn {symbol} ngày {d['date']}:\n"
        f"- Giá: {fmt_price(d['price'])} | Thay đổi: {d['change_pct']:+.1f}%\n\n"
        f"[MTF SCORE: {mtf.get('mtf_total',0)}/100]\n"
        f"- Quyết định MTF: {mtf.get('decision_vi','')}\n"
        f"- Monthly {mtf.get('monthly_score',0)}: {mtf.get('monthly_note','')}\n"
        f"- Weekly  {mtf.get('weekly_score',0)}: {mtf.get('weekly_note','')}\n"
        f"- Daily   {d['total_score']}: {tp.get('decision_vi','')}\n"
        f"- DCA cho phép: {mtf.get('dca_note','')}\n"
        f"{('- ⚠️ ' + mtf.get('monthly_warning','')) if mtf.get('monthly_warning') else ''}\n\n"
        f"[CONFLUENCE: {cf.get('score',0)}/40 — {cf.get('level','')}]\n"
        f"{cf_lines}\n\n"
        f"[KỸ THUẬT DAILY]\n"
        f"- ST: {d['supertrend']} | EMA9/20/50: {d['ema9']:.1f}/{d['ema20']:.1f}/{d['ema50']:.1f}\n"
        f"- RSI: {d['rsi14']:.1f} | MACD: {d['macd']:+.3f} | OBV: {d['obv_trend']} | CMF: {d['cmf20']:+.3f}\n"
        f"- Vùng DCA: {tp.get('dca_zone1','')} | {tp.get('dca_zone2','')} | {tp.get('dca_zone3','')}\n"
        f"- Tín hiệu thoát ({tp.get('reversal_count',0)}/5):\n{reversal_lines}\n\n"
        f"Phân tích 4-5 câu theo góc nhìn dài hạn: xác nhận quyết định MTF, "
        f"đánh giá confluence hiện tại có đủ mạnh để DCA không, "
        f"và 1 rủi ro dài hạn quan trọng nhất cần theo dõi."
    )
    return ask_claude(prompt)


# ── Handlers ──
def handle_help(chat_id):
    msg = (
        "🤖 *Stock Bot — Hướng dẫn sử dụng*\n"
        "─────────────────────\n"
        "💬 *Chat tự do với mã CK:*\n"
        "  `PVT` hoặc `giá FPT` hoặc `phân tích MBB`\n"
        "  → Tự động phân tích 5 Combo chỉ báo\n\n"
        "📌 *Lệnh có sẵn:*\n"
        "  `/vnindex` — VN-Index hiện tại\n"
        "  `/sectors` — Dòng tiền 7 ngành\n"
        "  `/macro`   — Vàng XAUUSD\n"
        "  `/news`    — Tin tức chứng khoán\n\n"
        "🔍 *5 Combo được tính tự động:*\n"
        "  C1: EMA Stack + MACD + Volume\n"
        "  C2: VWAP + OBV + Bollinger Bands\n"
        "  C3: SuperTrend + Stoch RSI + CMF\n"
        "  C4: MACD Cross + RSI + MFI\n"
        "  C5: Multi-TF Trend Strength\n\n"
        "⚡ Score ≥70 = Mua mạnh | 55-69 = Mua | <45 = Tránh"
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
    msg = (
        f"{arrow} *VN-Index*\n"
        f"Điểm: *{d['close']:,.2f}*\n"
        f"Thay đổi: *{sign}{d.get('change_pct', 0):.2f}%* ({sign}{d.get('change',0):.2f} điểm)\n"
        f"Open: {d.get('open',0):,.2f}\n"
        f"Ngày: {d.get('date','')}"
    )
    send_message(chat_id, msg)

def handle_sectors(chat_id):
    send_typing(chat_id)
    d = api_get("/sectors", timeout=60)
    if "error" in d:
        send_message(chat_id, f"❌ {d['error']}")
        return
    lines = ["📊 *Dòng tiền ngành hôm nay:*\n"]
    for s in d.get("sectors", []):
        sig_emoji = {"DONG TIEN VAO": "🟢", "DONG TIEN RA": "🔴"}.get(s["signal"], "⚪")
        pct = s['avg_change_pct']
        sign = "+" if pct >= 0 else ""
        lines.append(f"{sig_emoji} *{s['sector']}*: {sign}{pct:.1f}%")
    lines.append(f"\n_Cập nhật: {d.get('updated_at','')}_")
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

    # Nếu score >= 45, thêm Claude insight
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

# ── Main dispatcher ──
def process_message(msg):
    chat_id = str(msg["chat"]["id"])
    text    = msg.get("text", "").strip()

    if not text:
        return

    log.info(f"MSG [{chat_id}]: {text[:80]}")
    text_lower = text.lower()
    text_upper = text.upper()

    # ── Lệnh cụ thể ──
    if text_lower in ["/start", "/help", "help", "giup", "huong dan"]:
        handle_help(chat_id)
        return

    if text_lower == "/vnindex" or text_lower == "vnindex":
        handle_vnindex(chat_id)
        return

    if text_lower in ["/sectors", "sectors", "nganh", "dong tien nganh"]:
        handle_sectors(chat_id)
        return

    if text_lower.startswith("/macro") or text_lower == "macro":
        send_typing(chat_id)
        d = api_get("/macro")
        if "XAUUSD" in d:
            send_message(chat_id, f"🥇 Vàng XAUUSD: *${d['XAUUSD']['price']:,.2f}*")
        else:
            send_message(chat_id, "❌ Không lấy được macro data")
        return

    if text_lower.startswith("/news") or text_lower == "news":
        send_typing(chat_id)
        d = api_get("/news")
        if "news" in d:
            lines = ["📰 *Tin tức mới nhất:*\n"]
            for a in d["news"]:
                lines.append(f"• {a['title']}\n  _{a['source']} | {a['published']}_")
            send_message(chat_id, "\n".join(lines))
        else:
            send_message(chat_id, "❌ Không lấy được tin tức")
        return

    # ── Auto-detect mã cổ phiếu ──
    # Trường hợp 1: Nhắn đúng mã (1-2 từ viết hoa)
    words = text.split()
    if 1 <= len(words) <= 2:
        candidate = words[0].upper()
        if re.match(r'^[A-Z]{2,4}[0-9]?[A-Z]?$', candidate):
            if candidate in KNOWN_SYMBOLS or len(candidate) in [2,3]:
                if candidate not in {"NO", "OK", "BN", "TK", "TP", "SL", "GD", "KQ"}:
                    handle_stock_combo(chat_id, candidate)
                    return

    # Trường hợp 2: Extract từ câu dài
    symbol = extract_symbol(text)
    if symbol and symbol not in {"VN", "OK", "NO", "THE"}:
        # Double-check: không phải lệnh thông thường
        if not any(text_lower.startswith(cmd) for cmd in ["/phan", "/khuyen", "khuyen nghi"]):
            handle_stock_combo(chat_id, symbol)
            return

    # Trường hợp 3: /phan tich hoặc phan tich [SYMBOL]
    phan_match = re.search(r'(?:phan tich|phân tích|analyze|check)\s+([A-Z]{2,4})', text_upper)
    if phan_match:
        handle_stock_combo(chat_id, phan_match.group(1))
        return

    # Trường hợp 4: khuyen nghi / khuyến nghị
    if any(kw in text_lower for kw in ["khuyen nghi", "khuyến nghị", "nen mua gi", "nên mua gì", "co phieu tot"]):
        send_typing(chat_id)
        prompt = (
            f"Câu hỏi từ nhà đầu tư: {text}\n\n"
            "Hãy trả lời ngắn gọn và thực tế về thị trường chứng khoán Việt Nam hiện tại, "
            "đề xuất 2-3 cổ phiếu đáng chú ý với lý do cụ thể."
        )
        response = ask_claude(prompt)
        send_message(chat_id, f"💡 *Gợi ý đầu tư:*\n\n{response}")
        return

    # Trường hợp 5: Chat tự do → Claude trả lời
    handle_free_chat(chat_id, text)

# ── Bot loop ──
def main():
    log.info("🤖 Stock Bot khởi động — Combo Engine v2")

    # Xóa webhook cũ
    try:
        r = requests.post(f"{TELEGRAM_API}/deleteWebhook", timeout=10)
        log.info(f"Webhook deleted: {r.json()}")
    except: pass

    send_message(CHAT_ID,
        "🤖 *Bot đã khởi động lại!*\n\n"
        "Chat bất kỳ mã CK để phân tích:\n"
        "`PVT` `FPT` `MBB` `TCB` ...\n\n"
        "Gõ /help để xem hướng dẫn đầy đủ"
    )

    offset = None
    while True:
        try:
            updates = get_updates(offset=offset, timeout=30)
            for upd in updates:
                offset = upd["update_id"] + 1
                msg = upd.get("message")
                if msg and "text" in msg:
                    try:
                        process_message(msg)
                    except Exception as e:
                        log.error(f"process_message error: {e}")
        except KeyboardInterrupt:
            log.info("Bot stopped")
            break
        except Exception as e:
            log.error(f"Main loop error: {e}")
            time.sleep(5)

if __name__ == "__main__":
    main()
