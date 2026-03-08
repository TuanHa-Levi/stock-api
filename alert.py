#!/usr/bin/env python3
# ============================================================
# alert.py — Auto Alert Module v4.0 (MVP3)
# Quét watchlist, phát hiện tín hiệu mới, gửi Telegram
#
# Chạy:
#   python alert.py                  ← daemon, 12:00 & 20:00 T2-T6
#   python alert.py --once           ← chạy 1 lần ngay
#   python alert.py --force-init     ← chỉ cập nhật state, không gửi alert
#   python alert.py --symbol HPG     ← debug 1 mã
#   python alert.py --manual         ← gọi từ Telegram /alert command
# ============================================================
import os, json, time, logging, requests, argparse
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("/root/stock-api/data/alert.log", encoding="utf-8"),
    ]
)
log = logging.getLogger(__name__)

# ── Config ──
TELEGRAM_TOKEN  = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID         = os.environ.get("CHAT_ID", "")
STOCK_API_URL   = os.environ.get("STOCK_API_URL", "http://localhost:5000")
DATA_DIR        = os.environ.get("DATA_DIR", "/root/stock-api/data")
PORTFOLIO_FILE  = os.path.join(DATA_DIR, "portfolios.json")
ALERT_STATE_FILE= os.path.join(DATA_DIR, "alert_state.json")

# Mã mặc định nếu watchlist trống
DEFAULT_WATCHLIST = ["HPG","FPT","VCB","TCB","MBB","PVT","GAS","GMD","DCM","VHM"]

# ── Telegram helpers ──
def send_telegram(msg, chat_id=None):
    cid = chat_id or CHAT_ID
    if not TELEGRAM_TOKEN or not cid:
        log.warning("Thiếu TELEGRAM_TOKEN hoặc CHAT_ID — không gửi được")
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={
                "chat_id":    cid,
                "text":       msg,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            },
            timeout=15,
        )
        return r.status_code == 200
    except Exception as e:
        log.error(f"Telegram error: {e}")
        return False


# ── State I/O ──
def load_state():
    os.makedirs(DATA_DIR, exist_ok=True)
    try:
        with open(ALERT_STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(ALERT_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# ── Watchlist ──
def load_watchlist():
    """Lấy tất cả symbols từ tất cả portfolios của tất cả users."""
    try:
        with open(PORTFOLIO_FILE, encoding="utf-8") as f:
            data = json.load(f)
        symbols = set()
        for user_data in data.values():
            if isinstance(user_data, dict):
                for portfolio in user_data.values():
                    if isinstance(portfolio, dict):
                        for sym in portfolio.get("symbols", []):
                            symbols.add(sym.upper().strip())
        if symbols:
            return sorted(symbols)
    except Exception as e:
        log.warning(f"Không đọc được portfolios.json: {e}")
    return DEFAULT_WATCHLIST


# ── API call ──
def get_combo(symbol, timeout=45):
    try:
        r = requests.get(f"{STOCK_API_URL}/combo/{symbol}", timeout=timeout)
        data = r.json()
        if "error" not in data:
            return data
    except Exception as e:
        log.warning(f"get_combo({symbol}) error: {e}")
    return None


# ── Master Decision ──
def get_master_key(mtf, tp, cf):
    """Lấy key quyết định master để track state thay đổi."""
    reversal      = tp.get("reversal_count", 0)
    mtf_total     = mtf.get("mtf_total", 0)
    cf_score      = cf.get("score", 0)
    monthly_score = mtf.get("monthly_score", 0)
    daily_dec     = tp.get("decision", "")

    if daily_dec == "THOAT_LENH" or reversal >= 3:
        return "THOAT_LENH"
    if monthly_score < 20:
        return "KHONG_DCA"
    if mtf_total >= 60 and cf_score >= 20 and reversal <= 1:
        return "DCA_NGAY"
    if mtf_total >= 60 and cf_score < 20 and reversal <= 1:
        return "NAM_GIU_CHO"
    if reversal == 2 and mtf_total >= 55:
        return "THEO_DOI_CHAT"
    if mtf_total < 50:
        return "CANH_BAO"
    return "NAM_GIU"


MASTER_LABEL = {
    "THOAT_LENH":    "🔴🔴 THOÁT LỆNH",
    "KHONG_DCA":     "🔴 KHÔNG DCA",
    "DCA_NGAY":      "🟢 DCA NGAY",
    "NAM_GIU_CHO":   "⚪ NẮM GIỮ — Chờ CF",
    "THEO_DOI_CHAT": "🟡 THEO DÕI CHẶT",
    "CANH_BAO":      "🔴 CẢNH BÁO",
    "NAM_GIU":       "⚪ NẮM GIỮ",
}

# Priority của master key (thấp hơn = cần alert gấp hơn)
MASTER_PRIORITY = {
    "THOAT_LENH": 0, "KHONG_DCA": 1, "CANH_BAO": 2,
    "THEO_DOI_CHAT": 3, "NAM_GIU_CHO": 4,
    "NAM_GIU": 5, "DCA_NGAY": 5,
}


# ── Detect alerts ──
def detect_alerts(symbol, current, prev):
    """So sánh state hiện tại vs trước → list alerts có mức độ ưu tiên."""
    alerts = []
    if not prev:
        return alerts  # Lần đầu: chỉ lưu state, không alert

    mtf  = current.get("mtf", {})
    tp   = current.get("trade_plan", {})
    cf   = current.get("confluence", {})
    ks   = current.get("kelly_size", {})

    curr_key      = get_master_key(mtf, tp, cf)
    curr_reversal = tp.get("reversal_count", 0)
    curr_score    = current.get("total_score", 0)
    curr_mtf      = mtf.get("mtf_total", 0)
    curr_cf       = cf.get("score", 0)
    curr_kelly    = ks.get("tier", "NONE")

    prev_key      = prev.get("master_key")
    prev_reversal = prev.get("reversal_count", 0)
    prev_score    = prev.get("score", 0)
    prev_mtf      = prev.get("mtf_total", 0)
    prev_cf       = prev.get("cf_score", 0)

    # ── R1: THOÁT LỆNH (ưu tiên cao nhất) ──
    if curr_key == "THOAT_LENH" and prev_key != "THOAT_LENH":
        alerts.append({
            "priority": "CRITICAL", "type": "EXIT",
            "msg": f"🔴🔴 *THOÁT LỆNH* — {curr_reversal}/5 tín hiệu đảo chiều xác nhận",
        })

    # ── R2: Reversal count tăng đến ngưỡng nguy hiểm ──
    elif curr_reversal >= 3 and prev_reversal < 3:
        alerts.append({
            "priority": "CRITICAL", "type": "REVERSAL_3",
            "msg": f"🔴 *{curr_reversal}/5 tín hiệu đảo chiều* — sẵn sàng thoát",
        })
    elif curr_reversal == 2 and prev_reversal < 2:
        alerts.append({
            "priority": "HIGH", "type": "REVERSAL_2",
            "msg": f"⚠️ *2/5 tín hiệu xấu* — theo dõi chặt, chưa phải thoát",
        })

    # ── R3: Master Decision thay đổi (không tính THOAT đã xử lý ở R1) ──
    if prev_key and curr_key != prev_key and curr_key != "THOAT_LENH":
        prev_lbl = MASTER_LABEL.get(prev_key, prev_key)
        curr_lbl = MASTER_LABEL.get(curr_key, curr_key)
        # Chỉ alert nếu thay đổi thực sự quan trọng
        prev_prio = MASTER_PRIORITY.get(prev_key, 5)
        curr_prio = MASTER_PRIORITY.get(curr_key, 5)
        if abs(prev_prio - curr_prio) >= 2:
            alerts.append({
                "priority": "HIGH" if curr_prio < prev_prio else "MEDIUM",
                "type": "DECISION_CHANGE",
                "msg": f"📊 Quyết định: {prev_lbl} → *{curr_lbl}*",
            })

    # ── R4: Score / MTF drop mạnh ──
    if prev_score > 0 and (prev_score - curr_score) > 15:
        alerts.append({
            "priority": "MEDIUM", "type": "SCORE_DROP",
            "msg": f"📉 Score giảm mạnh: {prev_score} → *{curr_score}* (−{prev_score - curr_score}đ)",
        })
    if prev_mtf > 0 and (prev_mtf - curr_mtf) > 12:
        alerts.append({
            "priority": "MEDIUM", "type": "MTF_DROP",
            "msg": f"📉 MTF giảm: {prev_mtf} → *{curr_mtf}* (−{prev_mtf - curr_mtf}đ)",
        })

    # ── R5: Tín hiệu DCA mới xuất hiện (score tăng đột biến) ──
    if curr_key == "DCA_NGAY" and prev_key not in ("DCA_NGAY",):
        alerts.append({
            "priority": "MEDIUM", "type": "DCA_SIGNAL",
            "msg": f"🟢 *DCA NGAY* xuất hiện — MTF:{curr_mtf}/100 CF:{curr_cf}/45",
        })

    # ── R6: Kelly tier cải thiện lên STRONG ──
    prev_kelly = prev.get("kelly_tier", "NONE")
    if curr_kelly == "STRONG" and prev_kelly != "STRONG":
        alerts.append({
            "priority": "MEDIUM", "type": "KELLY_STRONG",
            "msg": f"💰 Kelly STRONG — đủ điều kiện vào mạnh ({ks.get('pct_per_dca','?')} / lần)",
        })

    return alerts


# ── Format message ──
def fmt_price(p):
    if p is None: return "—"
    return f"{p*1000:,.0f}đ" if p < 1000 else f"{p:,.0f}đ"


def format_alert_msg(symbol, current, alerts):
    price    = current.get("price", 0)
    chg_pct  = current.get("change_pct", 0)
    score    = current.get("total_score", 0)
    mtf      = current.get("mtf", {})
    cf       = current.get("confluence", {})
    tp       = current.get("trade_plan", {})
    ks       = current.get("kelly_size", {})

    mtf_total = mtf.get("mtf_total", 0)
    cf_score  = cf.get("score", 0)
    reversal  = tp.get("reversal_count", 0)
    kelly_str = ks.get("tier", "NONE")

    price_str = fmt_price(price)
    sign  = "+" if chg_pct >= 0 else ""
    arrow = "📈" if chg_pct >= 0 else "📉"

    has_critical = any(a["priority"] == "CRITICAL" for a in alerts)
    has_high     = any(a["priority"] == "HIGH"     for a in alerts)
    header_emoji = "🚨" if has_critical else ("⚠️" if has_high else "📊")

    lines = [
        f"{header_emoji} *ALERT: {symbol}* — {price_str} {arrow}{sign}{chg_pct:.1f}%",
        f"Score: *{score}*/100 | MTF: *{mtf_total}*/100 | CF: {cf_score}/45 | Rev: {reversal}/5 | Kelly: {kelly_str}",
        "───────────────────",
    ]
    for a in alerts:
        lines.append(f"• {a['msg']}")

    # DCA zone nếu là tín hiệu mua
    dca_zone = tp.get("dca_best_zone")
    if dca_zone and any(a["type"] in ("DCA_SIGNAL","KELLY_STRONG") for a in alerts):
        lines.append(f"\n📍 Vùng DCA: `{dca_zone}`")

    lines.append(f"\n_Quét: {datetime.now().strftime('%d/%m/%Y %H:%M')}_")
    return "\n".join(lines)


# ── Main scan ──
def run_scan(symbols=None, force_init=False, chat_id=None):
    if symbols is None:
        symbols = load_watchlist()
    if not symbols:
        log.info("Watchlist trống")
        return 0

    state     = load_state()
    new_state = dict(state)
    all_alerts = []

    log.info(f"Quét {len(symbols)} mã: {', '.join(symbols)}")

    for symbol in symbols:
        try:
            current = get_combo(symbol)
            if not current:
                log.warning(f"Không có data cho {symbol}")
                continue

            mtf = current.get("mtf", {})
            tp  = current.get("trade_plan", {})
            cf  = current.get("confluence", {})
            ks  = current.get("kelly_size", {})

            # Lưu state mới
            new_state[symbol] = {
                "master_key":   get_master_key(mtf, tp, cf),
                "reversal_count": tp.get("reversal_count", 0),
                "score":        current.get("total_score", 0),
                "mtf_total":    mtf.get("mtf_total", 0),
                "cf_score":     cf.get("score", 0),
                "kelly_tier":   ks.get("tier", "NONE"),
                "price":        current.get("price", 0),
                "updated_at":   datetime.now().isoformat(),
            }

            if force_init:
                continue  # Không alert, chỉ cập nhật state

            alerts = detect_alerts(symbol, current, state.get(symbol))
            if alerts:
                msg = format_alert_msg(symbol, current, alerts)
                all_alerts.append((symbol, msg, alerts, current))
                log.info(f"Alert {symbol}: {[a['type'] for a in alerts]}")

            time.sleep(1.5)  # Rate limit

        except Exception as e:
            log.error(f"Lỗi quét {symbol}: {e}")

    # Gửi Telegram
    if all_alerts and not force_init:
        crit_count = sum(1 for _, _, a, _ in all_alerts
                         if any(x["priority"] == "CRITICAL" for x in a))
        header = (
            f"📡 *ALERT SCAN — {datetime.now().strftime('%d/%m/%Y %H:%M')}*\n"
            f"{len(all_alerts)} mã có tín hiệu"
            + (f" | 🚨 {crit_count} CRITICAL" if crit_count else "")
            + ":"
        )
        send_telegram(header, chat_id=chat_id)
        time.sleep(0.5)
        for symbol, msg, alerts, current in all_alerts:
            send_telegram(msg, chat_id=chat_id)
            time.sleep(0.5)
    elif force_init:
        log.info(f"Force-init xong: cập nhật state cho {len(new_state)} mã")
    else:
        log.info("Không có tín hiệu mới — không gửi Telegram")

    save_state(new_state)
    return len(all_alerts)


# ── Summary scan (gửi tóm tắt toàn bộ danh mục) ──
def run_summary(symbols=None, chat_id=None):
    """Gửi bản tóm tắt toàn bộ watchlist — không phụ thuộc state thay đổi."""
    if symbols is None:
        symbols = load_watchlist()
    if not symbols:
        send_telegram("📭 Watchlist trống — thêm mã bằng lệnh `thêm [MÃ]`", chat_id=chat_id)
        return

    results = []
    send_telegram(
        f"⏳ Đang quét *{len(symbols)} mã*...\n"
        f"_{', '.join(symbols)}_",
        chat_id=chat_id
    )

    for symbol in symbols:
        try:
            current = get_combo(symbol)
            if not current:
                continue
            mtf  = current.get("mtf", {})
            tp   = current.get("trade_plan", {})
            cf   = current.get("confluence", {})
            ks   = current.get("kelly_size", {})
            results.append({
                "symbol":   symbol,
                "price":    current.get("price", 0),
                "chg_pct":  current.get("change_pct", 0),
                "score":    current.get("total_score", 0),
                "mtf":      mtf.get("mtf_total", 0),
                "cf":       cf.get("score", 0),
                "reversal": tp.get("reversal_count", 0),
                "master":   get_master_key(mtf, tp, cf),
                "kelly":    ks.get("tier", "NONE"),
            })
            time.sleep(1)
        except Exception as e:
            log.warning(f"Summary error {symbol}: {e}")

    if not results:
        send_telegram("❌ Không lấy được data — thử lại sau", chat_id=chat_id)
        return

    # Phân loại
    buy_now  = [r for r in results if r["master"] == "DCA_NGAY"]
    hold_ok  = [r for r in results if r["master"] in ("NAM_GIU", "NAM_GIU_CHO")]
    watch_r  = [r for r in results if r["master"] in ("THEO_DOI_CHAT", "CANH_BAO", "KHONG_DCA")]
    exit_r   = [r for r in results if r["master"] == "THOAT_LENH"]

    lines = [
        f"📊 *TÓM TẮT DANH MỤC — {datetime.now().strftime('%d/%m %H:%M')}*",
        f"Tổng: {len(results)} mã | "
        f"DCA: {len(buy_now)} | Giữ: {len(hold_ok)} | "
        f"Cảnh báo: {len(watch_r)} | Thoát: {len(exit_r)}",
        "━━━━━━━━━━━━━━━━━━━━",
    ]

    EMOJI_MAP = {
        "THOAT_LENH": "🔴🔴", "KHONG_DCA": "🔴", "CANH_BAO": "🔴",
        "THEO_DOI_CHAT": "🟡", "NAM_GIU_CHO": "⚪",
        "NAM_GIU": "⚪", "DCA_NGAY": "🟢",
    }

    # Sort: exit first, then watch, then hold, then buy
    order_key = lambda r: MASTER_PRIORITY.get(r["master"], 5)
    results_sorted = sorted(results, key=order_key)

    for r in results_sorted:
        em      = EMOJI_MAP.get(r["master"], "⚪")
        p_str   = fmt_price(r["price"])
        sign    = "+" if r["chg_pct"] >= 0 else ""
        rev_str = f" ⚠️{r['reversal']}/5" if r["reversal"] >= 2 else ""
        ks_str  = f" 💰{r['kelly']}" if r["kelly"] == "STRONG" else ""
        lines.append(
            f"{em} *{r['symbol']}* {p_str} ({sign}{r['chg_pct']:.1f}%) "
            f"S:{r['score']} MTF:{r['mtf']} CF:{r['cf']}{rev_str}{ks_str}"
        )

    lines.append(f"\n_Kelly STRONG: vào mạnh | ⚠️N: N/5 tín hiệu thoát_")
    send_telegram("\n".join(lines), chat_id=chat_id)


# ── CLI ──
def main():
    parser = argparse.ArgumentParser(description="Stock Alert Module v4.0")
    parser.add_argument("--once",        action="store_true", help="Chạy 1 lần rồi thoát")
    parser.add_argument("--force-init",  action="store_true", help="Init state không gửi alert")
    parser.add_argument("--summary",     action="store_true", help="Gửi tóm tắt toàn danh mục")
    parser.add_argument("--manual",      action="store_true", help="Manual scan (từ Telegram)")
    parser.add_argument("--symbol",      help="Debug 1 mã cụ thể")
    parser.add_argument("--chat-id",     help="Chat ID Telegram cụ thể")
    args = parser.parse_args()

    chat_id = args.chat_id or CHAT_ID

    if args.symbol:
        # Debug 1 mã
        sym  = args.symbol.upper()
        data = get_combo(sym)
        if not data:
            print(f"Không lấy được data cho {sym}")
            return
        state   = load_state()
        alerts  = detect_alerts(sym, data, state.get(sym))
        if alerts:
            msg = format_alert_msg(sym, data, alerts)
            print(msg)
            if args.manual:
                send_telegram(msg, chat_id=chat_id)
        else:
            print(f"Không có alert mới cho {sym}")
            mtf = data.get("mtf",{})
            tp  = data.get("trade_plan",{})
            cf  = data.get("confluence",{})
            print(f"State hiện tại: {get_master_key(mtf, tp, cf)} | "
                  f"Score:{data.get('total_score')} MTF:{mtf.get('mtf_total')} "
                  f"CF:{cf.get('score')} Rev:{tp.get('reversal_count')}")
        return

    if args.summary:
        run_summary(chat_id=chat_id)
        return

    if args.once or args.manual:
        n = run_scan(force_init=args.force_init, chat_id=chat_id)
        log.info(f"Scan xong: {n} alert(s) gửi")
        return

    if args.force_init:
        run_scan(force_init=True)
        log.info("Force-init hoàn tất")
        return

    # ── Daemon mode: chạy 12:00 & 20:00 T2-T6 ──
    try:
        import schedule
    except ImportError:
        log.error("Cần cài: pip install schedule --break-system-packages")
        return

    def _job_scan():
        if datetime.now().weekday() < 5:  # T2(0)–T6(4)
            log.info("Chạy scheduled scan...")
            run_scan()
        else:
            log.info("Cuối tuần — bỏ qua scan")

    def _job_summary():
        if datetime.now().weekday() < 5:
            log.info("Chạy scheduled summary...")
            run_summary()

    schedule.every().day.at("09:30").do(_job_scan)    # Sau mở cửa
    schedule.every().day.at("12:00").do(_job_scan)    # Giữa phiên
    schedule.every().day.at("15:15").do(_job_scan)    # Sau đóng cửa
    schedule.every().day.at("20:00").do(_job_summary) # Tóm tắt buổi tối

    log.info("🔔 Alert daemon khởi động — lịch: 9:30 | 12:00 | 15:15 | 20:00 T2-T6")
    send_telegram(
        "🔔 *Alert Module v4.0 đã khởi động*\n"
        "Lịch quét: 9:30 | 12:00 | 15:15 | 20:00 (T2-T6)\n"
        "Chỉ gửi khi có tín hiệu mới."
    )

    while True:
        schedule.run_pending()
        time.sleep(20)


if __name__ == "__main__":
    main()
