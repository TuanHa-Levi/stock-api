# ============================================================
# portfolio.py — v5: Danh mục đầu tư (Multi-watchlist)
# Hỗ trợ: nhiều danh mục / user, giá vốn, khối lượng
# Lưu trữ: /root/stock-api/data/portfolios.json
# ============================================================
import os, json, time, logging, requests, uuid

log = logging.getLogger(__name__)

STOCK_API_URL  = os.environ.get("STOCK_API_URL", "http://localhost:5000")
DATA_DIR       = os.path.join(os.path.dirname(__file__), "data")
PORTFOLIO_FILE = os.path.join(DATA_DIR, "portfolios.json")
MAX_SYMBOLS    = 20
MAX_WATCHLISTS = 5


# ══════════════════════════════════════════════════════════════
# STORAGE
# ══════════════════════════════════════════════════════════════

def _ensure_data_dir():
    os.makedirs(DATA_DIR, exist_ok=True)

def _load() -> dict:
    _ensure_data_dir()
    if not os.path.exists(PORTFOLIO_FILE):
        return {}
    try:
        with open(PORTFOLIO_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _save(data: dict):
    _ensure_data_dir()
    with open(PORTFOLIO_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def _get_user(data: dict, uid: str) -> dict:
    """Lấy hoặc tạo mới user record"""
    if uid not in data:
        wl_id = f"wl_{uid}_1"
        data[uid] = {
            "watchlists": {
                wl_id: {
                    "name": "Danh mục chính",
                    "created_at": time.strftime("%Y-%m-%d"),
                    "symbols": {}
                }
            },
            "active_wl": wl_id
        }
    return data[uid]

def _get_active_wl(user: dict) -> tuple[str, dict]:
    """Trả về (wl_id, watchlist_dict) đang active"""
    wl_id = user.get("active_wl")
    wls   = user.get("watchlists", {})
    if wl_id and wl_id in wls:
        return wl_id, wls[wl_id]
    # fallback: lấy cái đầu tiên
    if wls:
        first = next(iter(wls))
        user["active_wl"] = first
        return first, wls[first]
    return None, None


# ══════════════════════════════════════════════════════════════
# WATCHLIST MANAGEMENT
# ══════════════════════════════════════════════════════════════

def get_watchlists(uid: str) -> dict:
    """Trả về dict {wl_id: wl_info} của user"""
    data = _load()
    user = _get_user(data, str(uid))
    return user.get("watchlists", {})

def get_active_wl_id(uid: str) -> str:
    data = _load()
    user = _get_user(data, str(uid))
    return user.get("active_wl")

def set_active_wl(uid: str, wl_id: str) -> bool:
    data = _load()
    user = _get_user(data, str(uid))
    if wl_id in user["watchlists"]:
        user["active_wl"] = wl_id
        _save(data)
        return True
    return False

def create_watchlist(uid: str, name: str) -> tuple[bool, str]:
    """Tạo danh mục mới. Trả về (success, wl_id hoặc error_msg)"""
    data = _load()
    user = _get_user(data, str(uid))
    wls  = user["watchlists"]
    if len(wls) >= MAX_WATCHLISTS:
        return False, f"Tối đa {MAX_WATCHLISTS} danh mục"
    wl_id = f"wl_{uid}_{int(time.time())}"
    wls[wl_id] = {
        "name": name.strip()[:30],
        "created_at": time.strftime("%Y-%m-%d"),
        "symbols": {}
    }
    user["active_wl"] = wl_id
    _save(data)
    return True, wl_id

def delete_watchlist(uid: str, wl_id: str) -> tuple[bool, str]:
    """Xóa danh mục. Không xóa nếu chỉ còn 1."""
    data = _load()
    user = _get_user(data, str(uid))
    wls  = user["watchlists"]
    if len(wls) <= 1:
        return False, "Không thể xóa danh mục duy nhất"
    if wl_id not in wls:
        return False, "Không tìm thấy danh mục"
    name = wls[wl_id]["name"]
    del wls[wl_id]
    # Chuyển active sang cái còn lại
    if user.get("active_wl") == wl_id:
        user["active_wl"] = next(iter(wls))
    _save(data)
    return True, name

def rename_watchlist(uid: str, wl_id: str, new_name: str) -> bool:
    data = _load()
    user = _get_user(data, str(uid))
    if wl_id not in user["watchlists"]:
        return False
    user["watchlists"][wl_id]["name"] = new_name.strip()[:30]
    _save(data)
    return True


# ══════════════════════════════════════════════════════════════
# SYMBOL MANAGEMENT
# ══════════════════════════════════════════════════════════════

def get_symbols(uid: str) -> dict:
    """Trả về {sym: {cost_price, qty, added_at}} của active watchlist"""
    data = _load()
    user = _get_user(data, str(uid))
    _, wl = _get_active_wl(user)
    if not wl:
        return {}
    return wl.get("symbols", {})

def add_symbol(uid: str, sym: str, cost_price: float = 0, qty: int = 0) -> tuple[bool, str]:
    """Thêm hoặc update mã. Trả về (success, message)"""
    data = _load()
    user = _get_user(data, str(uid))
    wl_id, wl = _get_active_wl(user)
    if not wl:
        return False, "Không có danh mục nào"
    syms = wl.setdefault("symbols", {})
    if sym in syms:
        # Update existing
        syms[sym]["cost_price"] = cost_price
        syms[sym]["qty"]        = qty
        syms[sym]["updated_at"] = time.strftime("%Y-%m-%d %H:%M")
        _save(data)
        return True, "updated"
    if len(syms) >= MAX_SYMBOLS:
        return False, f"Danh mục đã đầy ({MAX_SYMBOLS} mã)"
    syms[sym] = {
        "cost_price": cost_price,
        "qty":        qty,
        "added_at":   time.strftime("%Y-%m-%d")
    }
    _save(data)
    return True, "added"

def remove_symbol(uid: str, sym: str) -> bool:
    data = _load()
    user = _get_user(data, str(uid))
    _, wl = _get_active_wl(user)
    if not wl:
        return False
    if sym in wl.get("symbols", {}):
        del wl["symbols"][sym]
        _save(data)
        return True
    return False


# ══════════════════════════════════════════════════════════════
# DISPLAY HELPERS
# ══════════════════════════════════════════════════════════════

def fmt_price(p):
    if not p or p == 0:
        return "—"
    if p < 1000:
        p = p * 1000
    return f"{p:,.0f}đ"

def format_watchlist_summary(uid: str) -> str:
    """Hiển thị danh mục active với giá vốn, P&L nếu có"""
    data  = _load()
    user  = _get_user(data, str(uid))
    wl_id, wl = _get_active_wl(user)
    if not wl:
        return "📋 Chưa có danh mục nào."

    syms  = wl.get("symbols", {})
    name  = wl["name"]
    lines = [f"📁 *{name}* ({len(syms)}/{MAX_SYMBOLS} mã)\n"]

    if not syms:
        lines.append("_(Danh mục trống — thêm mã bằng nút bên dưới)_")
        return "\n".join(lines)

    for sym, info in syms.items():
        cp  = info.get("cost_price", 0)
        qty = info.get("qty", 0)
        if cp and cp > 0 and qty and qty > 0:
            cost_display = f"  💰 Giá vốn: {fmt_price(cp)} | KL: {qty:,} CP"
        elif cp and cp > 0:
            cost_display = f"  💰 Giá vốn: {fmt_price(cp)}"
        elif qty and qty > 0:
            cost_display = f"  📦 KL: {qty:,} CP"
        else:
            cost_display = ""
        lines.append(f"• *{sym}*{cost_display}")

    # Tóm tắt nhiều watchlists
    all_wls = user["watchlists"]
    if len(all_wls) > 1:
        lines.append(f"\n_({len(all_wls)} danh mục — đang xem: {name})_")

    return "\n".join(lines)


def format_analysis_result(uid: str) -> str:
    """Phân tích nhanh từng mã trong danh mục active"""
    syms = get_symbols(uid)
    if not syms:
        return "📋 Danh mục trống."

    results = []
    for sym in syms:
        try:
            d = requests.get(f"{STOCK_API_URL}/combo/{sym}", timeout=30).json()
            if "error" in d:
                results.append(f"• *{sym}*: ❌ Lỗi data")
                continue

            master   = d.get("trade_plan", {}).get("decision", "THEO_DOI_CHAT")
            mtf      = d.get("mtf", {}).get("mtf_total", 0)
            cf       = d.get("confluence", {}).get("score", 0)
            kelly    = d.get("kelly_size", {}).get("tier", "NONE")
            price    = d.get("close", 0)
            chg_pct  = d.get("change_pct", 0)

            MASTER_EMOJI = {
                "DCA_NGAY":      "💚",
                "NAM_GIU":       "🟢",
                "NAM_GIU_CHO":   "🔵",
                "THEO_DOI_CHAT": "🟡",
                "KHONG_DCA":     "🟠",
                "CANH_BAO":      "🔴",
                "CANH_THOAT":    "🔴",
                "THOAT_LENH":    "⛔",
            }
            KELLY_EMOJI = {"STRONG": "💰💰", "NORMAL": "💰", "WEAK": "⚠️", "NONE": ""}
            me = MASTER_EMOJI.get(master, "⚪")
            ke = KELLY_EMOJI.get(kelly, "")

            # P&L nếu có giá vốn
            info = syms[sym]
            cp   = info.get("cost_price", 0)
            qty  = info.get("qty", 0)
            pl_line = ""
            if cp and cp > 0 and price > 0:
                cp_norm = cp if cp > 1000 else cp * 1000
                pr_norm = price if price > 1000 else price * 1000
                pl_pct  = (pr_norm - cp_norm) / cp_norm * 100
                pl_sign = "+" if pl_pct >= 0 else ""
                pl_line = f" | P&L: *{pl_sign}{pl_pct:.1f}%*"
                if qty and qty > 0:
                    pl_vnd = (pr_norm - cp_norm) * qty
                    pl_line += f" ({pl_sign}{pl_vnd/1e6:.1f}M)"

            sign = "+" if chg_pct >= 0 else ""
            results.append(
                f"{me} *{sym}* {ke}\n"
                f"   Giá: {fmt_price(price)} ({sign}{chg_pct:.1f}%){pl_line}\n"
                f"   MTF: {mtf} | CF: {cf}/45 | {master}"
            )
        except Exception as e:
            results.append(f"• *{sym}*: ❌ {str(e)[:40]}")

    return "🔍 *Phân tích danh mục:*\n\n" + "\n\n".join(results)
