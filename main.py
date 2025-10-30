import os
import requests
import pandas as pd
from datetime import datetime

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
INTERVAL = "1h"
LIMIT = 200
FUTURES_COINS = ["BTC", "ETH", "SOL", "BNB", "DOGE"]

def send_telegram(message):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("❌ Telegram bilgileri eksik!")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, data={"chat_id": CHAT_ID, "text": message})
    except:
        pass

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
    except:
        return None

def detect_signals(df):
    df["ema_fast"] = df["close"].ewm(span=9).mean()
    df["ema_slow"] = df["close"].ewm(span=21).mean()
    df["rsi"] = 100 - (100 / (1 + df["close"].pct_change().rolling(14).mean() / df["close"].pct_change().rolling(14).std()))
    df["vol_avg"] = df["volume"].rolling(10).mean()

    last = df.iloc[-1]
    prev = df.iloc[-2]

    signals = []

    # EMA CROSS
    if last["ema_fast"] > last["ema_slow"] and prev["ema_fast"] <= prev["ema_slow"]:
        if last["rsi"] > 48 and last["volume"] > last["vol_avg"] * 1.2:
            signals.append("🟢 BUY (EMA Cross + RSI + Hacim)")

    if last["ema_fast"] < last["ema_slow"] and prev["ema_fast"] >= prev["ema_slow"]:
        if last["rsi"] < 52 and last["volume"] > last["vol_avg"] * 1.2:
            signals.append("🔴 SELL (EMA Cross + RSI + Hacim)")

    # Extra sinyal: RSI aşırı durum
    if last["rsi"] < 30:
        signals.append("⚠️ RSI Aşırı Satış (Dip Yakın)")
    if last["rsi"] > 70:
        signals.append("⚠️ RSI Aşırı Alım (Tepe Yakın)")

    return signals

def main():
    print(f"=== Mum Botu Aktif {datetime.now()} ===")

    for coin in ["BTCUSDT"] + [f"{c}_USDT" for c in FUTURES_COINS]:
        df = get_futures_klines(coin.replace("_USDT",""))
        if df is None or len(df) < 60:
            continue

        signals = detect_signals(df)
        if signals:
            msg = f"📈 {coin} ({INTERVAL})\n" + "\n".join(signals)
            send_telegram(msg)
            print("Sinyal gönderildi ✅", msg)
        else:
            print(f"{coin}: Sinyal yok ❌")

if __name__ == "__main__":
    main()
