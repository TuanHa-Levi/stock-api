# ============================================================
# app.py — Stock API với Full Combo Technical Analysis
# VPS Nhân Hòa | Source: KBS (hoạt động trên cloud)
# ============================================================
import ssl, os, urllib3
os.environ["PYTHONHTTPSVERIFY"] = "0"
ssl._create_default_https_context = ssl._create_unverified_context
urllib3.disable_warnings()

import requests as _req
_orig = _req.Session.request
def _noverify(self, *a, **kw):
    kw.setdefault("verify", False)
    return _orig(self, *a, **kw)
_req.Session.request = _noverify

from flask import Flask, jsonify
from vnstock import Quote
import requests
from datetime import datetime, timedelta
import math
import pandas as pd

app = Flask(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
    "Accept-Language": "vi-VN,vi;q=0.9",
}

# ══════════════════════════════════════════════════════════════
# HELPERS — Tính toán chỉ báo kỹ thuật
# ══════════════════════════════════════════════════════════════

def ema(data, period):
    """Exponential Moving Average"""
    if len(data) < period:
        return [sum(data) / len(data)] * len(data)
    k = 2 / (period + 1)
    result = [sum(data[:period]) / period]
    for price in data[period:]:
        result.append(price * k + result[-1] * (1 - k))
    # Pad đầu với None để align với data gốc
    return [None] * (period - 1) + result

def sma(data, period):
    result = []
    for i in range(len(data)):
        if i < period - 1:
            result.append(None)
        else:
            result.append(sum(data[i-period+1:i+1]) / period)
    return result

def calc_rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i-1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)

def calc_rsi_series(closes, period=14):
    """RSI cho toàn bộ series"""
    rsi_series = [None] * period
    if len(closes) <= period:
        return rsi_series
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i-1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    if avg_loss == 0:
        rsi_series.append(100.0)
    else:
        rsi_series.append(round(100 - (100 / (1 + avg_gain/avg_loss)), 2))
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period-1) + gains[i]) / period
        avg_loss = (avg_loss * (period-1) + losses[i]) / period
        if avg_loss == 0:
            rsi_series.append(100.0)
        else:
            rsi_series.append(round(100 - (100 / (1 + avg_gain/avg_loss)), 2))
    return rsi_series

def calc_macd(closes, fast=12, slow=26, signal=9):
    ema_fast = ema(closes, fast)
    ema_slow = ema(closes, slow)
    macd_line = []
    for i in range(len(closes)):
        f = ema_fast[i]
        s = ema_slow[i]
        if f is None or s is None:
            macd_line.append(None)
        else:
            macd_line.append(round(f - s, 4))
    valid_macd = [x for x in macd_line if x is not None]
    if len(valid_macd) < signal:
        return macd_line, [None]*len(macd_line), [None]*len(macd_line)
    signal_ema_vals = ema(valid_macd, signal)
    signal_line = [None] * (len(macd_line) - len(signal_ema_vals)) + signal_ema_vals
    histogram = []
    for i in range(len(macd_line)):
        m = macd_line[i]
        s = signal_line[i]
        if m is None or s is None:
            histogram.append(None)
        else:
            histogram.append(round(m - s, 4))
    return macd_line, signal_line, histogram

def calc_bollinger(closes, period=20, std_dev=2):
    result = []
    for i in range(len(closes)):
        if i < period - 1:
            result.append((None, None, None))
        else:
            window = closes[i-period+1:i+1]
            mid = sum(window) / period
            variance = sum((x - mid)**2 for x in window) / period
            std = math.sqrt(variance)
            result.append((round(mid, 2), round(mid + std_dev*std, 2), round(mid - std_dev*std, 2)))
    return result  # (middle, upper, lower)

def calc_atr(highs, lows, closes, period=14):
    true_ranges = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i-1]),
            abs(lows[i] - closes[i-1])
        )
        true_ranges.append(tr)
    if len(true_ranges) < period:
        return sum(true_ranges) / len(true_ranges) if true_ranges else 0
    atr = sum(true_ranges[:period]) / period
    for tr in true_ranges[period:]:
        atr = (atr * (period-1) + tr) / period
    return round(atr, 2)

def calc_obv(closes, volumes):
    obv = [0]
    for i in range(1, len(closes)):
        if closes[i] > closes[i-1]:
            obv.append(obv[-1] + volumes[i])
        elif closes[i] < closes[i-1]:
            obv.append(obv[-1] - volumes[i])
        else:
            obv.append(obv[-1])
    return obv

def calc_stoch_rsi(closes, rsi_period=14, stoch_period=14, k_period=3, d_period=3):
    rsi_s = calc_rsi_series(closes, rsi_period)
    valid_rsi = [x for x in rsi_s if x is not None]
    if len(valid_rsi) < stoch_period:
        return 50.0, 50.0
    window = valid_rsi[-stoch_period:]
    min_rsi = min(window)
    max_rsi = max(window)
    if max_rsi == min_rsi:
        stoch_rsi = 50.0
    else:
        stoch_rsi = (valid_rsi[-1] - min_rsi) / (max_rsi - min_rsi) * 100
    return round(stoch_rsi, 2), round(stoch_rsi, 2)  # K, D (simplified)

def calc_supertrend(highs, lows, closes, period=10, multiplier=3.0):
    """SuperTrend indicator"""
    n = len(closes)
    if n < period + 1:
        return "NEUTRAL", 0
    atr_vals = []
    for i in range(1, n):
        tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
        atr_vals.append(tr)
    # ATR smoothed
    atr_s = [sum(atr_vals[:period]) / period]
    for v in atr_vals[period:]:
        atr_s.append((atr_s[-1]*(period-1) + v) / period)
    # Basic upper/lower bands
    direction = 1  # 1=bullish, -1=bearish
    final_upper = []
    final_lower = []
    for i in range(len(atr_s)):
        idx = i + period
        if idx >= n:
            break
        hl2 = (highs[idx] + lows[idx]) / 2
        basic_upper = hl2 + multiplier * atr_s[i]
        basic_lower = hl2 - multiplier * atr_s[i]
        if i == 0:
            final_upper.append(basic_upper)
            final_lower.append(basic_lower)
        else:
            fu = basic_upper if basic_upper < final_upper[-1] or closes[idx-1] > final_upper[-1] else final_upper[-1]
            fl = basic_lower if basic_lower > final_lower[-1] or closes[idx-1] < final_lower[-1] else final_lower[-1]
            final_upper.append(fu)
            final_lower.append(fl)
    if not final_upper:
        return "NEUTRAL", 0
    # Determine direction
    last_close = closes[-1]
    last_upper = final_upper[-1]
    last_lower = final_lower[-1]
    if last_close > last_upper:
        direction = 1
    elif last_close < last_lower:
        direction = -1
    else:
        # Check prev
        if len(final_upper) > 1:
            prev_close = closes[-2] if len(closes) > 1 else last_close
            direction = 1 if prev_close > final_upper[-2] else -1
    st_value = last_lower if direction == 1 else last_upper
    return ("BULL" if direction == 1 else "BEAR"), round(st_value, 2)

def calc_cmf(highs, lows, closes, volumes, period=20):
    """Chaikin Money Flow"""
    n = len(closes)
    if n < period:
        return 0.0
    mfv_list = []
    for i in range(n):
        hl = highs[i] - lows[i]
        if hl == 0:
            mfv_list.append(0)
        else:
            clv = ((closes[i] - lows[i]) - (highs[i] - closes[i])) / hl
            mfv_list.append(clv * volumes[i])
    vol_window = volumes[-period:]
    mfv_window = mfv_list[-period:]
    total_vol = sum(vol_window)
    if total_vol == 0:
        return 0.0
    return round(sum(mfv_window) / total_vol, 4)

def calc_mfi(highs, lows, closes, volumes, period=14):
    """Money Flow Index"""
    n = len(closes)
    if n < period + 1:
        return 50.0
    typical = [(highs[i]+lows[i]+closes[i])/3 for i in range(n)]
    pos_mf, neg_mf = 0, 0
    for i in range(n-period, n):
        if i == 0:
            continue
        mf = typical[i] * volumes[i]
        if typical[i] >= typical[i-1]:
            pos_mf += mf
        else:
            neg_mf += mf
    if neg_mf == 0:
        return 100.0
    mfr = pos_mf / neg_mf
    return round(100 - (100 / (1 + mfr)), 2)

def get_stock_data(symbol, days=400):
    """Pull data từ KBS — source hoạt động trên mọi cloud server"""
    end = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    try:
        df = Quote(symbol=symbol, source='KBS').history(start=start, end=end, interval='1D')
        if df is None or df.empty:
            return None
        df = df.sort_values('time').reset_index(drop=True)
        return df
    except Exception as e:
        return None

# ══════════════════════════════════════════════════════════════
# SMART DCA ENGINE — Hybrid Regime + Confluence Zones
# ══════════════════════════════════════════════════════════════

def calc_adx(highs, lows, closes, period=14):
    """ADX — đo độ mạnh xu hướng (không phân biệt lên/xuống)
    ADX > 25 = trend mạnh | < 20 = sideways
    """
    n = len(closes)
    if n < period * 2:
        return 20.0
    plus_dm, minus_dm, tr_list = [], [], []
    for i in range(1, n):
        h_diff = highs[i] - highs[i-1]
        l_diff = lows[i-1] - lows[i]
        plus_dm.append(h_diff if h_diff > l_diff and h_diff > 0 else 0)
        minus_dm.append(l_diff if l_diff > h_diff and l_diff > 0 else 0)
        tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
        tr_list.append(tr)
    # Wilder smoothing
    def wilder_smooth(data, p):
        s = [sum(data[:p])]
        for v in data[p:]:
            s.append(s[-1] - s[-1]/p + v)
        return s
    tr_s    = wilder_smooth(tr_list, period)
    pdm_s   = wilder_smooth(plus_dm, period)
    mdm_s   = wilder_smooth(minus_dm, period)
    dx_vals = []
    for i in range(len(tr_s)):
        if tr_s[i] == 0:
            continue
        pdi = 100 * pdm_s[i] / tr_s[i]
        mdi = 100 * mdm_s[i] / tr_s[i]
        dx  = 100 * abs(pdi - mdi) / (pdi + mdi) if (pdi + mdi) != 0 else 0
        dx_vals.append(dx)
    if not dx_vals:
        return 20.0
    # ADX = smoothed DX
    adx_vals = [sum(dx_vals[:period]) / period]
    for v in dx_vals[period:]:
        adx_vals.append((adx_vals[-1] * (period-1) + v) / period)
    return round(adx_vals[-1], 1)

def calc_vwap_rolling(closes, highs, lows, volumes, period=20):
    """VWAP rolling N ngày — giá trung bình theo khối lượng"""
    n = len(closes)
    if n < period:
        typical = [(highs[i]+lows[i]+closes[i])/3 for i in range(n)]
        vol_total = sum(volumes)
        return sum(t*v for t,v in zip(typical, volumes)) / vol_total if vol_total else closes[-1]
    window_close  = closes[-period:]
    window_high   = highs[-period:]
    window_low    = lows[-period:]
    window_vol    = volumes[-period:]
    typical = [(window_high[i]+window_low[i]+window_close[i])/3 for i in range(period)]
    vol_total = sum(window_vol)
    if vol_total == 0:
        return closes[-1]
    return round(sum(t*v for t,v in zip(typical, window_vol)) / vol_total, 2)

def calc_fibonacci(closes, highs, lows, period=60):
    """Tự động tìm swing high/low trong N ngày, tính Fib retracement
    Returns dict: fib_382, fib_500, fib_618, swing_high, swing_low
    """
    n = min(period, len(closes))
    recent_highs = highs[-n:]
    recent_lows  = lows[-n:]
    swing_high = max(recent_highs)
    swing_low  = min(recent_lows)
    diff = swing_high - swing_low
    if diff == 0:
        return None
    return {
        "swing_high": round(swing_high, 2),
        "swing_low":  round(swing_low, 2),
        "fib_236":    round(swing_high - diff * 0.236, 2),
        "fib_382":    round(swing_high - diff * 0.382, 2),
        "fib_500":    round(swing_high - diff * 0.500, 2),
        "fib_618":    round(swing_high - diff * 0.618, 2),
        "fib_786":    round(swing_high - diff * 0.786, 2),
    }

def calc_pivot_points(df_weekly):
    """Weekly Pivot Points từ tuần gần nhất
    Returns: pivot, s1, s2, r1, r2
    """
    if df_weekly is None or len(df_weekly) < 2:
        return None
    last_week = df_weekly.iloc[-2]  # tuần hoàn chỉnh gần nhất
    H = float(last_week['high'])
    L = float(last_week['low'])
    C = float(last_week['close'])
    P  = (H + L + C) / 3
    S1 = round(2*P - H, 2)
    S2 = round(P - (H - L), 2)
    R1 = round(2*P - L, 2)
    R2 = round(P + (H - L), 2)
    return {"pivot": round(P,2), "s1": S1, "s2": S2, "r1": R1, "r2": R2}

def calc_volume_profile(closes, highs, lows, volumes, period=60, buckets=20):
    """Volume Profile — phân bổ volume theo vùng giá trong N ngày
    Returns: poc (Point of Control), vah (Value Area High), val (Value Area Low)
    """
    n = min(period, len(closes))
    c_w = closes[-n:]; h_w = highs[-n:]; l_w = lows[-n:]; v_w = volumes[-n:]
    price_min = min(l_w)
    price_max = max(h_w)
    if price_max == price_min:
        return {"poc": closes[-1], "vah": closes[-1], "val": closes[-1]}
    bucket_size = (price_max - price_min) / buckets
    histogram = [0.0] * buckets
    for i in range(n):
        # Phân bổ volume đều vào các bucket trong range High-Low ngày đó
        day_min = l_w[i]; day_max = h_w[i]
        if day_max == day_min:
            b = min(int((day_min - price_min) / bucket_size), buckets-1)
            histogram[b] += v_w[i]
        else:
            for b in range(buckets):
                b_low  = price_min + b * bucket_size
                b_high = b_low + bucket_size
                overlap = max(0, min(day_max, b_high) - max(day_min, b_low))
                ratio   = overlap / (day_max - day_min)
                histogram[b] += v_w[i] * ratio
    # POC = bucket có volume cao nhất
    poc_idx  = histogram.index(max(histogram))
    poc_price = round(price_min + (poc_idx + 0.5) * bucket_size, 2)
    # Value Area = 70% tổng volume quanh POC
    total_vol   = sum(histogram)
    target_vol  = total_vol * 0.70
    va_vol = histogram[poc_idx]
    lo_idx = poc_idx; hi_idx = poc_idx
    while va_vol < target_vol and (lo_idx > 0 or hi_idx < buckets-1):
        add_lo = histogram[lo_idx-1] if lo_idx > 0 else 0
        add_hi = histogram[hi_idx+1] if hi_idx < buckets-1 else 0
        if add_lo >= add_hi and lo_idx > 0:
            lo_idx -= 1; va_vol += add_lo
        elif hi_idx < buckets-1:
            hi_idx += 1; va_vol += add_hi
        else:
            break
    val = round(price_min + lo_idx * bucket_size, 2)
    vah = round(price_min + (hi_idx+1) * bucket_size, 2)
    return {"poc": poc_price, "vah": vah, "val": val}

def detect_regime(closes, highs, lows, volumes, adx_val):
    """Nhận diện Market Regime
    TRENDING  : ADX>25 + EMA stack thẳng
    BREAKOUT  : Vol surge + giá phá đỉnh 20 ngày
    RANGING   : ADX<20 + giá dao động kênh
    TRANSITION: Còn lại
    """
    n = len(closes)
    # EMA stack
    ema9_s  = ema(closes, 9)
    ema20_s = ema(closes, 20)
    ema50_s = ema(closes, 50)
    e9  = next((x for x in reversed(ema9_s)  if x is not None), closes[-1])
    e20 = next((x for x in reversed(ema20_s) if x is not None), closes[-1])
    e50 = next((x for x in reversed(ema50_s) if x is not None), closes[-1])
    ema_stack = e9 > e20 > e50 and closes[-1] > e9

    # Volume surge (vs TB20)
    vol_avg20 = sum(volumes[-20:]) / min(20, n)
    vol_surge = volumes[-1] > vol_avg20 * 1.8

    # Breakout: giá hôm nay > max 20 ngày trước
    high_20d  = max(highs[-21:-1]) if n > 21 else max(highs[:-1])
    breakout  = closes[-1] > high_20d * 1.005 and vol_surge

    # Ranging: giá trong kênh hẹp (range 20 ngày < 15%)
    high_20  = max(highs[-20:])
    low_20   = min(lows[-20:])
    range_20_pct = (high_20 - low_20) / low_20 * 100 if low_20 > 0 else 20

    if breakout:
        regime = "BREAKOUT"
        regime_note = f"Giá phá đỉnh {high_20d:,.2f} với vol surge — momentum mạnh"
    elif adx_val > 25 and ema_stack:
        regime = "TRENDING"
        regime_note = f"ADX={adx_val:.0f} (>25) + EMA stack thẳng — xu hướng tăng rõ"
    elif adx_val < 20 and range_20_pct < 15:
        regime = "RANGING"
        regime_note = f"ADX={adx_val:.0f} (<20) + range 20 ngày {range_20_pct:.1f}% — tích lũy ngang"
    else:
        regime = "TRANSITION"
        regime_note = f"ADX={adx_val:.0f} — chuyển tiếp, chưa rõ xu hướng"

    return regime, regime_note

def calc_smart_dca(closes, highs, lows, volumes, df_weekly,
                   ema9, ema20, ema50, bb_lower, bb_mid, bb_upper,
                   st_val, vwap_daily):
    """Hybrid Regime + Confluence DCA
    1. Tính ADX + nhận diện regime
    2. Thu thập tất cả mức giá từ 7 nguồn (tùy regime)
    3. Cluster các mức gần nhau (±1.5%)
    4. Rank cluster → chọn 1 vùng DCA tốt nhất
    5. Nếu zone xa >8% → thêm breakout entry
    """
    current_price = closes[-1]
    adx_val = calc_adx(highs, lows, closes, 14)
    regime, regime_note = detect_regime(closes, highs, lows, volumes, adx_val)

    # ── Thu thập tất cả mức giá tiềm năng ──
    # Format: (price, source_name, weight)
    # Weight: POC=3, VWAP=2, EMA/ST/Fib/Pivot=1
    all_levels = []

    def add(price, name, weight=1):
        if price and price > 0:
            all_levels.append((round(price, 2), name, weight))

    # VWAP20
    vwap20 = calc_vwap_rolling(closes, highs, lows, volumes, 20)
    add(vwap20, "VWAP20", 2)

    # Volume Profile POC/VAL
    vp = calc_volume_profile(closes, highs, lows, volumes, 60)
    if vp:
        add(vp["poc"], "POC", 3)
        add(vp["val"], "VAL", 2)

    # Fibonacci retracement
    fib = calc_fibonacci(closes, highs, lows, 60)
    if fib:
        add(fib["fib_382"], "Fib 38.2%", 1)
        add(fib["fib_500"], "Fib 50%",   1)
        add(fib["fib_618"], "Fib 61.8%", 1)

    # Weekly Pivot Points
    pp = calc_pivot_points(df_weekly)
    if pp:
        add(pp["s1"], "Pivot S1", 1)
        add(pp["s2"], "Pivot S2", 1)

    # SuperTrend level
    if st_val:
        add(st_val, "SuperTrend", 1)

    # EMA levels — tùy regime
    if regime in ("TRENDING", "BREAKOUT", "TRANSITION"):
        add(ema9,  "EMA9",  1)
        add(ema20, "EMA20", 1)
    if regime in ("RANGING", "TRANSITION"):
        add(ema20, "EMA20", 1)
        add(ema50, "EMA50", 1)
        if bb_mid:   add(bb_mid,   "BB Mid",   1)
        if bb_lower: add(bb_lower, "BB Lower", 1)
    if regime == "TRENDING":
        add(ema50, "EMA50", 1)

    # ── Cluster các mức giá gần nhau (±1.5%) ──
    # Chỉ lấy các mức DƯỚI hoặc bằng giá hiện tại (vùng hỗ trợ, không phải kháng cự)
    support_levels = [(p, n, w) for p, n, w in all_levels if p <= current_price * 1.005]
    support_levels.sort(key=lambda x: x[0], reverse=True)  # gần giá nhất trước

    clusters = []
    used = [False] * len(support_levels)
    for i, (p, n, w) in enumerate(support_levels):
        if used[i]:
            continue
        cluster = [(p, n, w)]
        used[i] = True
        for j, (p2, n2, w2) in enumerate(support_levels):
            if used[j]:
                continue
            if abs(p2 - p) / p <= 0.015:  # ±1.5% tolerance
                cluster.append((p2, n2, w2))
                used[j] = True
        clusters.append(cluster)

    if not clusters:
        # Fallback: dùng EMA20 nếu không có cluster nào
        return {
            "regime":        regime,
            "regime_note":   regime_note,
            "adx":           adx_val,
            "best_zone_low":  round(ema20 * 0.99, 2),
            "best_zone_high": round(ema20 * 1.01, 2),
            "best_zone_str":  f"{round(ema20*0.99,2):,.2f}–{round(ema20*1.01,2):,.2f}",
            "best_zone_sources": ["EMA20 (fallback)"],
            "best_zone_stars":   "⭐",
            "best_zone_dist_pct": round((ema20 - current_price) / current_price * 100, 1),
            "breakout_entry": None,
            "fib":   fib,
            "pivot": pp,
            "vp":    vp,
        }

    # ── Rank mỗi cluster ──
    def score_cluster(cluster):
        num_sources = len(cluster)
        weight_sum  = sum(w for _, _, w in cluster)
        # Bonus cho POC và VWAP
        has_poc  = any("POC"  in n for _, n, _ in cluster)
        has_vwap = any("VWAP" in n for _, n, _ in cluster)
        # Ưu tiên gần giá hơn (regime trending: không quá xa)
        center = sum(p for p, _, _ in cluster) / len(cluster)
        dist_pct = abs(center - current_price) / current_price * 100
        proximity_bonus = max(0, 5 - dist_pct)  # gần hơn = bonus cao hơn
        return weight_sum + (2 if has_poc else 0) + (1 if has_vwap else 0) + proximity_bonus * 0.3

    clusters.sort(key=score_cluster, reverse=True)
    best = clusters[0]

    # ── Tính vùng giá từ cluster tốt nhất ──
    prices  = [p for p, _, _ in best]
    sources = [n for _, n, _ in best]
    zone_center = sum(prices) / len(prices)
    zone_low    = round(min(prices) * 0.995, 2)
    zone_high   = round(max(prices) * 1.005, 2)
    dist_pct    = round((zone_center - current_price) / current_price * 100, 1)

    # Stars rating
    n_sources = len(best)
    if n_sources >= 4:   stars = "⭐⭐⭐⭐"
    elif n_sources == 3: stars = "⭐⭐⭐"
    elif n_sources == 2: stars = "⭐⭐"
    else:                stars = "⭐"

    # ── Breakout entry nếu zone xa >8% ──
    breakout_entry = None
    if dist_pct < -8:
        # Mức breakout = đỉnh gần nhất + 0.5%
        recent_high = max(highs[-10:])
        breakout_price = round(recent_high * 1.005, 2)
        if breakout_price > current_price:
            breakout_entry = {
                "price":  breakout_price,
                "note":   f"Mua breakout khi vượt {breakout_price:,.2f} kèm vol > 1.5× TB20",
            }

    return {
        "regime":             regime,
        "regime_note":        regime_note,
        "adx":                adx_val,
        "best_zone_low":      zone_low,
        "best_zone_high":     zone_high,
        "best_zone_str":      f"{zone_low:,.2f}–{zone_high:,.2f}",
        "best_zone_sources":  sources,
        "best_zone_stars":    stars,
        "best_zone_dist_pct": dist_pct,
        "breakout_entry":     breakout_entry,
        "fib":                fib,
        "pivot":              pp,
        "vp":                 vp,
    }

# ══════════════════════════════════════════════════════════════
# MTF HELPERS — Resample & Score từng khung thời gian
# ══════════════════════════════════════════════════════════════

def resample_ohlcv(df_daily, rule='W-FRI'):
    """Resample daily OHLCV → Weekly hoặc Monthly
    rule: 'W-FRI' = weekly (kết thúc thứ 6), 'ME' = monthly
    """
    try:
        df = df_daily.copy()
        df['time'] = pd.to_datetime(df['time'])
        df = df.set_index('time')
        df_rs = df.resample(rule).agg({
            'open':   'first',
            'high':   'max',
            'low':    'min',
            'close':  'last',
            'volume': 'sum',
        }).dropna()
        df_rs = df_rs.reset_index()
        return df_rs
    except Exception as e:
        return None

def calc_tf_score(closes, highs, lows, volumes, tf_name='weekly'):
    """Tính score 0-100 cho một khung thời gian (weekly hoặc monthly)
    Weekly : 7 chỉ báo — EMA9/20, Price>EMA9, ST, MACD, RSI, OBV, Vol
    Monthly: 6 chỉ báo — EMA9/12, Price>EMA9, MACD, RSI, OBV, Vol
    """
    n = len(closes)
    if tf_name == 'monthly' and n < 6:
        return 0, "Không đủ data monthly", {}
    if tf_name == 'weekly' and n < 10:
        return 0, "Không đủ data weekly", {}

    score = 0
    details = {}

    # EMA periods tùy khung
    if tf_name == 'monthly':
        p_fast, p_slow = 9, 12
    else:
        p_fast, p_slow = 9, 20

    ema_fast_s = ema(closes, p_fast)
    ema_slow_s = ema(closes, p_slow)
    ef = next((x for x in reversed(ema_fast_s) if x is not None), closes[-1])
    es = next((x for x in reversed(ema_slow_s) if x is not None), closes[-1])
    latest = closes[-1]

    # MACD
    ml, sl_m, hist_m = calc_macd(closes, 6, 13, 5) if tf_name == 'monthly' else calc_macd(closes, 12, 26, 9)
    macd_v  = next((x for x in reversed(ml)    if x is not None), 0)
    hist_v  = next((x for x in reversed(hist_m) if x is not None), 0)

    # RSI
    rsi_v = calc_rsi(closes, 14)

    # OBV — tăng trong 3 nến gần nhất
    obv_s = calc_obv(closes, volumes)
    obv_up = obv_s[-1] > obv_s[-4] if len(obv_s) >= 4 else False

    # Volume vs TB
    if tf_name == 'monthly':
        vol_avg_n = 6
    else:
        vol_avg_n = 10
    vol_avg = sum(volumes[-vol_avg_n:]) / min(len(volumes), vol_avg_n)
    vol_surge = volumes[-1] > vol_avg

    if tf_name == 'monthly':
        # ─── Monthly Score (100đ) ───
        c1 = ef > es
        c2 = latest > ef
        c3 = macd_v > 0
        c4 = 40 <= rsi_v <= 75
        c5 = bool(obv_up)
        c6 = bool(vol_surge)
        weights = [(c1,20),(c2,15),(c3,20),(c4,15),(c5,15),(c6,15)]
        score = sum(w for c,w in weights if c)
        details = {
            f"EMA9M({ef:,.2f})>EMA12M({es:,.2f})":   ("✅" if c1 else "❌"),
            f"Giá({latest:,.2f})>EMA9M":              ("✅" if c2 else "❌"),
            "MACD Monthly dương":                      ("✅" if c3 else "❌"),
            f"RSI Monthly {rsi_v:.0f} (40–75)":        ("✅" if c4 else "❌"),
            "OBV Monthly tăng 3 kỳ":                   ("✅" if c5 else "❌"),
            f"Vol tháng > TB{vol_avg_n} tháng":         ("✅" if c6 else "❌"),
        }
        note = f"EMA {'✅' if c1 else '❌'} | MACD {'✅' if c3 else '❌'} | RSI {rsi_v:.0f} | OBV {'✅' if c5 else '❌'}"
    else:
        # ─── Weekly Score (100đ) ───
        # SuperTrend weekly
        st_dir_w, _ = calc_supertrend(highs, lows, closes, 10, 3.0)
        c1 = ef > es
        c2 = latest > ef
        c3 = st_dir_w == "BULL"
        c4 = macd_v > 0
        c5 = 40 <= rsi_v <= 72
        c6 = bool(obv_up)
        c7 = bool(vol_surge)
        weights = [(c1,20),(c2,15),(c3,15),(c4,15),(c5,15),(c6,10),(c7,10)]
        score = sum(w for c,w in weights if c)
        details = {
            f"EMA9W({ef:,.2f})>EMA20W({es:,.2f})":    ("✅" if c1 else "❌"),
            f"Giá({latest:,.2f})>EMA9W":               ("✅" if c2 else "❌"),
            "SuperTrend Weekly BULL":                   ("✅" if c3 else "❌"),
            "MACD Weekly dương":                        ("✅" if c4 else "❌"),
            f"RSI Weekly {rsi_v:.0f} (40–72)":          ("✅" if c5 else "❌"),
            "OBV Weekly tăng":                          ("✅" if c6 else "❌"),
            f"Vol tuần > TB{vol_avg_n} tuần":            ("✅" if c7 else "❌"),
        }
        note = f"EMA {'✅' if c1 else '❌'} | ST {'✅' if c3 else '❌'} | RSI {rsi_v:.0f} | OBV {'✅' if c6 else '❌'}"

    return score, note, details

def calc_confluence(closes, highs, lows, opens, volumes, rsi_series, ema20, ema50, symbol):
    """Confluence Layer — A+B+C+D (tổng 40đ)
    A: Mẫu nến đảo chiều tại vùng hỗ trợ  (+10)
    B: RSI Bullish Divergence               (+10)
    C: Volume Dry-up trong pullback         (+10)
    D: Relative Strength vs VNINDEX         (+10)
    """
    n = len(closes)
    confluence_score = 0
    signals = {}

    # ── A: Mẫu nến đảo chiều tại/gần vùng hỗ trợ ──
    at_support = closes[-1] <= ema20 * 1.02  # trong vòng 2% trên EMA20
    a_score = 0
    a_note  = ""
    if n >= 2:
        o, h, c, l = opens[-1], highs[-1], closes[-1], lows[-1]
        po, ph, pc, pl = opens[-2], highs[-2], closes[-2], lows[-2]
        body        = abs(c - o)
        lower_shadow = c - l if c > o else o - l
        upper_shadow = h - c if c > o else h - o
        full_range   = h - l if h != l else 0.0001

        # Hammer: thân nhỏ, bóng dưới dài ≥ 2×thân, bóng trên ngắn
        is_hammer = (body > 0 and lower_shadow >= 2 * body
                     and upper_shadow <= body * 0.5
                     and c > o and at_support)
        # Bullish Engulfing: nến đỏ hôm qua, nến xanh hôm nay bao trùm
        is_engulfing = (pc < po and c > o and c >= po and o <= pc)
        # Doji tại support: thân rất nhỏ so với range
        is_doji_support = (body / full_range < 0.1 and at_support)

        if is_hammer:
            a_score = 10; a_note = "Hammer tại vùng hỗ trợ 🔨"
        elif is_engulfing:
            a_score = 10; a_note = "Bullish Engulfing 📈"
        elif is_doji_support:
            a_score = 6;  a_note = "Doji tại hỗ trợ (tín hiệu yếu hơn)"
        else:
            a_note = "Không có mẫu nến đảo chiều"
    signals["A. Mẫu nến đảo chiều"] = f"{'✅' if a_score > 0 else '❌'} {a_note} (+{a_score}đ)"
    confluence_score += a_score

    # ── B: RSI Bullish Divergence ──
    b_score = 0; b_note = ""
    valid_rsi = [x for x in rsi_series if x is not None]
    lookback  = min(20, len(closes)-1, len(valid_rsi)-1)
    if lookback >= 5:
        price_window = closes[-lookback-1:-1]
        rsi_window   = valid_rsi[-lookback-1:-1]
        if len(price_window) > 0 and len(rsi_window) > 0:
            prev_price_low = min(price_window)
            prev_rsi_low   = min(rsi_window)
            curr_price     = closes[-1]
            curr_rsi       = valid_rsi[-1]
            price_lower = curr_price < prev_price_low * 0.99  # giá thấp hơn ít nhất 1%
            rsi_higher  = curr_rsi   > prev_rsi_low   + 3     # RSI cao hơn ít nhất 3 điểm
            if price_lower and rsi_higher:
                b_score = 10
                p_curr = f"{curr_price*1000:,.0f}đ" if curr_price < 1000 else f"{curr_price:,.0f}đ"
                p_prev = f"{prev_price_low*1000:,.0f}đ" if prev_price_low < 1000 else f"{prev_price_low:,.0f}đ"
                b_note  = f"Giá thấp hơn ({p_curr}<{p_prev}) nhưng RSI cao hơn ({curr_rsi:.0f}>{prev_rsi_low:.0f}) 📈"
            else:
                p_curr = f"{curr_price*1000:,.0f}đ" if curr_price < 1000 else f"{curr_price:,.0f}đ"
                p_prev = f"{prev_price_low*1000:,.0f}đ" if prev_price_low < 1000 else f"{prev_price_low:,.0f}đ"
                b_note = f"Không có divergence — giá: {p_curr} vs đáy {p_prev}, RSI: {curr_rsi:.0f} vs {prev_rsi_low:.0f}"
    signals["B. RSI Bullish Divergence"] = f"{'✅' if b_score > 0 else '❌'} {b_note} (+{b_score}đ)"
    confluence_score += b_score

    # ── C: Volume Dry-up — áp lực bán yếu trong pullback ──
    c_score = 0; c_note = ""
    if n >= 6:
        # Lấy 5 nến gần nhất có giá giảm
        pullback_vols = [volumes[i] for i in range(-5, 0) if closes[i] < closes[i-1]]
        if len(pullback_vols) >= 3:
            # Volume nến cuối pullback < 70% volume đầu pullback
            if pullback_vols[-1] < pullback_vols[0] * 0.70:
                c_score = 10
                c_note  = f"Vol giảm dần trong pullback ({pullback_vols[-1]/1e6:.1f}M < {pullback_vols[0]/1e6:.1f}M) — bán yếu"
            elif pullback_vols[-1] < pullback_vols[0] * 0.85:
                c_score = 5
                c_note  = "Vol giảm nhẹ trong pullback"
            else:
                c_note  = "Vol không giảm — áp lực bán còn"
        else:
            c_note = "Không đủ nến pullback để đánh giá"
    signals["C. Volume Dry-up"] = f"{'✅' if c_score > 0 else '❌'} {c_note} (+{c_score}đ)"
    confluence_score += c_score

    # ── D: Relative Strength vs VNINDEX ──
    d_score = 0; d_note = ""
    try:
        df_vni = get_stock_data('VNINDEX', days=40)
        if df_vni is not None and len(df_vni) >= 20:
            vni_closes  = df_vni['close'].astype(float).tolist()
            rs_stock_pct = (closes[-1] - closes[-20]) / closes[-20] * 100 if closes[-20] != 0 else 0
            rs_vni_pct   = (vni_closes[-1] - vni_closes[-20]) / vni_closes[-20] * 100 if vni_closes[-20] != 0 else 0
            outperform   = round(rs_stock_pct - rs_vni_pct, 1)
            if outperform >= 5:
                d_score = 10
                d_note  = f"Vượt trội VNIndex +{outperform:.1f}% (20 ngày) 🔥"
            elif outperform >= 0:
                d_score = 5
                d_note  = f"Nhỉnh hơn VNIndex +{outperform:.1f}% (20 ngày)"
            elif outperform >= -5:
                d_note  = f"Ngang thị trường ({outperform:+.1f}% vs VNIndex)"
            else:
                d_note  = f"Yếu hơn VNIndex {outperform:.1f}% (20 ngày) ⚠️"
        else:
            d_note = "Không lấy được data VNINDEX"
    except Exception:
        d_note = "Lỗi khi lấy VNINDEX"
    signals["D. Sức mạnh vs VNINDEX"] = f"{'✅' if d_score > 0 else '❌'} {d_note} (+{d_score}đ)"
    confluence_score += d_score

    # ── Phân loại Confluence ──
    if confluence_score >= 30:
        cf_level = "RẤT MẠNH 🔥🔥"
    elif confluence_score >= 20:
        cf_level = "MẠNH 🔥"
    elif confluence_score >= 10:
        cf_level = "TRUNG BÌNH ⚡"
    else:
        cf_level = "YẾU ❄️"

    return {
        "score":      confluence_score,
        "max_score":  40,
        "level":      cf_level,
        "signals":    signals,
        "note":       f"Confluence {confluence_score}/40 — {cf_level}",
    }

def calc_mtf(df_daily, daily_score, symbol):
    """MTF Score = Monthly×30% + Weekly×35% + Daily×35%
    Fallback nếu monthly < 6 nến → Weekly×45% + Daily×55%
    """
    # ── Resample ──
    df_w = resample_ohlcv(df_daily, 'W-FRI')
    df_m = resample_ohlcv(df_daily, 'ME')

    # ── Weekly score ──
    w_score, w_note, w_detail = 0, "Không đủ data", {}
    if df_w is not None and len(df_w) >= 10:
        wc = df_w['close'].astype(float).tolist()
        wh = df_w['high'].astype(float).tolist()
        wl = df_w['low'].astype(float).tolist()
        wv = df_w['volume'].astype(float).tolist()
        w_score, w_note, w_detail = calc_tf_score(wc, wh, wl, wv, 'weekly')

    # ── Monthly score ──
    m_score, m_note, m_detail = 0, "Không đủ data", {}
    fallback = True
    if df_m is not None and len(df_m) >= 6:
        mc = df_m['close'].astype(float).tolist()
        mh = df_m['high'].astype(float).tolist()
        ml = df_m['low'].astype(float).tolist()
        mv = df_m['volume'].astype(float).tolist()
        m_score, m_note, m_detail = calc_tf_score(mc, mh, ml, mv, 'monthly')
        fallback = False

    # ── Trọng số & MTF Total ──
    if fallback:
        mtf_total  = round(w_score * 0.45 + daily_score * 0.55)
        weights_str = "Weekly 45% + Daily 55% (fallback — thiếu Monthly)"
    else:
        mtf_total  = round(m_score * 0.30 + w_score * 0.35 + daily_score * 0.35)
        weights_str = "Monthly 30% + Weekly 35% + Daily 35%"

    # ── Quyết định MTF ──
    base_score   = mtf_total
    monthly_weak = (not fallback) and (m_score < 35)
    monthly_dead = (not fallback) and (m_score < 20)

    def mtf_decision_from_score(s):
        if s >= 75:   return "MUA_MANH",       "🟢🟢 MUA MẠNH",            ["tang1","tang2","tang3"]
        elif s >= 60: return "TICH_LUY",        "🟢 TÍCH LŨY",              ["tang1","tang2"]
        elif s >= 50: return "NAM_GIU",         "⚪ NẮM GIỮ",               ["tang3"]
        elif s >= 40: return "THEO_DOI",        "🟡 THEO DÕI",              []
        else:         return "CANH_BAO",        "🔴 CẢNH BÁO",              []

    decision_key, decision_vi, dca_allowed = mtf_decision_from_score(base_score)

    # Rule giảm 1 bậc nếu Monthly yếu
    downgraded = False
    if monthly_dead:
        decision_key  = "CANH_BAO"
        decision_vi   = "🔴 CẢNH BÁO — Monthly rất xấu"
        dca_allowed   = []
        downgraded    = True
    elif monthly_weak and decision_key not in ["CANH_BAO", "THEO_DOI"]:
        levels = ["MUA_MANH","TICH_LUY","NAM_GIU","THEO_DOI","CANH_BAO"]
        idx = levels.index(decision_key)
        decision_key, decision_vi, dca_allowed = mtf_decision_from_score(base_score - 15)
        downgraded = True

    # Cảnh báo Monthly
    if monthly_dead:
        monthly_warning = f"⛔ Monthly score {m_score}/100 — xu hướng lớn rất xấu, chặn DCA"
    elif monthly_weak:
        monthly_warning = f"⚠️ Monthly score {m_score}/100 — xu hướng lớn chưa xác nhận, giảm 1 bậc"
    else:
        monthly_warning = None

    # DCA labels
    dca_labels = {
        "tang1": "Tầng 1 (EMA20)",
        "tang2": "Tầng 2 (EMA50)",
        "tang3": "Tầng 3 (BB Lower — oversold)"
    }
    dca_note = " | ".join(dca_labels[t] for t in dca_allowed) if dca_allowed else "Không DCA"

    return {
        "monthly_score":    m_score,
        "weekly_score":     w_score,
        "daily_score":      daily_score,
        "mtf_total":        mtf_total,
        "weights":          weights_str,
        "fallback":         fallback,
        "decision":         decision_key,
        "decision_vi":      decision_vi,
        "dca_allowed":      dca_allowed,
        "dca_note":         dca_note,
        "downgraded":       downgraded,
        "monthly_warning":  monthly_warning,
        "monthly_note":     m_note,
        "weekly_note":      w_note,
        "monthly_detail":   m_detail,
        "weekly_detail":    w_detail,
        "monthly_candles":  len(df_m) if df_m is not None else 0,
        "weekly_candles":   len(df_w) if df_w is not None else 0,
    }

# ══════════════════════════════════════════════════════════════
# COMBO ENGINE — Tính điểm và tín hiệu 5 combo
# ══════════════════════════════════════════════════════════════

def run_combo_analysis(symbol):
    symbol = symbol.upper()
    df = get_stock_data(symbol, days=400)
    if df is None or len(df) < 30:
        return None, f"Không đủ dữ liệu cho {symbol}"

    closes  = df['close'].astype(float).tolist()
    highs   = df['high'].astype(float).tolist()
    lows    = df['low'].astype(float).tolist()
    volumes = df['volume'].astype(float).tolist()
    n = len(closes)

    latest_close  = closes[-1]
    latest_vol    = volumes[-1]
    prev_close    = closes[-2] if n > 1 else closes[-1]
    change        = latest_close - prev_close
    change_pct    = (change / prev_close * 100) if prev_close else 0
    latest_date   = str(df['time'].iloc[-1])[:10]

    # ── Tính tất cả chỉ báo ──
    ema9_s  = ema(closes, 9)
    ema20_s = ema(closes, 20)
    ema50_s = ema(closes, 50)
    ema9    = next((x for x in reversed(ema9_s)  if x is not None), closes[-1])
    ema20   = next((x for x in reversed(ema20_s) if x is not None), closes[-1])
    ema50   = next((x for x in reversed(ema50_s) if x is not None), closes[-1])

    macd_line, signal_line, histogram = calc_macd(closes)
    macd_val  = next((x for x in reversed(macd_line)  if x is not None), 0)
    signal_val= next((x for x in reversed(signal_line) if x is not None), 0)
    hist_val  = next((x for x in reversed(histogram)  if x is not None), 0)
    hist_prev = next((x for x in reversed(histogram[:-1]) if x is not None), 0)

    rsi14       = calc_rsi(closes, 14)
    vol_ma20    = sum(volumes[-20:]) / min(n, 20)
    vol_ratio   = latest_vol / vol_ma20 if vol_ma20 > 0 else 1.0
    obv_series  = calc_obv(closes, volumes)
    obv_now     = obv_series[-1]
    obv_5ago    = obv_series[-6] if len(obv_series) > 5 else obv_series[0]
    obv_trend   = "UP" if obv_now > obv_5ago else "DOWN"
    bb_series   = calc_bollinger(closes, 20)
    bb_mid, bb_upper, bb_lower = bb_series[-1]
    if bb_mid is None:
        bb_mid = latest_close; bb_upper = latest_close * 1.02; bb_lower = latest_close * 0.98
    atr14       = calc_atr(highs, lows, closes, 14)
    stoch_k, stoch_d = calc_stoch_rsi(closes)
    st_dir, st_val  = calc_supertrend(highs, lows, closes, 10, 3.0)
    cmf20       = calc_cmf(highs, lows, closes, volumes, 20)
    mfi14       = calc_mfi(highs, lows, closes, volumes, 14)

    # MACD crossover detection
    macd_cross_up = False
    macd_cross_dn = False
    valid_m = [(i, macd_line[i], signal_line[i]) for i in range(len(macd_line))
               if macd_line[i] is not None and signal_line[i] is not None]
    if len(valid_m) >= 2:
        m_now  = valid_m[-1][1]; s_now  = valid_m[-1][2]
        m_prev = valid_m[-2][1]; s_prev = valid_m[-2][2]
        macd_cross_up = (m_prev <= s_prev) and (m_now > s_now)
        macd_cross_dn = (m_prev >= s_prev) and (m_now < s_now)

    # OBV divergence (bullish: price lower, OBV higher)
    obv_bull_div = (closes[-1] < closes[-6]) and (obv_now > obv_5ago) if len(closes) > 5 else False

    # Price vs BB position
    bb_pos = "UPPER" if latest_close > bb_upper else ("LOWER" if latest_close < bb_lower else "MIDDLE")

    # ══════════════════════════════════════════════════════════
    # COMBO 1: EMA Stack + MACD + Volume  (Momentum Rider)
    # ══════════════════════════════════════════════════════════
    c1_conditions = [
        ("EMA Stack thuận (9>20>50)", ema9 > ema20 > ema50),
        ("MACD dương và tăng (histogram xanh)", macd_val > 0 and hist_val > hist_prev),
        ("Volume xác nhận (>150% MA20)", vol_ratio >= 1.5),
        ("RSI vùng khỏe mạnh (45-65)", 45 <= rsi14 <= 70),
        ("Giá trên EMA50", latest_close > ema50),
    ]
    c1_score = sum(1 for _, v in c1_conditions if v)
    c1_pct   = int(c1_score / len(c1_conditions) * 100)
    if c1_score >= 4:   c1_signal = "STRONG BUY 🟢🟢"
    elif c1_score == 3: c1_signal = "BUY 🟢"
    elif c1_score == 2: c1_signal = "NEUTRAL ⚪"
    elif c1_score == 1: c1_signal = "SELL 🔴"
    else:               c1_signal = "STRONG SELL 🔴🔴"

    # ══════════════════════════════════════════════════════════
    # COMBO 2: VWAP + OBV + Bollinger  (Intraday Precision)
    # ══════════════════════════════════════════════════════════
    # VWAP approximation = SMA giá điển hình × volume
    typical_prices = [(highs[i]+lows[i]+closes[i])/3 for i in range(n)]
    vwap_num = sum(typical_prices[-20:][i]*volumes[-20:][i] for i in range(min(20,n)))
    vwap_den = sum(volumes[-20:])
    vwap = vwap_num / vwap_den if vwap_den > 0 else latest_close

    c2_conditions = [
        ("Giá trên VWAP (dòng tiền tích cực)", latest_close >= vwap),
        ("OBV đang tăng (smart money vào)", obv_trend == "UP"),
        ("OBV bullish divergence (tổ chức tích lũy)", obv_bull_div),
        ("RSI từ vùng oversold bật lên (35-55)", 35 <= rsi14 <= 60),
        ("BB: không quá overbought (dưới upper)", bb_pos != "UPPER" or rsi14 < 65),
    ]
    c2_score = sum(1 for _, v in c2_conditions if v)
    c2_pct   = int(c2_score / len(c2_conditions) * 100)
    if c2_score >= 4:   c2_signal = "STRONG BUY 🟢🟢"
    elif c2_score == 3: c2_signal = "BUY 🟢"
    elif c2_score == 2: c2_signal = "NEUTRAL ⚪"
    elif c2_score == 1: c2_signal = "SELL 🔴"
    else:               c2_signal = "STRONG SELL 🔴🔴"

    # ══════════════════════════════════════════════════════════
    # COMBO 3: SuperTrend + Stoch RSI + CMF  (Swing Sniper)
    # ══════════════════════════════════════════════════════════
    c3_conditions = [
        ("SuperTrend BULL (xu hướng xanh)", st_dir == "BULL"),
        ("Giá trên SuperTrend support", latest_close > st_val),
        ("Stoch RSI từ oversold bật (<20 → tăng)", stoch_k < 50 and stoch_k > 20),
        ("CMF dương (dòng tiền ròng vào)", cmf20 > 0),
        ("Giá trên EMA20", latest_close > ema20),
    ]
    c3_score = sum(1 for _, v in c3_conditions if v)
    c3_pct   = int(c3_score / len(c3_conditions) * 100)
    if c3_score >= 4:   c3_signal = "STRONG BUY 🟢🟢"
    elif c3_score == 3: c3_signal = "BUY 🟢"
    elif c3_score == 2: c3_signal = "NEUTRAL ⚪"
    elif c3_score == 1: c3_signal = "SELL 🔴"
    else:               c3_signal = "STRONG SELL 🔴🔴"

    # ══════════════════════════════════════════════════════════
    # COMBO 4: MACD Cross + RSI + MFI  (Momentum Confirmed)
    # ══════════════════════════════════════════════════════════
    c4_conditions = [
        ("MACD vừa crossover lên (trigger mạnh)", macd_cross_up),
        ("RSI không overbought (<70)", rsi14 < 70),
        ("MFI dòng tiền vào (>50)", mfi14 > 50),
        ("Volume surge (>130% TB)", vol_ratio >= 1.3),
        ("Nến xanh (close > open)", latest_close > df['open'].astype(float).iloc[-1]),
    ]
    c4_score = sum(1 for _, v in c4_conditions if v)
    c4_pct   = int(c4_score / len(c4_conditions) * 100)
    if c4_score >= 4:   c4_signal = "STRONG BUY 🟢🟢"
    elif c4_score == 3: c4_signal = "BUY 🟢"
    elif c4_score == 2: c4_signal = "NEUTRAL ⚪"
    elif c4_score == 1: c4_signal = "SELL 🔴"
    else:               c4_signal = "STRONG SELL 🔴🔴"

    # ══════════════════════════════════════════════════════════
    # COMBO 5: Multi-TF Trend Strength  (Trend Filter)
    # ══════════════════════════════════════════════════════════
    # Tính EMA200 nếu có đủ data
    ema200_val = None
    if n >= 60:
        ema200_s = ema(closes, min(60, n))
        ema200_val = next((x for x in reversed(ema200_s) if x is not None), None)

    # Trend strength: đếm bao nhiêu EMA xếp tầng đúng
    ema_align = sum([
        1 if latest_close > ema9 else 0,
        1 if ema9 > ema20 else 0,
        1 if ema20 > ema50 else 0,
        1 if (ema200_val is None or ema50 > ema200_val) else 0,
    ])

    # 5-phiên momentum
    mom5 = (closes[-1] - closes[-6]) / closes[-6] * 100 if len(closes) > 5 else 0

    c5_conditions = [
        ("EMA alignment score ≥3 (trend mạnh)", ema_align >= 3),
        ("Momentum 5 phiên dương (>1%)", mom5 > 1.0),
        ("OBV trend UP (tiền vào)", obv_trend == "UP"),
        ("RSI > 50 (xu hướng tăng)", rsi14 > 50),
        ("MACD histogram dương và tăng", hist_val is not None and hist_val > 0 and hist_val > hist_prev),
    ]
    c5_score = sum(1 for _, v in c5_conditions if v)
    c5_pct   = int(c5_score / len(c5_conditions) * 100)
    if c5_score >= 4:   c5_signal = "STRONG BUY 🟢🟢"
    elif c5_score == 3: c5_signal = "BUY 🟢"
    elif c5_score == 2: c5_signal = "NEUTRAL ⚪"
    elif c5_score == 1: c5_signal = "SELL 🔴"
    else:               c5_signal = "STRONG SELL 🔴🔴"

    # ══════════════════════════════════════════════════════════
    # TỔNG HỢP — Weighted Score
    # ══════════════════════════════════════════════════════════
    # Weights: C1=25%, C2=20%, C3=20%, C4=20%, C5=15%
    weights = [0.25, 0.20, 0.20, 0.20, 0.15]
    scores  = [c1_pct, c2_pct, c3_pct, c4_pct, c5_pct]
    total_score = int(sum(w * s for w, s in zip(weights, scores)))

    # Quyết định tổng hợp
    if total_score >= 70:
        overall = "STRONG BUY 🔥"
        action  = "MUA MẠNH"
        emoji   = "🟢🟢🟢"
    elif total_score >= 55:
        overall = "BUY"
        action  = "MUA"
        emoji   = "🟢🟢"
    elif total_score >= 45:
        overall = "WATCH"
        action  = "THEO DÕI"
        emoji   = "🟡"
    elif total_score >= 30:
        overall = "SELL"
        action  = "BÁN"
        emoji   = "🔴🔴"
    else:
        overall = "STRONG SELL"
        action  = "BÁN MẠNH"
        emoji   = "🔴🔴🔴"

    # ══════════════════════════════════════════════════════════
    # LONG-TERM TRADE DECISION ENGINE v2
    # Triết lý: không dùng SL/TP cố định.
    # Mua theo vùng EMA hỗ trợ (3 tầng DCA).
    # Giữ khi xu hướng còn tích cực.
    # Thoát chỉ khi ≥3/5 tín hiệu đảo chiều xác nhận.
    # ══════════════════════════════════════════════════════════

    # ── 1. Trạng thái xu hướng ──
    trend_strong    = bool(st_dir == "BULL" and ema9 > ema20 > ema50 and latest_close > ema9)
    trend_confirmed = bool(st_dir == "BULL" and ema9 > ema20 > ema50)
    trend_weak      = bool(latest_close > ema50 and (ema9 > ema20 or st_dir == "BULL"))
    money_flow_in   = bool(obv_trend == "UP" and cmf20 > 0)
    money_flow_out  = bool(obv_trend == "DOWN" and cmf20 < 0)

    # ── 2. Điều kiện GIỮ VỮNG — không bán dù có pullback ──
    # Dài hạn: pullback về EMA là cơ hội mua thêm, không phải bán
    hold_ok = bool(
        st_dir == "BULL"              # xu hướng chính vẫn tăng
        and latest_close > ema50      # giá trên đường trung bình dài hạn
        and rsi14 > 35                # momentum không sụp đổ (nới rộng cho DH)
        and obv_trend == "UP"         # tổ chức chưa rút tiền
    )

    # ── 3. Hệ thống 5 tín hiệu THOÁT dài hạn ──
    # Cần ≥ 3/5 để xác nhận đảo chiều — tránh false signal ngắn hạn
    exit_signals = {
        "supertrend_bear":     bool(st_dir == "BEAR"),
        "death_cross":         bool(ema20 < ema50),
        "price_below_ema50":   bool(latest_close < ema50),
        "rsi_breakdown":       bool(rsi14 < 40 and mom5 < -3),
        "money_flow_out":      bool(money_flow_out and mfi14 < 45),
    }
    reversal_count  = sum(exit_signals.values())
    trend_reversed  = reversal_count >= 3

    # ── 4. Smart DCA — Hybrid Regime + Confluence ──
    # Weekly resample cho Pivot Points
    try:
        df_weekly_rs = df.copy()
        df_weekly_rs['time'] = pd.to_datetime(df_weekly_rs['time'])
        df_weekly_rs = df_weekly_rs.set_index('time').resample('W-FRI').agg({
            'open': 'first', 'high': 'max', 'low': 'min',
            'close': 'last', 'volume': 'sum'
        }).dropna().reset_index()
    except Exception:
        df_weekly_rs = None

    vwap_daily = calc_vwap_rolling(closes, highs, lows, volumes, 20)

    smart_dca = calc_smart_dca(
        closes, highs, lows, volumes, df_weekly_rs,
        ema9, ema20, ema50, bb_lower, bb_mid, bb_upper,
        st_val, vwap_daily
    )

    best_zone_str = smart_dca["best_zone_str"]
    best_dist     = smart_dca["best_zone_dist_pct"]
    dist_note_dca = (
        f"Giá đang ở trong vùng DCA" if abs(best_dist) < 1
        else f"Cách vùng DCA: {best_dist:+.1f}%"
    )
    dist_ema20_pct = round((latest_close - ema20) / ema20 * 100, 1)
    dist_ema50_pct = round((latest_close - ema50) / ema50 * 100, 1)

    # ── 5. Vùng hỗ trợ & kháng cự động ──
    # Hỗ trợ = mức GIÁ DƯỚI giá hiện tại; kháng cự = mức TRÊN giá hiện tại
    price = latest_close
    candidates = sorted(set([
        round(ema20, 2),
        round(ema50, 2),
        round(st_val, 2) if st_val else round(ema20 * 0.97, 2),
        round(bb_lower, 2) if bb_lower else round(ema50 * 0.95, 2),
        round(bb_upper, 2) if bb_upper else round(price * 1.05, 2),
        round(bb_mid, 2)   if bb_mid   else round(price * 1.02, 2),
    ]))
    supports    = sorted([x for x in candidates if x < price * 0.999], reverse=True)  # gần nhất trước
    resistances = sorted([x for x in candidates if x > price * 1.001])                # gần nhất trước

    support_1   = supports[0]    if len(supports) > 0 else round(price * 0.95, 2)
    support_2   = supports[1]    if len(supports) > 1 else round(price * 0.90, 2)
    support_3   = supports[2]    if len(supports) > 2 else round(price * 0.85, 2)
    resistance  = resistances[0] if len(resistances) > 0 else round(price * 1.05, 2)

    # ── 6. Ra quyết định dài hạn ──
    if trend_reversed:
        decision      = "THOAT_LENH"
        decision_vi   = "🔴🔴 THOÁT LỆNH DÀI HẠN"
        decision_note = f"Đảo chiều xác nhận {reversal_count}/5 tín hiệu — xu hướng tăng đã kết thúc"
        action_detail = "Giảm tỷ trọng mạnh hoặc thoát toàn bộ. Theo dõi để tái tích lũy ở vùng đáy mới"

    elif reversal_count == 2:
        decision      = "CANH_THOAT"
        decision_vi   = "🟠 CẢNH BÁO — SẴN SÀNG THOÁT"
        decision_note = f"2/5 tín hiệu đảo chiều — chưa xác nhận nhưng rủi ro tăng cao"
        action_detail = f"Không mua thêm. Đặt ngưỡng thoát nếu thêm 1 tín hiệu nữa xác nhận"

    elif total_score >= 70 and trend_strong and money_flow_in:
        decision      = "MUA_MANH"
        decision_vi   = "🟢🟢 MUA MẠNH / TĂNG TỶ TRỌNG"
        decision_note = "Xu hướng rất mạnh, dòng tiền vào rõ ràng — thời điểm tốt để tích lũy"
        action_detail = f"DCA vào vùng tốt nhất: {best_zone_str}"

    elif total_score >= 55 and trend_confirmed:
        decision      = "TICH_LUY"
        decision_vi   = "🟢 TÍCH LŨY / MUA THÊM"
        decision_note = "Xu hướng tăng xác nhận — tích lũy theo DCA khi giá về vùng hội tụ"
        action_detail = f"DCA vào vùng tốt nhất: {best_zone_str}"

    elif hold_ok:
        decision      = "NAM_GIU"
        decision_vi   = "⚪ NẮM GIỮ VỮNG"
        decision_note = "Chỉ báo tích cực, xu hướng còn nguyên — không có lý do bán"
        action_detail = f"Tiếp tục giữ. DCA thêm nếu giá về vùng: {best_zone_str}"

    elif trend_weak and not money_flow_out:
        decision      = "THEO_DOI"
        decision_vi   = "🟡 THEO DÕI — GIỮ CÓ ĐIỀU KIỆN"
        decision_note = "Xu hướng yếu dần nhưng chưa đảo chiều — không mua thêm, theo dõi chặt"
        action_detail = f"Giữ nếu chưa phá EMA50 ({ema50:,.2f}). Sẵn sàng thoát nếu thêm tín hiệu xấu"

    else:
        decision      = "TRANH_XA"
        decision_vi   = "🔴 TRÁNH XA — CHƯA VÀO"
        decision_note = "Xu hướng không rõ hoặc đang tích lũy đáy — chưa phải thời điểm"
        action_detail = f"Chờ giá ổn định và xác nhận lại trên EMA50 ({ema50:,.2f}) với volume tốt"

    # ── 7. Trạng thái giữ ──
    if hold_ok and not trend_reversed:
        hold_status = "✅ GIỮ VỮNG"
        hold_reason = "SuperTrend BULL + giá trên EMA50 + OBV tăng → chưa có lý do bán"
    elif reversal_count == 1:
        hold_status = "⚠️ GIỮ CÓ ĐIỀU KIỆN"
        hold_reason = f"1/5 tín hiệu cảnh báo — theo dõi chặt, chưa phải thoát"
    elif reversal_count == 2:
        hold_status = "🟠 RỦI RO CAO — CÂN NHẮC THOÁT"
        hold_reason = f"2/5 tín hiệu xấu — chuẩn bị thoát nếu thêm 1 tín hiệu nữa"
    elif trend_reversed:
        hold_status = "❌ THOÁT LỆNH"
        hold_reason = f"3+/5 tín hiệu đảo chiều — xu hướng dài hạn đã phá vỡ"
    else:
        hold_status = "⚠️ THEO DÕI"
        hold_reason = "Chỉ báo chưa rõ xu hướng — không mua thêm, theo dõi"

    # ── 8. Mô tả chi tiết 5 tín hiệu thoát ──
    exit_detail = {
        "SuperTrend đổi BEAR":              exit_signals["supertrend_bear"],
        f"Death cross EMA20 < EMA50":        exit_signals["death_cross"],
        f"Giá dưới EMA50 ({ema50:,.2f})":    exit_signals["price_below_ema50"],
        f"RSI<40 & Momentum<-3%":            exit_signals["rsi_breakdown"],
        f"OBV giảm & MFI<45":               exit_signals["money_flow_out"],
    }

    trade_plan = {
        # Quyết định chính
        "decision":           decision,
        "decision_vi":        decision_vi,
        "decision_note":      decision_note,
        "action_detail":      action_detail,
        # Trạng thái giữ
        "hold_status":        hold_status,
        "hold_reason":        hold_reason,
        "hold_ok":            bool(hold_ok),
        # Smart DCA — vùng tốt nhất (tham chiếu từ smart_dca)
        "dca_best_zone":      best_zone_str,
        "dca_dist_pct":       best_dist,
        "dca_current_zone":   dist_note_dca,
        "dca_note":           f"Regime: {smart_dca['regime']} | {len(smart_dca['best_zone_sources'])} chỉ báo hội tụ",
        # Khoảng cách giá vs EMA (phụ)
        "dist_ema20_pct":     dist_ema20_pct,
        "dist_ema50_pct":     dist_ema50_pct,
        "dist_note":          f"Cách EMA20: {dist_ema20_pct:+.1f}% | Cách EMA50: {dist_ema50_pct:+.1f}%",
        # Vùng hỗ trợ / kháng cự
        "support_1":          support_1,
        "support_2":          support_2,
        "support_3":          support_3,
        "resistance":         resistance,
        "levels_note":        f"Hỗ trợ: {support_1:,.2f} → {support_2:,.2f} → {support_3:,.2f} | Kháng cự: {resistance:,.2f}",
        # Hệ thống 5 tín hiệu thoát
        "reversal_count":     reversal_count,
        "reversal_detail":    {k: ("⚠️ XẤU" if v else "✅ OK") for k, v in exit_detail.items()},
        "trend_reversed":     bool(trend_reversed),
        "exit_threshold":     "Thoát khi ≥3/5 tín hiệu kích hoạt",
        # Chất lượng tín hiệu
        "trend_quality":      "MẠNH" if trend_strong else ("TỐT" if trend_confirmed else ("YẾU" if trend_weak else "XẤU")),
        "money_flow":         "VÀO" if money_flow_in else ("RA" if money_flow_out else "TRUNG LẬP"),
        "signal_quality":     f"{total_score}/100",
    }

    return {
        "symbol":      symbol,
        "date":        latest_date,
        "price":       latest_close,
        "change":      round(change, 2),
        "change_pct":  round(change_pct, 2),
        "volume":      int(latest_vol),
        "vol_ratio":   round(vol_ratio, 2),
        "rsi14":       rsi14,
        "macd":        round(macd_val, 3),
        "macd_signal": round(signal_val, 3),
        "macd_hist":   round(hist_val, 3) if hist_val else 0,
        "macd_cross":  "CROSS UP 🟢" if macd_cross_up else ("CROSS DOWN 🔴" if macd_cross_dn else "No cross"),
        "ema9":        round(ema9, 2),
        "ema20":       round(ema20, 2),
        "ema50":       round(ema50, 2),
        "bb_upper":    bb_upper,
        "bb_mid":      bb_mid,
        "bb_lower":    bb_lower,
        "bb_pos":      bb_pos,
        "atr14":       atr14,
        "obv_trend":   obv_trend,
        "obv_div":     bool(obv_bull_div),
        "vwap":        round(vwap, 2),
        "supertrend":  st_dir,
        "st_level":    st_val,
        "stoch_rsi_k": stoch_k,
        "cmf20":       round(cmf20, 3),
        "mfi14":       mfi14,
        "mom5d_pct":   round(mom5, 2),
        "combo1": {"name": "EMA Stack + MACD + Volume",    "signal": c1_signal, "score": c1_pct,
                   "conditions": {k: bool(v) for k, v in c1_conditions}},
        "combo2": {"name": "VWAP + OBV + Bollinger",       "signal": c2_signal, "score": c2_pct,
                   "conditions": {k: bool(v) for k, v in c2_conditions}},
        "combo3": {"name": "SuperTrend + Stoch RSI + CMF", "signal": c3_signal, "score": c3_pct,
                   "conditions": {k: bool(v) for k, v in c3_conditions}},
        "combo4": {"name": "MACD Cross + RSI + MFI",       "signal": c4_signal, "score": c4_pct,
                   "conditions": {k: bool(v) for k, v in c4_conditions}},
        "combo5": {"name": "Multi-TF Trend Strength",      "signal": c5_signal, "score": c5_pct,
                   "conditions": {k: bool(v) for k, v in c5_conditions}},
        "total_score": total_score,
        "overall":     overall,
        "action":      action,
        "emoji":       emoji,
        "entry":       latest_close,
        "atr14":       atr14,
        "trade_plan":  trade_plan,
        "mtf":         calc_mtf(df, total_score, symbol),
        "confluence":  calc_confluence(
                           closes, highs, lows,
                           df['open'].astype(float).tolist(),
                           volumes,
                           calc_rsi_series(closes, 14),
                           ema20, ema50, symbol
                       ),
        "smart_dca":   smart_dca,
    }, None

# ══════════════════════════════════════════════════════════════
# ENDPOINTS
# ══════════════════════════════════════════════════════════════

@app.route("/")
def health():
    return jsonify({"status": "ok", "message": "Stock API v2 — Combo Engine ready"})

@app.route("/combo/<symbol>")
def get_combo(symbol):
    result, err = run_combo_analysis(symbol)
    if err:
        return jsonify({"error": err}), 404
    return jsonify(result)

@app.route("/vnindex")
def get_vnindex():
    try:
        r = requests.get(
            "https://iboard-query.ssi.com.vn/v2/stock/snapshot?market=HOSE&symbol=VNIndex",
            headers=HEADERS, timeout=10, verify=False)
        if r.status_code == 200:
            d = r.json()
            data = d.get("data", d)
            if isinstance(data, list) and data:
                data = data[0]
            close = float(data.get("lastPrice") or data.get("close") or 0)
            ref   = float(data.get("refPrice") or data.get("referencePrice") or close)
            open_ = float(data.get("openPrice") or data.get("open") or close)
            change = close - ref
            pct    = (change / ref * 100) if ref else 0
            if close > 0:
                return jsonify({
                    "symbol": "VNINDEX", "source": "SSI",
                    "close": round(close, 2), "open": round(open_, 2),
                    "change": round(change, 2), "change_pct": round(pct, 2),
                    "trend": "TANG" if change >= 0 else "GIAM",
                    "date": datetime.now().strftime("%Y-%m-%d")
                })
    except: pass
    return jsonify({"error": "Khong lay duoc VNINDEX"}), 500

@app.route("/stock/<symbol>")
def get_stock(symbol):
    try:
        symbol = symbol.upper()
        df = get_stock_data(symbol, 40)
        if df is None or df.empty:
            return jsonify({"error": f"Khong tim thay {symbol}"}), 404
        latest = df.iloc[-1]
        prev   = df.iloc[-2] if len(df) > 1 else latest
        closes = df["close"].astype(float).tolist()
        change = float(latest["close"]) - float(prev["close"])
        pct    = (change / float(prev["close"])) * 100
        rsi    = calc_rsi(closes)
        ma20   = sum(closes[-20:]) / min(len(closes), 20)
        return jsonify({
            "symbol": symbol,
            "close": float(latest["close"]), "open": float(latest["open"]),
            "high": float(latest["high"]),   "low": float(latest["low"]),
            "volume": float(latest["volume"]),
            "change": round(change, 2), "change_pct": round(pct, 2),
            "date": str(latest["time"]), "rsi_14": round(rsi, 2),
            "ma20": round(ma20, 2),
            "trend": "TANG" if change >= 0 else "GIAM",
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/sectors")
def get_sectors():
    try:
        sector_map = {
            "Ngan hang":    ["VCB","BID","TCB","MBB"],
            "Dau khi":      ["PVT","GAS","PVS","BSR"],
            "Bat dong san": ["VHM","VIC","KDH"],
            "Cong nghe":    ["FPT","CMG"],
            "Thep":         ["HPG","NKG"],
            "Phan bon":     ["DCM","DPM"],
            "Cang bien":    ["GMD","HAH"],
        }
        results = []
        for sector, syms in sector_map.items():
            changes, vol = [], 0
            for sym in syms:
                try:
                    df = get_stock_data(sym, 5)
                    if df is not None and len(df) >= 2:
                        c = float(df.iloc[-1]["close"])
                        p = float(df.iloc[-2]["close"])
                        changes.append((c-p)/p*100)
                        vol += float(df.iloc[-1]["volume"])
                except: continue
            if changes:
                avg = sum(changes)/len(changes)
                results.append({
                    "sector": sector, "avg_change_pct": round(avg, 2),
                    "total_volume": int(vol),
                    "signal": "DONG TIEN VAO" if avg>0.5 else ("DONG TIEN RA" if avg<-0.5 else "TRUNG LAP")
                })
        results.sort(key=lambda x: x["avg_change_pct"], reverse=True)
        return jsonify({"sectors": results, "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M")})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/macro")
def get_macro():
    try:
        av_key = os.environ.get("ALPHA_VANTAGE_KEY","")
        result = {}
        if av_key:
            r = requests.get(
                f"https://www.alphavantage.co/query?function=CURRENCY_EXCHANGE_RATE&from_currency=XAU&to_currency=USD&apikey={av_key}",
                timeout=10, verify=False)
            d = r.json().get("Realtime Currency Exchange Rate",{})
            if d:
                result["XAUUSD"] = {"price": float(d["5. Exchange Rate"]), "updated": d["6. Last Refreshed"]}
        result["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/news")
def get_news():
    try:
        key = os.environ.get("NEWS_API_KEY","")
        if not key: return jsonify({"error": "Chua co NEWS_API_KEY"}), 400
        r = requests.get(
            f"https://newsapi.org/v2/everything?q=chung+khoan+Viet+Nam&language=vi&sortBy=publishedAt&pageSize=5&apiKey={key}",
            timeout=10, verify=False)
        arts = [{"title": a["title"], "source": a["source"]["name"], "published": a["publishedAt"][:10]}
                for a in r.json().get("articles",[])]
        return jsonify({"news": arts})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ══════════════════════════════════════════════════════════════
# AUTO DEPLOY — GitHub Webhook
# ══════════════════════════════════════════════════════════════
import subprocess, hmac, hashlib
from flask import request as flask_request

@app.route("/deploy", methods=["POST"])
def deploy():
    """GitHub Webhook endpoint — tự động pull + restart khi có push mới"""
    deploy_secret = os.environ.get("DEPLOY_SECRET", "")
    if not deploy_secret:
        return jsonify({"error": "DEPLOY_SECRET chưa được cấu hình"}), 500

    # Xác thực chữ ký từ GitHub
    sig_header = flask_request.headers.get("X-Hub-Signature-256", "")
    body = flask_request.get_data()
    expected = "sha256=" + hmac.new(
        deploy_secret.encode(), body, hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(sig_header, expected):
        return jsonify({"error": "Unauthorized — sai secret"}), 401

    # Chỉ deploy khi push vào branch main
    payload = flask_request.get_json(silent=True) or {}
    ref = payload.get("ref", "")
    if ref and ref != "refs/heads/main":
        return jsonify({"status": "ignored", "ref": ref}), 200

    # Pull code mới và restart cả 2 services
    result = subprocess.run(
        "cd /root/stock-api && git pull origin main && "
        "systemctl restart stockapi stockbot",
        shell=True, capture_output=True, text=True, timeout=60
    )

    if result.returncode == 0:
        return jsonify({
            "status":  "✅ Deploy thành công",
            "output":  result.stdout.strip()[-500:],
            "ref":     ref,
        }), 200
    else:
        return jsonify({
            "status": "❌ Deploy thất bại",
            "error":  result.stderr.strip()[-500:],
            "output": result.stdout.strip()[-200:],
        }), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
