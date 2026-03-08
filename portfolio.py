# ============================================================
# portfolio.py — MVP2: Danh mục theo dõi (Watchlist)
# Lưu trữ: /root/stock-api/data/portfolios.json
# Mỗi user_id có danh mục riêng
# ============================================================
import os, json, time, logging, requests

log = logging.getLogger(__name__)

STOCK_API_URL  = os.environ.get("STOCK_API_URL", "http://localhost:5000")
DATA_DIR       = os.path.join(os.path.dirname(__file__), "data")
PORTFOLIO_FILE = os.path.join(DATA_DIR, "portfolios.json")
MAX_SYMBOLS    = 20  # Tối đa mã mỗi user


# ══════════════════════════════════════════════════════════════
# STORAGE — Đọc/ghi portfolios.json
# ══════════════════════════════════════════════════════════════

def _ensure_data_dir():
    os.makedirs(DATA_DIR, exist_ok=True)


def _load() -> dict:
    """Load toàn bộ portfolios từ file JSON"""
    _ensure_data_dir()
    if not os.path.exists(PORTFOLIO_FILE):
        return {}
    try:
        with open(PORTFOLIO_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save(data: dict):
    """Ghi portfolios ra file JSON"""
    _ensure_data_dir()
    with open(PORTFOLIO_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_portfolio(user_id: str) -> list[str]:
    """Lấy danh mục của 1 user"""
    data = _load()
    return data.get(str(user_id), {}).get("symbols", [])


def _set_portfolio(user_id: str, symbols: list[str], name: str = "Danh mục của tôi"):
    """Lưu danh mục của 1 user"""
    data = _load()
    uid  = str(user_id)
    if uid not in data:
        data[uid] = {"name": name, "symbols": [], "created_at": time.strftime("%Y-%m-%d")}
    data[uid]["symbols"]    = symbols
    data[uid]["updated_at"] = time.strftime("%Y-%m-%d %H:%M")
    _save(data)


# ══════════════════════════════════════════════════════════════
# HANDLERS — Gọi từ bot.py
# ══════════════════════════════════════════════════════════════

def handle_add(user_id: str, symbols: list[str], send_fn) -> None:
    """Thêm 1 hoặc nhiều mã vào danh mục"""
    if not symbols:
        send_fn("❓ Bạn muốn thêm mã nào? VD: _thêm HPG FPT VNM_")
        return

    current = get_portfolio(user_id)
    added   = []
    existed = []
    skipped = []

    for sym in symbols:
        sym = sym.upper().strip()
        if sym in current:
            existed.append(sym)
        elif len(current) + len(added) >= MAX_SYMBOLS:
            skipped.append(sym)
        else:
            added.append(sym)
            current.append(sym)

    if added:
        _set_portfolio(user_id, current)

    # Build response
    lines = []
    if added:
        lines.append(f"✅ Đã thêm: *{', '.join(added)}*")
    if existed:
        lines.append(f"ℹ️ Đã có trong danh mục: {', '.join(existed)}")
    if skipped:
        lines.append(f"⚠️ Danh mục đầy ({MAX_SYMBOLS} mã), bỏ qua: {', '.join(skipped)}")

    lines.append(f"\n📋 Danh mục hiện tại ({len(current)} mã): *{' | '.join(current)}*")
    send_fn("\n".join(lines))


def handle_remove(user_id: str, symbols: list[str], send_fn) -> None:
    """Xóa 1 hoặc nhiều mã khỏi danh mục"""
    if not symbols:
        send_fn("❓ Bạn muốn xóa mã nào? VD: _xóa HPG_")
        return

    current = get_portfolio(user_id)
    removed = []
    not_found = []

    for sym in symbols:
        sym = sym.upper().strip()
        if sym in current:
            current.remove(sym)
            removed.append(sym)
        else:
            not_found.append(sym)

    if removed:
        _set_portfolio(user_id, current)

    lines = []
    if removed:
        lines.append(f"🗑️ Đã xóa: *{', '.join(removed)}*")
    if not_found:
        lines.append(f"ℹ️ Không có trong danh mục: {', '.join(not_found)}")

    if current:
        lines.append(f"\n📋 Còn lại ({len(current)} mã): *{' | '.join(current)}*")
    else:
        lines.append("\n📋 Danh mục hiện đang trống.")
    send_fn("\n".join(lines))


def handle_list(user_id: str, symbols: list, send_fn) -> None:
    """Hiển thị danh mục hiện tại"""
    current = get_portfolio(user_id)

    if not current:
        send_fn(
            "📋 *Danh mục của bạn đang trống.*\n\n"
            "Thêm mã bằng cách nhắn: _thêm HPG FPT VNM_"
        )
        return

    data = _load()
    info = data.get(str(user_id), {})
    updated = info.get("updated_at", "—")

    lines = [
        f"📋 *Danh mục của bạn* ({len(current)}/{MAX_SYMBOLS} mã)",
        f"_Cập nhật: {updated}_\n",
    ]
    for i, sym in enumerate(current, 1):
        lines.append(f"  {i}. *{sym}*")

    lines += [
        f"",
        f"💡 Lệnh nhanh:",
        f"  • _phân tích danh mục_ — quét tất cả",
        f"  • _thêm [MÃ]_ — thêm mã mới",
        f"  • _xóa [MÃ]_ — xóa mã",
    ]
    send_fn("\n".join(lines))


def handle_analyze_all(user_id: str, symbols: list, send_fn) -> None:
    """Phân tích toàn bộ mã trong danh mục"""
    current = get_portfolio(user_id)

    if not current:
        send_fn(
            "📋 Danh mục trống — chưa có gì để phân tích.\n"
            "Thêm mã: _thêm HPG FPT VNM_"
        )
        return

    total = len(current)
    send_fn(
        f"⏳ Đang quét *{total} mã* trong danh mục...\n"
        f"_{' | '.join(current)}_\n"
        f"_(mỗi mã ~10 giây, tổng ~{total*10//60 + 1} phút)_"
    )

    results = []
    errors  = []

    for sym in current:
        try:
            r = requests.get(
                f"{STOCK_API_URL}/combo/{sym}",
                timeout=45
            )
            if r.status_code == 200:
                d = r.json()
                results.append((sym, d))
            else:
                errors.append(sym)
        except Exception as e:
            log.warning(f"Portfolio analyze {sym}: {e}")
            errors.append(sym)
        time.sleep(1)  # tránh spam API

    if not results:
        send_fn("❌ Không lấy được dữ liệu. Vui lòng thử lại.")
        return

    # ── Summary table ──
    # Sắp xếp theo MTF total score giảm dần
    results.sort(
        key=lambda x: x[1].get("mtf", {}).get("mtf_total", 0),
        reverse=True
    )

    lines = [f"📊 *Tổng quan danh mục* | {time.strftime('%d/%m %H:%M')}\n"]

    # Master key từ bot.py calc_master_decision
    BUY_EMOJIS    = {"DCA_NGAY": "🟢🟢", "MUA_MANH": "🟢🟢", "TICH_LUY": "🟢"}
    HOLD_EMOJIS   = {"NAM_GIU": "⚪", "NAM_GIU_CHO": "⚪", "THEO_DOI": "🟡", "THEO_DOI_CHAT": "🟡"}
    EXIT_EMOJIS   = {"KHONG_DCA": "🔴", "CANH_BAO": "🔴", "CANH_THOAT": "🟠", "THOAT_LENH": "🔴🔴"}

    for sym, d in results:
        tp         = d.get("trade_plan", {})
        mtf        = d.get("mtf", {})
        sdca       = d.get("smart_dca", {})
        decision   = tp.get("decision", "—")
        mtf_total  = mtf.get("mtf_total", 0)
        price      = d.get("price", 0)
        chg_pct    = d.get("change_pct", 0)
        regime     = sdca.get("regime", "—")
        reversal   = tp.get("reversal_count", 0)
        best_zone  = sdca.get("best_zone_str", "—")

        # Emoji quyết định
        emoji = (
            BUY_EMOJIS.get(decision) or
            HOLD_EMOJIS.get(decision) or
            EXIT_EMOJIS.get(decision) or "⚫"
        )

        chg_sign = "+" if chg_pct >= 0 else ""
        price_fmt = f"{price*1000:,.0f}đ" if price < 1000 else f"{price:,.0f}đ"

        cf        = d.get("confluence", {})
        cf_score  = cf.get("score", 0)
        ks        = d.get("kelly_size", {})
        kelly     = ks.get("tier", "NONE")
        kelly_map = {"STRONG": "💰💰", "NORMAL": "💰", "WEAK": "⚠️", "NONE": ""}
        kelly_str = kelly_map.get(kelly, "")
        rev_str   = f" ⚠️{reversal}/5" if reversal >= 2 else ""

        line = (
            f"{emoji} *{sym}* {price_fmt} ({chg_sign}{chg_pct:.1f}%) {kelly_str}\n"
            f"   MTF {mtf_total} | CF {cf_score}/45 | {regime}{rev_str}\n"
            f"   DCA: _{best_zone}_"
        )
        lines.append(line)

    if errors:
        lines.append(f"\n⚠️ Lỗi lấy data: {', '.join(errors)}")

    # Thống kê nhanh
    # Dùng master key từ alert.py logic
    def _master(d):
        tp = d.get("trade_plan", {}); mtf = d.get("mtf", {}); cf = d.get("confluence", {})
        rev = tp.get("reversal_count", 0); mtf_t = mtf.get("mtf_total", 0)
        cf_s = cf.get("score", 0); m_s = mtf.get("monthly_score", 0)
        dec = tp.get("decision", "")
        if dec == "THOAT_LENH" or rev >= 3: return "THOAT_LENH"
        if m_s < 20: return "KHONG_DCA"
        if mtf_t >= 60 and cf_s >= 20 and rev <= 1: return "DCA_NGAY"
        if rev == 2 and mtf_t >= 55: return "THEO_DOI_CHAT"
        if mtf_t < 50: return "CANH_BAO"
        return "NAM_GIU"

    buy_count  = sum(1 for _, d in results if _master(d) in BUY_EMOJIS)
    exit_count = sum(1 for _, d in results if _master(d) in EXIT_EMOJIS)
    kelly_strong = sum(1 for _, d in results if d.get("kelly_size",{}).get("tier") == "STRONG")
    lines.insert(1,
        f"🟢 DCA/Mua: {buy_count} | ⚪ Giữ: {total-buy_count-exit_count} | "
        f"🔴 Cảnh báo: {exit_count}"
        + (f" | 💰 Kelly Strong: {kelly_strong}" if kelly_strong else "") + "\n"
    )

    send_fn("\n".join(lines))
