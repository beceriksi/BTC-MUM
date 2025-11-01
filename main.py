import requests
import pandas as pd
import os
from datetime import datetime

# =================== Telegram Settings ===================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")  # GitHub Secrets
CHAT_ID = os.getenv("CHAT_ID")                # GitHub Secrets

MEXC_FUTURES_URL = "https://contract.mexc.com/api/v1/contract/tickers"

# =================== Telegram Functions ===================
def send_telegram(message):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("❌ Telegram bilgileri eksik! Lütfen GitHub Secrets'ta TOKEN ve CHAT_ID ayarlı mı kontrol et.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={"chat_id": CHAT_ID, "text": message})
        print(f"Telegram mesaj durumu: {r.status_code}")
    except Exception as e:
        print(f"Telegram error: {e}")

# =================== Signal Detection ===================
def detect_signals(df):
    signals = []
    for _, row in df.iterrows():
        symbol = row["symbol"]
        price_change = float(row.get("rise_fall_rate", 0))
        volume = float(row.get("amount", 0))
        last_price = float(row.get("fair_price", 0))

        # === BUY Sinyali ===
        if price_change > 2 and volume > 1000000:
            signals.append(f"🟢 BUY: {symbol} | Değişim: {price_change:.2f}% | Hacim: {volume/1000:.1f}K")

        # === SELL Sinyali ===
        if price_change < -2 and volume > 1000000:
            signals.append(f"🔴 SELL: {symbol} | Düşüş: {price_change:.2f}% | Hacim: {volume/1000:.1f}K")

        # === Balina Satışı ===
        if -1 < price_change < 0 and volume > 5000000:
            signals.append(f"🐋 Balina Satışı olabilir: {symbol} | Değişim: {price_change:.2f}% | Hacim: {volume/1000:.1f}K")

    return signals

# =================== Main ===================
def main():
    print(f"=== Çalışıyor... {datetime.now()} ===")
    try:
        response = requests.get(MEXC_FUTURES_URL, timeout=10)
        data = response.json().get("data", [])
        if not data:
            print("❌ API'den veri alınamadı!")
            return

        df = pd.DataFrame(data)
        signals = detect_signals(df)

        if signals:
            message = f"📊 MEXC Futures Sinyalleri ({datetime.now().strftime('%Y-%m-%d %H:%M')}):\n\n" + "\n".join(signals)
            send_telegram(message)
            print("✅ Sinyaller gönderildi")
        else:
            print("Sinyal bulunamadı ❌")
    except Exception as e:
        print(f"Hata oluştu: {e}")

if __name__ == "__main__":
    main()
