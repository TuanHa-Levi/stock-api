# ============================================================
# router.py — Hybrid Intent Router
# Bước 1: Keyword matching (nhanh, không tốn API)
# Bước 2: Claude classify nếu keyword không match
# Bước 3: Hỏi lại user nếu vẫn không chắc
# ============================================================
import re, os, json, logging
import anthropic

log = logging.getLogger(__name__)

CLAUDE_API_KEY = os.environ.get("CLAUDE_API_KEY", "")

# ══════════════════════════════════════════════════════════════
# INTENT REGISTRY — mỗi MVP đăng ký intent của mình ở đây
# Khi thêm MVP mới, chỉ cần thêm vào dict này
# ══════════════════════════════════════════════════════════════
INTENT_REGISTRY = {

    # ── MVP1: Trading Analysis ──
    "trading.analyze": {
        "module":      "app",
        "handler":     "handle_stock_combo",
        "description": "Phân tích kỹ thuật 1 mã cổ phiếu (combo, MTF, DCA...)",
        "keywords": [
            "phân tích", "phan tich", "analyze", "check", "xem",
            "combo", "kỹ thuật", "ky thuat", "mua không", "có nên mua",
            "nên mua", "đánh giá", "danh gia",
        ],
        "symbol_required": True,
    },
    "trading.price": {
        "module":      "app",
        "handler":     "handle_stock_combo",
        "description": "Hỏi giá hoặc thông tin nhanh 1 mã",
        "keywords": [
            "giá", "gia", "price", "bao nhiêu", "bao nhieu",
            "hôm nay", "hom nay", "đang ở", "dang o",
        ],
        "symbol_required": True,
    },

    # ── MVP2: Portfolio / Watchlist ──
    "portfolio.add": {
        "module":      "portfolio",
        "handler":     "handle_add",
        "description": "Thêm mã cổ phiếu vào danh mục theo dõi",
        "keywords": [
            "thêm", "them", "add", "bỏ vào", "bo vao",
            "theo dõi", "theo doi", "watchlist", "danh mục",
        ],
        "symbol_required": True,
    },
    "portfolio.remove": {
        "module":      "portfolio",
        "handler":     "handle_remove",
        "description": "Xóa mã cổ phiếu khỏi danh mục theo dõi",
        "keywords": [
            "xóa", "xoa", "remove", "bỏ", "bo", "delete",
            "không theo dõi nữa", "loại", "loai",
        ],
        "symbol_required": True,
    },
    "portfolio.list": {
        "module":      "portfolio",
        "handler":     "handle_list",
        "description": "Xem danh sách mã trong danh mục",
        "keywords": [
            "danh mục của tôi", "danh muc cua toi",
            "watchlist của tôi", "xem danh mục", "list",
            "đang theo dõi", "dang theo doi", "danh sách",
        ],
        "symbol_required": False,
    },
    "portfolio.analyze_all": {
        "module":      "portfolio",
        "handler":     "handle_analyze_all",
        "description": "Phân tích toàn bộ danh mục đang theo dõi",
        "keywords": [
            "phân tích danh mục", "scan danh mục", "scan watchlist",
            "phân tích tất cả", "quét danh mục", "quet danh muc",
            "danh mục hôm nay", "check danh mục",
        ],
        "symbol_required": False,
    },

    # ── MVP3: Alert (placeholder — chưa implement) ──
    "alert.set": {
        "module":      "alert",
        "handler":     "handle_set_alert",
        "description": "Đặt cảnh báo giá hoặc lịch nhắc",
        "keywords": [
            "cảnh báo", "canh bao", "alert", "nhắc", "nhac",
            "thông báo khi", "thong bao khi",
        ],
        "symbol_required": False,
        "coming_soon": True,
    },
}

# ── Mã CK hợp lệ pattern ──
SYMBOL_PATTERN = re.compile(r'\b([A-Z]{2,4}[0-9]?)\b')
NOISE_WORDS = {
    "OK","NO","VN","TK","TP","SL","GD","KQ","BN","THE","AN","MY",
    "TV","AI","API","VIP","MVP","DCA","MTF","RSI","EMA","ATR",
}


def extract_symbols(text: str) -> list[str]:
    """Tìm tất cả mã CK tiềm năng trong text"""
    text_upper = text.upper()
    matches = SYMBOL_PATTERN.findall(text_upper)
    return [m for m in matches if m not in NOISE_WORDS and len(m) >= 2]


def keyword_match(text: str) -> dict | None:
    """
    Bước 1 — Keyword matching
    Returns intent dict nếu match, None nếu không
    """
    text_lower = text.lower().strip()
    text_upper = text.upper().strip()

    # ── Special: mã đơn độc (1-3 từ viết hoa) → trading.analyze ──
    words = text.split()
    if 1 <= len(words) <= 2:
        candidate = words[0].upper()
        if re.match(r'^[A-Z]{2,4}[0-9]?$', candidate) and candidate not in NOISE_WORDS:
            return {
                "intent":     "trading.analyze",
                "module":     "app",
                "handler":    "handle_stock_combo",
                "symbols":    [candidate],
                "confidence": "high",
                "method":     "symbol_only",
            }

    # ── Keyword scan theo độ ưu tiên ──
    # Ưu tiên portfolio trước trading (vì "thêm HPG" chứa cả "HPG" lẫn "thêm")
    priority_order = [
        "portfolio.add", "portfolio.remove",
        "portfolio.list", "portfolio.analyze_all",
        "trading.analyze", "trading.price",
        "alert.set",
    ]

    for intent_key in priority_order:
        intent_def = INTENT_REGISTRY[intent_key]
        for kw in intent_def["keywords"]:
            if kw in text_lower:
                symbols = extract_symbols(text)
                # Nếu cần symbol nhưng không tìm thấy → không match
                if intent_def.get("symbol_required") and not symbols:
                    continue
                return {
                    "intent":     intent_key,
                    "module":     intent_def["module"],
                    "handler":    intent_def["handler"],
                    "symbols":    symbols,
                    "confidence": "high",
                    "method":     f"keyword:{kw}",
                    "coming_soon": intent_def.get("coming_soon", False),
                }

    # ── Fallback: có symbol nhưng không có keyword rõ → trading.analyze ──
    symbols = extract_symbols(text)
    if symbols and len(text.split()) <= 5:
        return {
            "intent":     "trading.analyze",
            "module":     "app",
            "handler":    "handle_stock_combo",
            "symbols":    symbols,
            "confidence": "medium",
            "method":     "symbol_fallback",
        }

    return None


def claude_classify(text: str) -> dict | None:
    """
    Bước 2 — Claude phân loại nếu keyword không match
    Returns intent dict hoặc None nếu cần hỏi lại
    """
    if not CLAUDE_API_KEY:
        return None

    # Tóm tắt intent registry cho Claude
    intent_list = "\n".join([
        f"- {k}: {v['description']}"
        for k, v in INTENT_REGISTRY.items()
        if not v.get("coming_soon")
    ])

    try:
        client = anthropic.Anthropic(api_key=CLAUDE_API_KEY)
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",  # dùng Haiku để tiết kiệm
            max_tokens=200,
            system=(
                "Bạn là intent classifier cho Telegram bot chứng khoán Việt Nam. "
                "Phân loại tin nhắn vào đúng intent. "
                "Trả về JSON duy nhất, không giải thích.\n"
                "Format: {\"intent\": \"key\", \"symbols\": [\"VNM\"], \"confidence\": \"high/medium/low\"}\n"
                "Nếu không chắc: {\"intent\": \"unclear\", \"symbols\": [], \"confidence\": \"low\"}"
            ),
            messages=[{
                "role": "user",
                "content": (
                    f"Tin nhắn: \"{text}\"\n\n"
                    f"Các intent có sẵn:\n{intent_list}\n\n"
                    "Phân loại intent:"
                )
            }]
        )
        raw = msg.content[0].text.strip()
        # Strip markdown nếu có
        raw = re.sub(r'```json|```', '', raw).strip()
        result = json.loads(raw)

        intent_key = result.get("intent", "unclear")
        if intent_key == "unclear" or intent_key not in INTENT_REGISTRY:
            return None

        intent_def = INTENT_REGISTRY[intent_key]
        return {
            "intent":      intent_key,
            "module":      intent_def["module"],
            "handler":     intent_def["handler"],
            "symbols":     result.get("symbols", []),
            "confidence":  result.get("confidence", "medium"),
            "method":      "claude_classify",
            "coming_soon": intent_def.get("coming_soon", False),
        }

    except Exception as e:
        log.warning(f"Claude classify error: {e}")
        return None


def route(text: str) -> dict:
    """
    Main routing function — gọi từ bot.py
    Returns:
        {
          "intent":     "portfolio.add",
          "module":     "portfolio",
          "handler":    "handle_add",
          "symbols":    ["HPG", "VNM"],
          "confidence": "high",
          "method":     "keyword:thêm",
          "action":     "execute" | "clarify" | "coming_soon" | "unknown",
          "clarify_msg": "..." (nếu action = clarify)
        }
    """
    text = text.strip()
    if not text:
        return {"action": "unknown", "intent": "none", "symbols": []}

    # Bước 1: Keyword
    result = keyword_match(text)

    # Bước 2: Claude nếu không match
    if result is None:
        log.info(f"Keyword miss → Claude classify: '{text[:50]}'")
        result = claude_classify(text)

    # Bước 3: Không xác định được
    if result is None:
        return {
            "action":      "unknown",
            "intent":      "none",
            "symbols":     [],
            "clarify_msg": None,
        }

    # Coming soon
    if result.get("coming_soon"):
        return {**result, "action": "coming_soon"}

    # Confidence thấp → hỏi lại
    if result.get("confidence") == "low":
        intent_def = INTENT_REGISTRY.get(result["intent"], {})
        return {
            **result,
            "action":      "clarify",
            "clarify_msg": _build_clarify_msg(text, result),
        }

    return {**result, "action": "execute"}


def _build_clarify_msg(text: str, result: dict) -> str:
    """Tạo tin nhắn hỏi lại user"""
    intent = result.get("intent", "")
    symbols = result.get("symbols", [])
    sym_str = ", ".join(symbols) if symbols else "mã chưa xác định"

    clarify_map = {
        "portfolio.add":    f"Bạn muốn *thêm {sym_str}* vào danh mục theo dõi?",
        "portfolio.remove": f"Bạn muốn *xóa {sym_str}* khỏi danh mục?",
        "trading.analyze":  f"Bạn muốn *phân tích {sym_str}*?",
        "portfolio.list":   "Bạn muốn *xem danh mục* đang theo dõi?",
        "portfolio.analyze_all": "Bạn muốn *phân tích toàn bộ danh mục*?",
    }
    base = clarify_map.get(intent, f"Bạn muốn thực hiện: _{intent}_?")
    return f"🤔 {base}\n\nTrả lời *có/yes* để xác nhận hoặc mô tả lại yêu cầu."
