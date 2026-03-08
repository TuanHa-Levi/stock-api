#!/usr/bin/env python3
# ============================================================
# bot.py — Telegram Stock Bot với Combo Analysis Engine
# Tự động nhận diện mã cổ phiếu trong bất kỳ tin nhắn nào
# ============================================================
import os, re, time, logging, requests
import anthropic

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ── Config — đọc từ .env trên VPS, không hardcode ──
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID        = os.environ.get("CHAT_ID", "")
CLAUDE_API_KEY = os.environ.get("CLAUDE_API_KEY", "")
STOCK_API_URL  = os.environ.get("STOCK_API_URL", "http://localhost:5000")

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

KNOWN_SYMBOLS = {
    "VCB","BID","CTG","TCB","MBB","VPB","ACB","STB","MSB","HDB",
    "VIC","VHM","VRE","VNM","SAB","MSN","MWG","FPT","HPG","GAS",
    "PLX","PVD","PVS","PVT","BSR","DCM","DPM","DGC",
    "GMD","HAH","VSC","DVP","PNJ","REE","CMG","VGI","ELC",
    "KBC","IDC","SZC","BCM","KDH","NVL","PDR","DXG","THD",
    "HHV","LCG","CII","VCI","HCM","SSI","VND","FTS","BSI",
    "VHM","VNM","GAS","SAB","CTD","HSG","NKG","TVS",
    "HAG","HNG","QNS","VCS","PPC","BWE","TDM","PHR",
    "TAL","PC1","EVF","VIB","LPB","OCB","BAB","NAB",
    "AAT","ANV","AST","BFC","BMP","BVH","CAV","CHP",
    "VNINDEX","VN30",
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
PRICE_WORDS = re.compile(r'(giá|phân tích|mua|bán|check|xem|tra|combo|signal|tín hiệu|xu hướng|kỹ thuật)', re.IGNORECASE)

def extract_symbol(text: str):
    text_upper = text.upper()
    words = re.findall(r'\b[A-Z0-9]+\b', text_upper)

    for w in words:
        if w in KNOWN_SYMBOLS and w not in {"VN30", "VNINDEX"}:
            return w

    STOPWORDS = {
        "BUY","SELL","RSI","EMA","SMA","OBV","CMF","MFI","ATR","BB",
        "OK","NO","YES","THE","AND","BUT","FOR","NOT","CAN","GET","SET",
        "BOT","API","VPS","TG","AI","ML","KBS","HNX","HOSE","VN",
        "TP","SL","RR","MA","KQ","NV","MUA","BAN","GIA","CO","CHO",
        "NEN","DU","KY","NAM","MOI","MACD","VWAP","ADX","USD","VND",
        "TANG","GIAM","CAO","THAP","LOG"
    }
    for w in words:
        if (2 <= len(w) <= 4 and w.isalpha() and w not in STOPWORDS):
            if PRICE_WORDS.search(text) or len(text.split()) <= 3:
                return w
    return None

# ── Format combo result ──
def format_combo_message(d: dict) -> str:
    sym     = d["symbol"]
    price   = d["price"]
    chg     = d["change"]
    chg_pct = d["change_pct"]
    date    = d["date"]
    emoji   = d["emoji"]
    action  = d["action"]
    score   = d["total_score"]
    overall = d["overall"]

    chg_arrow = "📈" if chg >= 0 else "📉"
    chg_sign  = "+" if chg >= 0 else ""
    filled = int(score / 10)
    bar = "█" * filled + "░" * (10 - filled)

    lines = [
        f"{'─'*32}",
        f"📊 *{sym}* — Combo Analysis",
        f"{'─'*32}",
        f"{chg_arrow} Giá: *{price:,.0f}đ* ({chg_sign}{chg_pct:.1f}%) | {date}",
        f"📦 Vol: {d['volume']/1e6:.1f}M ({d['vol_ratio']:.1f}x TB)",
        f"",
        f"━━━ ĐIỂM TỔNG HỢP ━━━",
        f"{emoji} *{overall}* — Hành động: *{action}*",
        f"Score: `{bar}` *{score}/100*",
        f"",
        f"━━━ 5 COMBO CHỈ BÁO ━━━",
        f"C1 EMA+MACD+Vol   [{d['combo1']['score']:3d}%] {d['combo1']['signal']}",
        f"C2 VWAP+OBV+BB    [{d['combo2']['score']:3d}%] {d['combo2']['signal']}",
        f"C3 ST+StochRSI    [{d['combo3']['score']:3d}%] {d['combo3']['signal']}",
        f"C4 MACD X+RSI+MFI [{d['combo4']['score']:3d}%] {d['combo4']['signal']}",
        f"C5 Trend Strength [{d['combo5']['score']:3d}%] {d['combo5']['signal']}",
        f"",
        f"━━━ CHỈ BÁO KEY ━━━",
        f"RSI(14):    *{d['rsi14']:.1f}* {'⚠️OB' if d['rsi14']>70 else ('💚OS' if d['rsi14']<30 else '✅')}",
        f"MACD:       {d['macd']:+.3f} | Signal: {d['macd_signal']:.3f}",
        f"  └ {d['macd_cross']}",
        f"EMA:   9={d['ema9']:,.0f}  20={d['ema20']:,.0f}  50={d['ema50']:,.0f}",
        f"SuperTrend: *{d['supertrend']}* @ {d['st_level']:,.0f}",
        f"OBV Trend:  *{d['obv_trend']}*{'  📌DIV' if d['obv_div'] else ''}",
        f"CMF(20):    {d['cmf20']:+.3f} {'✅' if d['cmf20']>0 else '❌'}",
        f"MFI(14):    {d['mfi14']:.1f}",
        f"Stoch RSI:  K={d['stoch_rsi_k']:.0f}",
        f"BB:  U={d['bb_upper']:,.0f} | M={d['bb_mid']:,.0f} | L={d['bb_lower']:,.0f}",
        f"  └ Vị trí: *{d['bb_pos']}*",
        f"ATR(14): {d['atr14']:,.0f}đ  |  Đà 5 phiên: {d['mom5d_pct']:+.1f}%",
        f"",
        f"━━━ KẾ HOẠCH GIAO DỊCH ━━━",
    ]

    if score >= 45:
        lines += [
            f"🎯 Entry:  *{d['entry']:,.0f}đ*",
            f"🛑 Stop:   *{d['sl']:,.0f}đ* ({d['sl_pct']:+.1f}%)",
            f"✅ TP1:    *{d['tp1']:,.0f}đ* ({d['tp1_pct']:+.1f}%)",
            f"✅ TP2:    *{d['tp2']:,.0f}đ* ({d['tp2_pct']:+.1f}%)",
            f"⚖️ R:R =   1 : {d['rr_ratio']}",
        ]
    else:
        lines += [
            f"⚠️ Score thấp — *không khuyến nghị* vào lệnh",
            f"   Đợi tín hiệu rõ hơn hoặc xem ngành khác",
        ]

    lines += [
        f"",
        f"⚡ *Lưu ý:* Tham khảo thêm. Không phải tư vấn đầu tư.",
    ]
    return "\n".join(lines)

# ── Claude AI ──
def ask_claude(prompt: str) -> str:
    try:
        client = anthropic.Anthropic(api_key=CLAUDE_API_KEY)
        msg = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1000,
            system=(
                "Bạn là chuyên gia phân tích chứng khoán Việt Nam với 15 năm kinh nghiệm. "
                "Trả lời ngắn gọn, súc tích, thực tế. Dùng tiếng Việt. "
                "Khi phân tích cổ phiếu, luôn đề cập: xu hướng, dòng tiền, rủi ro, hành động cụ thể."
            ),
            messages=[{"role": "user", "content": prompt}]
        )
        return msg.content[0].text
    except Exception as e:
        return f"❌ Claude API lỗi: {str(e)[:100]}"

def claude_with_combo(symbol: str, combo_data: dict) -> str:
    summary = (
        f"Cổ phiếu {symbol}, giá {combo_data['price']:,.0f}đ, "
        f"thay đổi {combo_data['change_pct']:+.1f}%, "
        f"RSI={combo_data['rsi14']:.1f}, MACD={combo_data['macd']:+.3f}, "
        f"SuperTrend={combo_data['supertrend']}, OBV={combo_data['obv_trend']}, "
        f"CMF={combo_data['cmf20']:+.3f}, Score={combo_data['total_score']}/100, "
        f"Tín hiệu={combo_data['overall']}."
    )
    prompt = (
        f"Dữ liệu kỹ thuật {symbol}: {summary}\n\n"
        f"Phân tích ngắn (5-7 câu): "
        f"1) Xu hướng hiện tại, "
        f"2) Dòng tiền đang làm gì, "
        f"3) Rủi ro chính, "
        f"4) Hành động nên làm với lý do cụ thể."
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
        f"Thay đổi: *{sign}{d.get('change_pct',0):.2f}%* ({sign}{d.get('change',0):.2f} điểm)\n"
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
        pct  = s['avg_change_pct']
        sign = "+" if pct >= 0 else ""
        lines.append(f"{sig_emoji} *{s['sector']}*: {sign}{pct:.1f}%")
    lines.append(f"\n_Cập nhật: {d.get('updated_at','')}_")
    send_message(chat_id, "\n".join(lines))

def handle_stock_combo(chat_id, symbol: str):
    send_typing(chat_id)
    send_message(chat_id, f"⏳ Đang phân tích *{symbol}* với 5 Combo chỉ báo...\n_(mất 5-10 giây)_")

    data = api_get(f"/combo/{symbol}", timeout=45)
    if "error" in data:
        fallback = api_get(f"/stock/{symbol}", timeout=20)
        if "error" in fallback:
            send_message(chat_id, f"❌ Không tìm thấy mã *{symbol}*. Vui lòng kiểm tra lại.")
            return
        send_message(chat_id,
            f"⚠️ *{symbol}* — Dữ liệu cơ bản\n"
            f"Giá: *{fallback.get('close',0):,.0f}đ* ({fallback.get('change_pct',0):+.1f}%)\n"
            f"RSI(14): {fallback.get('rsi_14',50):.1f}\n"
            f"_(Không đủ dữ liệu lịch sử để chạy Combo Analysis)_"
        )
        return

    send_message(chat_id, format_combo_message(data))

    if data.get("total_score", 0) >= 40:
        time.sleep(1)
        send_typing(chat_id)
        send_message(chat_id, f"🧠 *Claude AI nhận định:*\n\n{claude_with_combo(symbol, data)}")

def handle_free_chat(chat_id, text: str):
    send_typing(chat_id)
    send_message(chat_id, ask_claude(text))

# ── Main dispatcher ──
def process_message(msg):
    chat_id    = str(msg["chat"]["id"])
    text       = msg.get("text", "").strip()
    if not text:
        return

    log.info(f"MSG [{chat_id}]: {text[:80]}")
    text_lower = text.lower()
    text_upper = text.upper()

    if text_lower in ["/start", "/help", "help"]:
        handle_help(chat_id); return

    if text_lower in ["/vnindex", "vnindex"]:
        handle_vnindex(chat_id); return

    if text_lower in ["/sectors", "sectors", "nganh"]:
        handle_sectors(chat_id); return

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

    # Auto-detect mã 1-2 từ
    words = text.split()
    if 1 <= len(words) <= 2:
        candidate = words[0].upper()
        if re.match(r'^[A-Z]{2,4}$', candidate):
            if candidate in KNOWN_SYMBOLS or len(candidate) == 3:
                if candidate not in {"NO","OK","BN","TK","TP","SL","GD","KQ","VN"}:
                    handle_stock_combo(chat_id, candidate); return

    # Extract từ câu dài
    symbol = extract_symbol(text)
    if symbol and symbol not in {"VN","OK","NO"}:
        if not any(text_lower.startswith(k) for k in ["/phan","khuyen"]):
            handle_stock_combo(chat_id, symbol); return

    # /phan tich SYMBOL
    m = re.search(r'(?:phan tich|phân tích|analyze|check)\s+([A-Z]{2,4})', text_upper)
    if m:
        handle_stock_combo(chat_id, m.group(1)); return

    # Khuyến nghị
    if any(k in text_lower for k in ["khuyen nghi","khuyến nghị","nen mua gi","nên mua gì"]):
        send_typing(chat_id)
        send_message(chat_id, f"💡 *Gợi ý đầu tư:*\n\n{ask_claude(text)}")
        return

    # Chat tự do
    handle_free_chat(chat_id, text)

# ── Bot loop ──
def main():
    log.info("🤖 Stock Bot khởi động — Combo Engine v2")

    if not TELEGRAM_TOKEN:
        log.error("❌ TELEGRAM_TOKEN chưa set trong .env"); return
    if not CLAUDE_API_KEY:
        log.error("❌ CLAUDE_API_KEY chưa set trong .env"); return

    try:
        requests.post(f"{TELEGRAM_API}/deleteWebhook", timeout=10)
    except: pass

    send_message(CHAT_ID,
        "🤖 *Bot đã khởi động lại!*\n\n"
        "Chat bất kỳ mã CK để phân tích:\n"
        "`PVT` `FPT` `MBB` `TCB` ...\n\n"
        "Gõ /help để xem hướng dẫn"
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
            break
        except Exception as e:
            log.error(f"Main loop error: {e}")
            time.sleep(5)

if __name__ == "__main__":
    main()
