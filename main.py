#!/usr/bin/env python3
import os
import time
import requests
import math
import traceback
from datetime import datetime
import pandas as pd

# ====== CONFIG ======
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

# Majör coin listesi (spot Market on Binance)
COINS = [
    "BTCUSDT","ETHUSDT","BNBUSDT","SOLUSDT","XRPUSDT","ADAUSDT",
    "AVAXUSDT","DOGEUSDT","DOTUSDT","LINKUSDT","MATICUSDT","LTCUSDT"
]

# Timeframes used (multi-timeframe)
TF_PRIMARY = "4h"   # primary signal timeframe
TF_CONFIRM = "1h"   # confirm shorter timeframe
TF_LONG = "1d"      # long term trend

# indicator / detector params
MIN_ROWS = 50
VOL_MULT = 1.6      # hacim spike eşiği
EMA_FAST = 9
EMA_SLOW = 21
EMA_LONG = 200

# SL/TP suggestion (yüzde)
SL_PCT = 0.01   # %1 stop loss önerisi
TP1_PCT = 0.02  # %2 ilk hedef
TP2_PCT = 0.05  # %5 ikinci hedef

# HTTP timeout / retries
REQUEST_TIMEOUT = 8
MAX_RETRIES = 3

# GitHub Actions çalıştırmaları kısa olabilir, bu script tek çalıştırma için tasarlandı.
# Scheduling GitHub Actions ile yapılacak (aşağıda workflow var).

# ====== HELPERS ======
def send_telegram(text):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("Telegram secrets eksik. Mesaj atılamıyor.")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}
    try:
        r = requests.post(url, data=payload, timeout=10)
        print(f"Telegram status: {r.status_code}")
        return r.status_code == 200
    except Exception as e:
        print("Telegram gönderim hatası:", e)
        return False

def fetch_klines_binance(symbol: str, interval: str, limit: int = 500):
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    for i in range(MAX_RETRIES):
        try:
            r = requests.get(url, timeout=REQUEST_TIMEOUT)
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, list) and len(data) > 0:
                    df = pd.DataFrame(data, columns=[
                        "open_time","open","high","low","close","volume",
                        "close_time","quote_asset_volume","trades","taker_base","taker_quote","ignore"
                    ])
                    df = df[["open","high","low","close","volume"]].astype(float)
                    return df
            else:
                # short sleep and retry
                time.sleep(0.5)
        except Exception:
            time.sleep(0.5)
    return None

# Indicators
def ema(series, span):
    return series.ewm(span=span, adjust=False).mean()

def macd(series, fast=12, slow=26, signal=9):
    fast_ema = series.ewm(span=fast, adjust=False).mean()
    slow_ema = series.ewm(span=slow, adjust=False).mean()
    macd_line = fast_ema - slow_ema
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist = macd_line - signal_line
    return macd_line, signal_line, hist

def rsi(series, period=14):
    delta = series.diff()
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)
    ma_up = up.rolling(period, min_periods=1).mean()
    ma_down = down.rolling(period, min_periods=1).mean().replace(0, 1e-9)
    rs = ma_up / ma_down
    return 100 - (100 / (1 + rs))

def detect_engulfing(df):
    # simple last engulfing detector
    if len(df) < 2:
        return None
    prev = df.iloc[-2]
    last = df.iloc[-1]
    # bullish engulfing
    if (prev['close'] < prev['open']) and (last['close'] > last['open']) and (last['close'] - last['open'] > prev['open'] - prev['close']):
        return ("bull", float(last['low']))
    # bearish engulfing
    if (prev['close'] > prev['open']) and (last['close'] < last['open']) and (last['open'] - last['close'] > prev['close'] - prev['open']):
        return ("bear", float(last['high']))
    return None

def suggest_sl_tp(price, side):
    # side: "BUY" or "SELL"
    if side == "BUY":
        sl = price * (1 - SL_PCT)
        tp1 = price * (1 + TP1_PCT)
        tp2 = price * (1 + TP2_PCT)
    else:
        sl = price * (1 + SL_PCT)
        tp1 = price * (1 - TP1_PCT)
        tp2 = price * (1 - TP2_PCT)
    return round(sl, 6), round(tp1, 6), round(tp2, 6)

# ====== ANALYZE SYMBOL (multi-timeframe + human message) ======
def analyze_symbol(symbol):
    try:
        df_primary = fetch_klines_binance(symbol, TF_PRIMARY, limit=300)
        df_confirm = fetch_klines_binance(symbol, TF_CONFIRM, limit=200)
        df_long = fetch_klines_binance(symbol, TF_LONG, limit=200)
        # require at least primary present
        if df_primary is None or len(df_primary) < MIN_ROWS:
            return None  # no data -> silent skip

        # compute indicators on primary
        df = df_primary.copy()
        df['ema_fast'] = ema(df['close'], EMA_FAST)
        df['ema_slow'] = ema(df['close'], EMA_SLOW)
        df['ema_long'] = ema(df['close'], EMA_LONG)
        df['macd_line'], df['macd_signal'], df['macd_hist'] = macd(df['close'])
        df['rsi'] = rsi(df['close'])
        df['vol_avg'] = df['volume'].rolling(20, min_periods=1).mean()

        last = df.iloc[-1]
        prev = df.iloc[-2]

        # conditions
        ema_cross_up = (last['ema_fast'] > last['ema_slow']) and (prev['ema_fast'] <= prev['ema_slow'])
        ema_cross_down = (last['ema_fast'] < last['ema_slow']) and (prev['ema_fast'] >= prev['ema_slow'])
        macd_bull = last['macd_line'] > last['macd_signal']
        macd_bear = last['macd_line'] < last['macd_signal']
        rsi_val = last['rsi'] if not pd.isna(last['rsi']) else 50
        vol_spike = last['volume'] > VOL_MULT * last['vol_avg']
        engulf = detect_engulfing(df)

        # confirm on shorter timeframe if available (reduces fakeouts)
        confirm_ok = False
        if df_confirm is not None and len(df_confirm) >= 20:
            d = df_confirm.copy()
            d['ema_fast'] = ema(d['close'], EMA_FAST)
            d['ema_slow'] = ema(d['close'], EMA_SLOW)
            # if short tf trend aligns with main signal direction, confirm
            if ema_cross_up and d['ema_fast'].iloc[-1] > d['ema_slow'].iloc[-1]:
                confirm_ok = True
            if ema_cross_down and d['ema_fast'].iloc[-1] < d['ema_slow'].iloc[-1]:
                confirm_ok = True

        # long term bias
        long_bias = None
        if df_long is not None and len(df_long) >= 30:
            dl = df_long.copy()
            dl['ema_long'] = ema(dl['close'], EMA_LONG)
            long_bias = "UP" if dl['close'].iloc[-1] > dl['ema_long'].iloc[-1] else "DOWN"

        # decision logic (human-like)
        comments = []
        suggestion = "NEUTRAL"
        score = 0

        # trend comment
        if last['ema_fast'] > last['ema_long']:
            comments.append("Kısa-orta vadede trend yukarı")
            score += 1
        else:
            comments.append("Kısa-orta vadede trend aşağı")
            score -= 1

        # ema cross weight
        if ema_cross_up:
            comments.append("EMA kesişimi: yukarı (al sinyali)")
            score += 2
        if ema_cross_down:
            comments.append("EMA kesişimi: aşağı (sat sinyali)")
            score -= 2

        # MACD / RSI
        if macd_bull:
            comments.append("MACD pozitif")
            score += 1
        else:
            comments.append("MACD negatif")
            score -= 1

        if rsi_val > 70:
            comments.append(f"RSI yüksek ({rsi_val:.0f}) — aşırı alım riski")
            score -= 1
        elif rsi_val < 30:
            comments.append(f"RSI düşük ({rsi_val:.0f}) — aşırı satış bölgesi")
            score += 1
        else:
            comments.append(f"RSI: {rsi_val:.0f}")

        # volume
        if vol_spike:
            comments.append("Hacim desteği mevcut (vol spike)")
            score += 2
        else:
            comments.append("Hacim zayıf")
            # small penalty/bonus none

        # orderblock / engulfing
        if engulf is not None:
            t, lvl = engulf
            if t == "bull":
                comments.append("Engulfing (bull) tespit edildi — alıcı baskısı")
                score += 2
            else:
                comments.append("Engulfing (bear) tespit edildi — satış baskısı")
                score -= 2

        # confirmation
        if confirm_ok:
            comments.append("Kısa zaman çerçevesi teyit ediyor")
            score += 1
        else:
            # minor note only
            comments.append("Kısa zaman çerçevesi teyidi yok / kararsız")

        # long bias note
        if long_bias:
            comments.append(f"Uzun vadeli eğilim: {long_bias}")

        # final suggestion from score & conditions
        if score >= 3:
            suggestion = "BUY"
        elif score <= -3:
            suggestion = "SELL"
        else:
            suggestion = "NEUTRAL"

        # strengthen if strong confluence
        strong_buy = (ema_cross_up and macd_bull and vol_spike and score >= 3)
        strong_sell = (ema_cross_down and macd_bear and vol_spike and score <= -3)

        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
        price = float(last['close'])

        # SL/TP suggestion
        if suggestion == "BUY":
            sl, tp1, tp2 = suggest_sl_tp(price, "BUY")
            human = f"<b>{symbol} — BUY signal</b>\n{now}\nFiyat: {price:.6f}\nÖzet: {', '.join(comments[:3])}...\nÖneri: BUY\nSL: {sl}  TP1: {tp1}  TP2: {tp2}"
        elif suggestion == "SELL":
            sl, tp1, tp2 = suggest_sl_tp(price, "SELL")
            human = f"<b>{symbol} — SELL signal</b>\n{now}\nFiyat: {price:.6f}\nÖzet: {', '.join(comments[:3])}...\nÖneri: SELL\nSL: {sl}  TP1: {tp1}  TP2: {tp2}"
        else:
            human = f"<b>{symbol} — NEUTRAL</b>\n{now}\nFiyat: {price:.6f}\nKısa özet: {', '.join(comments[:4])}..."

        # if strong confluence, add emphasis
        if strong_buy:
            human += "\n🔥 <b>STRONG BUY (high confidence)</b>"
        if strong_sell:
            human += "\n🔥 <b>STRONG SELL (high confidence)</b>"

        # only send when strong or clear signal (avoid spam): send if strong or suggestion is BUY/SELL with vol spike or cross
        should_alert = False
        if strong_buy or strong_sell:
            should_alert = True
        elif suggestion in ("BUY","SELL") and (vol_spike or ema_cross_up or ema_cross_down or engulf is not None):
            should_alert = True
        else:
            should_alert = False

        return {
            "symbol": symbol,
            "suggestion": suggestion,
            "message": human,
            "should_alert": should_alert,
            "score": score
        }

    except Exception as e:
        print("analyze_symbol hata:", e)
        traceback.print_exc()
        return None

# ====== MAIN RUN ======
def main():
    results = []
    for s in COINS:
        res = analyze_symbol(s)
        if res is None:
            continue
        results.append(res)

    # Compose output: send per-signal or a digest
    alerts = [r for r in results if r['should_alert']]
    digest_lines = []
    for r in results:
        digest_lines.append(f"{r['symbol']}: {r['suggestion']} (score {r['score']})")

    # Send digest occasionally (only when there is any alert)
    if alerts:
        # send each alert as its own message (clear, timely)
        for a in alerts:
            send_telegram(a['message'])
            time.sleep(0.8)  # small pause
        # send short digest after alerts
        digest_msg = "<b>Quick digest:</b>\n" + "\n".join(digest_lines)
        send_telegram(digest_msg)
    else:
        # no alerts: do not spam. But optionally you can send a quiet daily summary — we skip here.
        print("No alerts this run. Summary (not sent):")
        print("\n".join(digest_lines))

if __name__ == "__main__":
    main()
