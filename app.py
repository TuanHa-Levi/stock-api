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

    # ── Entry / SL / TP theo ATR ──
    entry   = latest_close
    sl      = round(entry - 1.5 * atr14, 2)
    sl_pct  = round((sl - entry) / entry * 100, 1)
    tp1     = round(entry + 2.0 * atr14, 2)
    tp2     = round(entry + 3.5 * atr14, 2)
    tp1_pct = round((tp1 - entry) / entry * 100, 1)
    tp2_pct = round((tp2 - entry) / entry * 100, 1)
    rr1     = round(abs(tp1_pct / sl_pct), 1) if sl_pct != 0 else 0

    # ══════════════════════════════════════════════════════════
    # TRADE DECISION ENGINE — Quyết định giao dịch cụ thể
    # ══════════════════════════════════════════════════════════
    entry_low  = round(entry - 0.3 * atr14, 2)
    entry_high = round(entry + 0.2 * atr14, 2)
    sl_trail   = round(max(sl, ema20 * 0.985), 2)

    trend_confirmed = bool(st_dir == "BULL" and ema9 > ema20 > ema50)
    money_flow_in   = bool(obv_trend == "UP" and cmf20 > 0)
    money_flow_out  = bool(obv_trend == "DOWN" and cmf20 < 0)
    trend_broken    = bool(st_dir == "BEAR" and latest_close < ema20)

    if total_score >= 70 and trend_confirmed and money_flow_in:
        decision      = "MUA_NGAY"
        decision_vi   = "🟢🟢 MUA NGAY"
        decision_note = "Tín hiệu đủ mạnh — vào lệnh MUA tại giá thị trường"
        wait_buy      = entry_low
        wait_buy_note = f"Mua ngay hoặc chờ pullback về {entry_low:,.2f}"
    elif total_score >= 55 and (trend_confirmed or bool(macd_cross_up)):
        decision      = "CHO_MUA"
        decision_vi   = "🟡 ĐẶT LỆNH CHỜ MUA"
        decision_note = "Xu hướng tốt nhưng chưa đủ xác nhận — đặt lệnh chờ"
        wait_buy      = entry_low
        wait_buy_note = f"Đặt lệnh chờ mua tại {entry_low:,.2f} (pullback về EMA/hỗ trợ)"
    elif total_score >= 45:
        decision      = "THEO_DOI"
        decision_vi   = "⚪ THEO DÕI — CHƯA VÀO LỆNH"
        decision_note = "Tín hiệu chưa rõ — chờ breakout xác nhận mới vào lệnh"
        wait_buy      = entry_high
        wait_buy_note = f"Chờ breakout vượt {entry_high:,.2f} kèm volume > {int(vol_ma20*1.5):,}"
    elif total_score >= 30 and trend_broken:
        decision      = "CHO_BAN"
        decision_vi   = "🔴 ĐẶT LỆNH CHỜ BÁN"
        decision_note = "Xu hướng xấu đi — bán nếu đang giữ, không mua mới"
        wait_buy      = None
        wait_buy_note = "Không mua — chờ xác nhận đáy và tín hiệu đảo chiều"
    else:
        decision      = "BAN_NGAY"
        decision_vi   = "🔴🔴 BÁN / KHÔNG THAM GIA"
        decision_note = "Tín hiệu xấu toàn diện — bán nếu đang giữ, tránh mua mới"
        wait_buy      = None
        wait_buy_note = "Không mua — đợi thị trường hồi phục và có tín hiệu mới"

    trade_plan = {
        "decision":           decision,
        "decision_vi":        decision_vi,
        "decision_note":      decision_note,
        "wait_buy":           wait_buy,
        "wait_buy_note":      wait_buy_note,
        "entry_zone_low":     entry_low,
        "entry_zone_high":    entry_high,
        "cut_loss":           sl,
        "cut_loss_trail":     sl_trail,
        "cut_loss_pct":       sl_pct,
        "cut_loss_note":      f"Cắt lỗ khi giá đóng cửa dưới {sl:,.2f} ({sl_pct:+.1f}%)",
        "take_profit_1":      tp1,
        "take_profit_1_pct":  tp1_pct,
        "take_profit_1_note": f"Chốt 50% vị thế tại {tp1:,.2f} (+{tp1_pct:.1f}%)",
        "take_profit_2":      tp2,
        "take_profit_2_pct":  tp2_pct,
        "take_profit_2_note": f"Chốt 50% còn lại tại {tp2:,.2f} (+{tp2_pct:.1f}%)",
        "rr_ratio":           rr1,
        "rr_note":            f"Rủi ro {abs(sl_pct):.1f}% — Lợi nhuận {tp1_pct:.1f}% (R:R = 1:{rr1})",
        "trigger_buy":        f"Nến đóng cửa trên {entry_high:,.2f} + Volume > {int(vol_ma20*1.3):,}",
        "trigger_sell":       f"Nến đóng cửa dưới {sl:,.2f} hoặc SuperTrend đổi BEAR",
        "trend_quality":      "TỐT" if trend_confirmed else ("TRUNG BÌNH" if total_score >= 45 else "XẤU"),
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
        "entry":       entry,
        "sl":          sl,
        "sl_pct":      sl_pct,
        "tp1":         tp1,
        "tp2":         tp2,
        "tp1_pct":     tp1_pct,
        "tp2_pct":     tp2_pct,
        "rr_ratio":    rr1,
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
