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

def get_stock_data(symbol, days=80):
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
# COMBO ENGINE — Tính điểm và tín hiệu 5 combo
# ══════════════════════════════════════════════════════════════

def run_combo_analysis(symbol):
    symbol = symbol.upper()
    df = get_stock_data(symbol, days=90)
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

    # ── 4. Vùng DCA 3 tầng — mua theo pullback ──
    # Tầng 1 — Mua lý tưởng: pullback về EMA20 (hỗ trợ ngắn hạn)
    dca_zone1_low  = round(ema20 * 0.99, 2)
    dca_zone1_high = round(ema20 * 1.01, 2)
    # Tầng 2 — Mua tốt: pullback về EMA50 (hỗ trợ trung hạn)
    dca_zone2_low  = round(ema50 * 0.99, 2)
    dca_zone2_high = round(ema50 * 1.01, 2)
    # Tầng 3 — Mua mạnh tay: về BB lower / vùng oversold (cơ hội DCA tốt nhất)
    dca_zone3_low  = round(bb_lower * 0.99, 2) if bb_lower else round(ema50 * 0.94, 2)
    dca_zone3_high = round(bb_lower * 1.01, 2) if bb_lower else round(ema50 * 0.96, 2)

    # Giá hiện tại đang ở tầng nào?
    dist_ema20_pct = round((latest_close - ema20) / ema20 * 100, 1)
    dist_ema50_pct = round((latest_close - ema50) / ema50 * 100, 1)
    if latest_close <= dca_zone3_high:
        dca_current_zone = "TẦNG 3 🔥 (vùng oversold — mua mạnh nhất)"
    elif latest_close <= dca_zone2_high:
        dca_current_zone = "TẦNG 2 ✅ (EMA50 — mua tốt)"
    elif latest_close <= dca_zone1_high:
        dca_current_zone = "TẦNG 1 ✅ (EMA20 — mua lý tưởng)"
    else:
        gap_to_ema20 = dist_ema20_pct
        dca_current_zone = f"Trên vùng DCA ({gap_to_ema20:+.1f}% so EMA20) — chờ pullback"

    # ── 5. Vùng hỗ trợ & kháng cự động ──
    support_1  = round(min(ema20, st_val if st_val else ema20), 2)
    support_2  = round(ema50, 2)
    support_3  = round(bb_lower, 2) if bb_lower else round(ema50 * 0.95, 2)
    resistance = round(bb_upper, 2) if bb_upper else round(latest_close * 1.05, 2)

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
        action_detail = f"Mua ngay hoặc chờ pullback về Tầng 1: {dca_zone1_low:,.2f}–{dca_zone1_high:,.2f}"

    elif total_score >= 55 and trend_confirmed:
        decision      = "TICH_LUY"
        decision_vi   = "🟢 TÍCH LŨY / MUA THÊM"
        decision_note = "Xu hướng tăng xác nhận — tích lũy theo DCA khi giá về vùng EMA"
        action_detail = f"DCA tại Tầng 1 ({dca_zone1_low:,.2f}–{dca_zone1_high:,.2f}) hoặc Tầng 2 ({dca_zone2_low:,.2f}–{dca_zone2_high:,.2f})"

    elif hold_ok:
        decision      = "NAM_GIU"
        decision_vi   = "⚪ NẮM GIỮ VỮNG"
        decision_note = "Chỉ báo tích cực, xu hướng còn nguyên — không có lý do bán"
        action_detail = f"Tiếp tục giữ. Có thể DCA thêm nếu giá về Tầng 2: {dca_zone2_low:,.2f}–{dca_zone2_high:,.2f}"

    elif trend_weak and not money_flow_out:
        decision      = "THEO_DOI"
        decision_vi   = "🟡 THEO DÕI — GIỮ CÓ ĐIỀU KIỆN"
        decision_note = "Xu hướng yếu dần nhưng chưa đảo chiều — không mua thêm, theo dõi chặt"
        action_detail = f"Giữ nếu chưa phá EMA50 ({support_2:,.2f}). Sẵn sàng thoát nếu thêm tín hiệu xấu"

    else:
        decision      = "TRANH_XA"
        decision_vi   = "🔴 TRÁNH XA — CHƯA VÀO"
        decision_note = "Xu hướng không rõ hoặc đang tích lũy đáy — chưa phải thời điểm"
        action_detail = f"Chờ giá ổn định và xác nhận lại trên EMA50 ({support_2:,.2f}) với volume tốt"

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
        # Vùng DCA 3 tầng
        "dca_zone1":          f"{dca_zone1_low:,.2f}–{dca_zone1_high:,.2f}",
        "dca_zone2":          f"{dca_zone2_low:,.2f}–{dca_zone2_high:,.2f}",
        "dca_zone3":          f"{dca_zone3_low:,.2f}–{dca_zone3_high:,.2f}",
        "dca_current_zone":   dca_current_zone,
        "dca_note":           f"Tầng 1 (EMA20) | Tầng 2 (EMA50) | Tầng 3 (BB Lower)",
        # Khoảng cách giá vs EMA
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

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
