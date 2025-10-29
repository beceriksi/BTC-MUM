import os
import requests
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from io import BytesIO
from datetime import datetime

# =================== Settings ===================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
INTERVAL = "1h"
LIMIT = 200
FUTURES_COINS = ["BTC", "ETH", "SOL", "BNB", "DOGE"]

# =================== Telegram ===================
def send_message(msg):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("Telegram bilgileri eksik.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, data={"chat_id": CHAT_ID, "text": msg})
    except Exception as e:
        print("Telegram hatası:", e)

def send_photo(buf, coin):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
    files = {"photo": buf}
    data = {"chat_id": CHAT_ID, "caption": f"{coin} Mum Grafiği"}
    try:
        requests.post(url, files=files, data=data)
    except Exception as e:
        print("Foto gönderme hatası:", e)

# =================== RSI ===================
def calc_rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -1 * delta.clip(upper=0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi

# =================== Candlestick Patterns ===================
def detect_candlestick(df):
    last = df.iloc[-1]
    prev = df.iloc[-2]
    signals = []

    open_ = last["open"]
    close = last["close"]
    high = last["high"]
    low = last["low"]

    # Bullish Engulfing
    if close > open_ and prev["close"] < prev["open"] and close > prev["open"] and open_ < prev["close"]:
        signals.append("🟢 Bullish Engulfing → Olası Yükseliş")
    # Bearish Engulfing
    elif close < open_ and prev["close"] > prev["open"] and close < prev["open"] and open_ > prev["close"]:
        signals.append("🔴 Bearish Engulfing → Olası Düşüş")
    # Hammer
    elif close > open_ and (low + (close - open_)*2) > open_:
        signals.append("🟢 Hammer → Potansiyel dönüş")
    # Shooting Star
    elif close < open_ and (high - open_) > 2*(open_-close):
        signals.append("🔴 Shooting Star → Potansiyel düşüş")

    return signals

# =================== Data Fetch ===================
def get_futures_klines(symbol):
    url = f"https://www.mexc.com/open/api/v2/market/kline?symbol={symbol}_USDT&type={INTERVAL}&limit={LIMIT}"
    try:
        r = requests.get(url, timeout=10)
        data = r.json().get("data", [])
        if not data:
            return None
        df = pd.DataFrame(data, columns=["time","open","high","low","close","volume"])
        df[["open","high","low","close","volume"]] = df[["open","high","low","close","volume"]].astype(float)
        return df
    except Exception as e:
        print(f"API hatası ({symbol}): {e}")
        return None

def get_btc_klines():
    url = f"https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval={INTERVAL}&limit={LIMIT}"
    try:
        r = requests.get(url, timeout=10)
        data = r.json()
        df = pd.DataFrame(data, columns=[
            "open_time","open","high","low","close","volume","close_time",
            "quote_asset_volume","number_of_trades","taker_buy_base","taker_buy_quote","ignore"
        ])
        df["open"] = df["open"].astype(float)
        df["high"] = df["high"].astype(float)
        df["low"] = df["low"].astype(float)
        df["close"] = df["close"].astype(float)
        df["volume"] = df["volume"].astype(float)
        df["time"] = pd.to_datetime(df["open_time"], unit='ms')
        return df
    except Exception as e:
        print("BTC API hatası:", e)
        return None

# =================== Signal Detection ===================
def detect_signals(df):
    df['ma_fast'] = df['close'].rolling(9).mean()
    df['ma_slow'] = df['close'].rolling(21).mean()
    df['rsi'] = calc_rsi(df['close'])
    df['change'] = df['close'].pct_change()
    df['vol_avg'] = df['volume'].rolling(10).mean()

    last = df.iloc[-1]
    prev = df.iloc[-2]
    signals = []

    # MA+RSI
    if last['ma_fast'] > last['ma_slow'] and prev['ma_fast'] <= prev['ma_slow'] and last['rsi'] < 70:
        signals.append("🟢 BUY sinyali (MA+RSI)")
    elif last['ma_fast'] < last['ma_slow'] and prev['ma_fast'] >= prev['ma_slow'] and last['rsi'] > 30:
        signals.append("🔴 SELL sinyali (MA+RSI)")

    # Testere Formasyonu
    vol = df['change'].rolling(10).std().iloc[-1]
    trend = df['close'].diff().rolling(10).mean().iloc[-1]
    if vol > 0.015 and abs(trend) < 50:
        signals.append("⚙️ Testere Formasyonu Tespit Edildi")

    # Balina Satışı
    if -1 < last['change']*100 < 0 and last['volume'] > 5*last['vol_avg']:
        signals.append("🐋 Balina Satışı olabilir")

    # Hacim Patlaması
    if last['volume'] > 3*last['vol_avg']:
        signals.append("💥 Hacim Patlaması tespit edildi")

    # Candlestick Patterns
    cand_signals = detect_candlestick(df)
    if cand_signals:
        signals.extend(cand_signals)

    return signals

# =================== Candlestick Plot ===================
def plot_candles(df, coin):
    df_plot = df.iloc[-50:]
    fig, ax = plt.subplots(figsize=(12,6))

    df_plot['time'] = pd.to_datetime(df_plot.get("time", df_plot.get("open_time", None)), unit='ms', errors='coerce')

    for idx, row in df_plot.iterrows():
        color = 'green' if row['close'] >= row['open'] else 'red'
        ax.plot([row['time'], row['time']], [row['low'], row['high']], color='black')
        ax.add_patch(plt.Rectangle((mdates.date2num(row['time'])-0.01, row['open']),
                                   0.02, row['close']-row['open'], color=color))

    ax.xaxis_date()
    plt.title(f"{coin} Mum Grafiği")
    plt.tight_layout()
    buf = BytesIO()
    plt.savefig(buf, format='png')
    buf.seek(0)
    plt.close(fig)
    return buf

# =================== Main ===================
def main():
    all_coins = ["BTCUSDT"] + [f"{c}_USDT" for c in FUTURES_COINS]
    for coin in all_coins:
        if coin.startswith("BTC"):
            df = get_btc_klines()
        else:
            symbol = coin.split("_")[0]
            df = get_futures_klines(symbol)
        if df is None or len(df) < 50:
            continue
        signals = detect_signals(df)
        if signals:
            msg = f"{coin} ({INTERVAL}) Sinyalleri:\n" + "\n".join(signals)
            print(msg)
            send_message(msg)
            buf = plot_candles(df, coin)
            send_photo(buf, coin)
        else:
            print(f"{coin}: Sinyal Yok")

if __name__ == "__main__":
    main()
