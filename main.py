import requests, pandas as pd, numpy as np, os

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

def send(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, data={"chat_id": CHAT_ID, "text": msg})

def get_btc(interval="1h", limit=200):
    url = f"https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval={interval}&limit={limit}"
    data = requests.get(url).json()
    df = pd.DataFrame(data, columns=[
        't','o','h','l','c','v','ct','q','n','tb','tq','i'
    ]).astype(float)
    df['c'] = df['c'].astype(float)
    return df

def detect_saw(df):
    df['change'] = df['c'].pct_change()
    vol = df['change'].rolling(10).std()
    trend = df['c'].diff().rolling(10).mean()
    if vol.iloc[-1] > 0.015 and abs(trend.iloc[-1]) < 50:
        return True
    return False

def rsi(series, period=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def analyze_btc():
    df = get_btc()
    df['rsi'] = rsi(df['c'])
    ma20 = df['c'].rolling(20).mean()
    ma50 = df['c'].rolling(50).mean()

    signal = ""
    if detect_saw(df):
        signal = "⚙️ Testere formasyonu tespit edildi!\n"
        if ma20.iloc[-1] > ma50.iloc[-1] and df['rsi'].iloc[-1] < 70:
            signal += "📈 Yükseliş olasılığı yüksek."
        elif ma20.iloc[-1] < ma50.iloc[-1] and df['rsi'].iloc[-1] > 30:
            signal += "📉 Düşüş olasılığı yüksek."
        else:
            signal += "🤔 Kararsız piyasa."
        send(signal)

if __name__ == "__main__":
    analyze_btc()
