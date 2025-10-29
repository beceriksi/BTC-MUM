import os
import requests
import pandas as pd
from datetime import datetime

# =================== Settings ===================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
INTERVAL = "1h"
LIMIT = 200
FUTURES_COINS = ["BTC", "ETH", "SOL", "BNB", "DOGE"]

# =================== Telegram Functions ===================
def send_telegram(message):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("❌ Telegram bilgileri eksik! Lütfen secretleri kontrol et.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        r = requests.post(url, data={"chat_id": CHAT_ID, "text": message})
        print(f"Telegram mesaj durumu: {r.status_code}")
        if r.status_code != 200:
            print("Hata mesajı:", r.text)
    except Exception as e:
        print("Telegram hatası:", e)

def send_test_message():
    try:
        print("🔹 Telegram test mesajı gönderiliyor...")
        send_telegram("✅ Mum Botu çalışıyor! Bu test mesajıdır.")
    except Exception as e:
        print(f"Test mesajı gönderilemedi: {e}")

# =================== Data Fetch ===================
def get_futures_klines(symbol):
    url = f"https://www.mexc.com/open/api/v2/market/kline?symbol={symbol}_USDT&type={INTERVAL}&limit={LIMIT}"
    try:
        r = requests.get(url, timeout=10)
        data = r.json().get("data", [])
        if not data:
            print(f"❌ {symbol} verisi alınamadı!")
            return None
        df = pd.DataFrame(data, columns=["time","open","high","low","close","volume"])
        df[["open","high","low","close","volume"]] = df[["open","high","low","close","volume"]].astype(float)
        return df
    except Exception as e:
        print(f"API hatası ({symbol}): {e}")
        return None

# =================== Signal Detection ===================
def detect_signals(df):
    df['ma_fast'] = df['close'].rolling(9).mean()
    df['ma_slow'] = df['close'].rolling(21).mean()
    df['change'] = df['close'].pct_change()
    df['vol_avg'] = df['volume'].rolling(10).mean()

    last = df.iloc[-1]
    prev = df.iloc[-2]
    signals = []

    # MA + trend
    if last['ma_fast'] > last['ma_slow'] and prev['ma_fast'] <= prev['ma_slow']:
        signals.append("🟢 BUY sinyali (MA Kesimi)")
    elif last['ma_fast'] < last['ma_slow'] and prev['ma_fast'] >= prev['ma_slow']:
        signals.append("🔴 SELL sinyali (MA Kesimi)")

    # Testere Formasyonu
    vol = df['change'].rolling(10).std().iloc[-1]
    trend = df['close'].diff().rolling(10).mean().iloc[-1]
    if vol > 0.015 and abs(trend) < 50:
        signals.append("⚙️ Testere Formasyonu Tespit Edildi")

    # Balina Satışı
    if -0.01 < last['change'] < 0 and last['volume'] > 5*last['vol_avg']:
        signals.append("🐋 Balina Satışı olabilir")

    # Hacim Patlaması
    if last['volume'] > 3*last['vol_avg']:
        signals.append("💥 Hacim Patlaması tespit edildi")

    return signals

# =================== Main ===================
def main():
    print(f"=== Mum Botu Çalışıyor... {datetime.now()} ===")
    
    # Telegram test mesajını en başta gönder
    send_test_message()

    all_coins = ["BTCUSDT"] + [f"{c}_USDT" for c in FUTURES_COINS]
    for coin in all_coins:
        symbol = coin.replace("_USDT", "")
        df = get_futures_klines(symbol)
        if df is None or len(df) < 50:
            print(f"{coin}: Veri yok veya yetersiz.")
            continue
        signals = detect_signals(df)
        if signals:
            msg = f"{coin} ({INTERVAL}) Sinyalleri:\n" + "\n".join(signals)
            print(msg)
            send_telegram(msg)
        else:
            print(f"{coin}: Sinyal bulunamadı ❌")

if __name__ == "__main__":
    main()
