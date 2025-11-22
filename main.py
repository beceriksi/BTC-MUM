import os
import time
import requests
import ccxt
import pandas as pd
import numpy as np

from ta.trend import EMAIndicator, MACD
from ta.momentum import RSIIndicator

# ================== AYARLAR ==================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

EXCHANGE_NAME = "okx"
TIMEFRAME = "1d"
OHLCV_LIMIT = 120         # 120 günlük mum
TRADES_LIMIT = 500        # Orderflow için son 500 işlem
TOP_N_COINS = 100         # Hacme göre en iyi 100 coin

CONFIDENCE_THRESHOLD = 75 # Güven puanı eşiği

# Balina seviyeleri (USDT)
S_THRESHOLD = 200_000
M_THRESHOLD = 500_000
L_THRESHOLD = 1_000_000
XL_THRESHOLD = 5_000_000


# ================== TELEGRAM ==================

def send_telegram_message(text: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[!] TELEGRAM_BOT_TOKEN veya TELEGRAM_CHAT_ID yok, mesaj konsola yazılıyor.\n")
        print(text)
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown"
    }
    resp = requests.post(url, data=payload)
    if resp.status_code != 200:
        print("[!] Telegram hatası:", resp.text)


# ================== EXCHANGE ==================

def get_exchange():
    exchange = ccxt.okx({
        "enableRateLimit": True,
        "options": {
            "defaultType": "spot"
        }
    })
    return exchange


# ================== TEKNİK ANALİZ ==================

def ohlcv_to_df(ohlcv):
    """
    ccxt fetch_ohlcv çıktısını DataFrame'e çevirir.
    """
    if not ohlcv or len(ohlcv) == 0:
        return None

    df = pd.DataFrame(
        ohlcv,
        columns=["time", "open", "high", "low", "close", "volume"]
    )
    df["time"] = pd.to_datetime(df["time"], unit="ms")
    return df


def find_support_resistance(df: pd.DataFrame, lookback: int = 60, max_levels: int = 3):
    """
    Basit swing high/low ile son lookback gün içinden en yakın destek / dirençleri bul.
    """
    closes = df["close"].values
    supports = []
    resistances = []

    if len(df) < 5:
        return [], []

    start_idx = max(2, len(df) - lookback)
    end_idx = len(df) - 2

    for i in range(start_idx, end_idx):
        # local low
        if closes[i] < closes[i - 1] and closes[i] < closes[i + 1]:
            supports.append((i, closes[i]))
        # local high
        if closes[i] > closes[i - 1] and closes[i] > closes[i + 1]:
            resistances.append((i, closes[i]))

    last_close = closes[-1]

    supports_sorted = sorted(supports, key=lambda x: abs(x[1] - last_close))
    resistances_sorted = sorted(resistances, key=lambda x: abs(x[1] - last_close))

    support_levels = [round(s[1], 4) for s in supports_sorted[:max_levels]]
    resistance_levels = [round(r[1], 4) for r in resistances_sorted[:max_levels]]

    return support_levels, resistance_levels


def technical_analysis_daily(df: pd.DataFrame):
    """
    Günlük mumlardan trend, RSI, MACD vs hesaplar.
    df kolonları: time, open, high, low, close, volume
    """
    closes = df["close"]

    ema20 = EMAIndicator(closes, window=20, fillna=False).ema_indicator()
    ema50 = EMAIndicator(closes, window=50, fillna=False).ema_indicator()
    rsi = RSIIndicator(closes, window=14, fillna=False).rsi()

    macd_ind = MACD(closes, window_slow=26, window_fast=12, window_sign=9, fillna=False)
    macd = macd_ind.macd()
    macd_signal = macd_ind.macd_signal()
    macd_hist = macd_ind.macd_diff()

    df["ema20"] = ema20
    df["ema50"] = ema50
    df["rsi"] = rsi
    df["macd"] = macd
    df["macd_signal"] = macd_signal
    df["macd_hist"] = macd_hist

    last = df.iloc[-1]

    trend = "yatay"
    if last["ema20"] > last["ema50"] and last["close"] > last["ema20"]:
        trend = "yukarı"
    elif last["ema20"] < last["ema50"] and last["close"] < last["ema20"]:
        trend = "aşağı"

    momentum = "nötr"
    if last["rsi"] >= 60 and last["macd_hist"] > 0:
        momentum = "güçlü yukarı"
    elif last["rsi"] <= 40 and last["macd_hist"] < 0:
        momentum = "güçlü aşağı"

    return {
        "last_close": float(last["close"]),
        "ema20": float(last["ema20"]),
        "ema50": float(last["ema50"]),
        "rsi": float(last["rsi"]),
        "macd": float(last["macd"]),
        "macd_signal": float(last["macd_signal"]),
        "macd_hist": float(last["macd_hist"]),
        "trend": trend,
        "momentum": momentum,
    }


# ================== ORDERFLOW & BALİNA ==================

def analyze_trades(trades):
    """
    trades: ccxt.fetch_trades çıktısı
    Alım/satım hacmi, al-sat oranı, balina seviyeleri vb.
    """
    buy_quote = 0.0
    sell_quote = 0.0

    # S, M, L, XL seviyelerinde net (buy - sell)
    whale_S_net = 0.0
    whale_M_net = 0.0
    whale_L_net = 0.0
    whale_XL_net = 0.0

    for t in trades:
        side = t.get("side")
        price = float(t.get("price", 0))
        amount = float(t.get("amount", 0))
        quote = price * amount

        if side == "buy":
            buy_quote += quote
            direction = 1.0
        elif side == "sell":
            sell_quote += quote
            direction = -1.0
        else:
            continue

        # Balina seviyelerine göre netleri güncelle
        if quote >= XL_THRESHOLD:
            whale_XL_net += direction * quote
        elif quote >= L_THRESHOLD:
            whale_L_net += direction * quote
        elif quote >= M_THRESHOLD:
            whale_M_net += direction * quote
        elif quote >= S_THRESHOLD:
            whale_S_net += direction * quote

    total = buy_quote + sell_quote
    if total > 0:
        buy_ratio = buy_quote / total
    else:
        buy_ratio = 0.5

    whale_net_total = whale_S_net + whale_M_net + whale_L_net + whale_XL_net

    return {
        "buy_quote": buy_quote,
        "sell_quote": sell_quote,
        "buy_ratio": buy_ratio,
        "whale_S_net": whale_S_net,
        "whale_M_net": whale_M_net,
        "whale_L_net": whale_L_net,
        "whale_XL_net": whale_XL_net,
        "whale_net_total": whale_net_total,
    }


def whale_level_text(whale_S_net, whale_M_net, whale_L_net, whale_XL_net):
    """
    S/M/L/XL için + / - / 0 gösterimi.
    """
    def sign_symbol(v):
        if v > 0:
            return "(+)"
        elif v < 0:
            return "(-)"
        else:
            return "(0)"

    return f"S{sign_symbol(whale_S_net)} M{sign_symbol(whale_M_net)} L{sign_symbol(whale_L_net)} XL{sign_symbol(whale_XL_net)}"


# ================== SKORLAMA ==================

def scoring(tech, flow, supports, resistances):
    """
    BUY ve SELL için puanları hesaplar.
    """
    buy_score = 0
    sell_score = 0

    last_close = tech["last_close"]
    ema20 = tech["ema20"]
    ema50 = tech["ema50"]
    rsi = tech["rsi"]
    macd_hist = tech["macd_hist"]
    buy_ratio = flow["buy_ratio"]

    # --- Trend (EMA20 / EMA50) ---
    if ema20 > ema50:
        buy_score += 15
    elif ema20 < ema50:
        sell_score += 15

    # --- RSI + MACD ---
    if 52 <= rsi <= 62:
        buy_score += 10
    if rsi < 40:
        sell_score += 10

    if macd_hist > 0:
        buy_score += 10
    elif macd_hist < 0:
        sell_score += 10

    # --- Orderflow (taker buy ratio) ---
    if buy_ratio > 0.55:
        buy_score += 10
    elif buy_ratio < 0.45:
        sell_score += 10

    # --- Whale net S/M/L/XL ---
    def add_whale_score(net_value, buy_s, sell_s):
        if net_value > 0:
            return buy_s, 0
        elif net_value < 0:
            return 0, sell_s
        else:
            return 0, 0

    bs, ss = add_whale_score(flow["whale_S_net"], 5, 5)
    buy_score += bs
    sell_score += ss

    bs, ss = add_whale_score(flow["whale_M_net"], 10, 10)
    buy_score += bs
    sell_score += ss

    bs, ss = add_whale_score(flow["whale_L_net"], 15, 15)
    buy_score += bs
    sell_score += ss

    bs, ss = add_whale_score(flow["whale_XL_net"], 20, 20)
    buy_score += bs
    sell_score += ss

    # --- Destek / Direnç ---
    if supports or resistances:
        # En yakın destek ve dirençten hangisi daha yakınsa ona göre puan
        nearest_support = supports[0] if supports else None
        nearest_resistance = resistances[0] if resistances else None

        if nearest_support is not None and nearest_resistance is not None:
            dist_support = abs(last_close - nearest_support)
            dist_resist = abs(last_close - nearest_resistance)
            if dist_support < dist_resist:
                # desteğe daha yakın
                buy_score += 5
            else:
                # dirence daha yakın
                sell_score += 5

    return buy_score, sell_score


def build_direction_and_confidence(buy_score, sell_score):
    if buy_score >= sell_score:
        direction = "BUY"
        confidence = buy_score
    else:
        direction = "SELL"
        confidence = sell_score

    # 100'ü geçerse 100'e clamp
    confidence = int(min(confidence, 100))
    return direction, confidence


# ================== ANA AKIŞ ==================

def get_top_n_symbols(exchange, n=TOP_N_COINS):
    """
    OKX'te USDT spot pariteleri için 24h hacme göre en iyi n tanesini döner.
    """
    markets = exchange.load_markets()
    tickers = exchange.fetch_tickers()

    rows = []
    for symbol, info in markets.items():
        if info.get("spot") is not True:
            continue
        if not symbol.endswith("/USDT"):
            continue
        t = tickers.get(symbol)
        if not t:
            continue
        quote_volume = t.get("quoteVolume") or 0
        rows.append((symbol, float(quote_volume)))

    if not rows:
        return []

    rows_sorted = sorted(rows, key=lambda x: x[1], reverse=True)
    top_symbols = [s for s, v in rows_sorted[:n]]
    return top_symbols


def analyze_symbol(exchange, symbol):
    """
    Tek bir sembol için tüm analizi yapar.
    Hata olursa None döner.
    """
    try:
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe=TIMEFRAME, limit=OHLCV_LIMIT)
    except Exception as e:
        print(f"[!] {symbol} OHLCV hatası:", e)
        return None

    df = ohlcv_to_df(ohlcv)
    if df is None or len(df) < 60:
        return None

    tech = technical_analysis_daily(df)
    supports, resistances = find_support_resistance(df)

    # Orderflow / trades
    try:
        trades = exchange.fetch_trades(symbol, limit=TRADES_LIMIT)
        flow = analyze_trades(trades)
    except Exception as e:
        print(f"[!] {symbol} trades hatası:", e)
        # Orderflow bulunamazsa nötr değerlere çek
        flow = {
            "buy_quote": 0.0,
            "sell_quote": 0.0,
            "buy_ratio": 0.5,
            "whale_S_net": 0.0,
            "whale_M_net": 0.0,
            "whale_L_net": 0.0,
            "whale_XL_net": 0.0,
            "whale_net_total": 0.0,
        }

    buy_score, sell_score = scoring(tech, flow, supports, resistances)
    direction, confidence = build_direction_and_confidence(buy_score, sell_score)
    whale_text = whale_level_text(
        flow["whale_S_net"],
        flow["whale_M_net"],
        flow["whale_L_net"],
        flow["whale_XL_net"],
    )

    return {
        "symbol": symbol,
        "tech": tech,
        "flow": flow,
        "supports": supports,
        "resistances": resistances,
        "buy_score": buy_score,
        "sell_score": sell_score,
        "direction": direction,
        "confidence": confidence,
        "whale_text": whale_text,
    }


def format_btc_eth_block(btc_data, eth_data):
    parts = []

    def fmt(data, name_tag):
        if data is None:
            return f"{name_tag}: Veri yok.\n"

        tech = data["tech"]
        flow = data["flow"]
        supports = data["supports"]
        resistances = data["resistances"]

        support_str = ", ".join(str(s) for s in supports) if supports else "yok"
        resist_str = ", ".join(str(r) for r in resistances) if resistances else "yok"

        whale_line = data["whale_text"]
        buy_ratio_pct = round(flow["buy_ratio"] * 100, 1)

        direction, confidence = build_direction_and_confidence(
            data["buy_score"], data["sell_score"]
        )

        return (
            f"{name_tag} – Trend: {tech['trend']} | Momentum: {tech['momentum']}\n"
            f"Fiyat: {round(tech['last_close'], 4)}\n"
            f"RSI: {round(tech['rsi'], 1)} | MACD Hist: {round(tech['macd_hist'], 4)}\n"
            f"Buy Ratio: %{buy_ratio_pct}\n"
            f"Whales: {whale_line}\n"
            f"Destek: {support_str}\n"
            f"Direnç: {resist_str}\n"
            f"Günlük Yön: {direction} (Güven: {confidence}/100)\n"
        )

    parts.append("🟦 BTCUSDT")
    parts.append(fmt(btc_data, "BTCUSDT"))
    parts.append("🟪 ETHUSDT")
    parts.append(fmt(eth_data, "ETHUSDT"))

    return "\n".join(parts)


def format_signal_list(results):
    """
    Sonuçlardan güven ≥ threshold olanları BUY / SELL listesi olarak formatlar.
    """
    buy_list = []
    sell_list = []

    for r in results:
        if r is None:
            continue
        if r["symbol"] in ("BTC/USDT", "ETH/USDT"):
            continue  # BTC ve ETH zaten ayrı blokta

        direction = r["direction"]
        conf = r["confidence"]
        if conf < CONFIDENCE_THRESHOLD:
            continue

        tech = r["tech"]
        flow = r["flow"]
        whale_text = r["whale_text"]
        buy_ratio_pct = round(flow["buy_ratio"] * 100, 1)

        line = (
            f"{r['symbol'].replace('/','')} – Güven: *{conf}*\n"
            f"Trend: {tech['trend']} | RSI: {round(tech['rsi'],1)} | MACD Hist: {round(tech['macd_hist'],4)}\n"
            f"Buy Ratio: %{buy_ratio_pct} | Whales: {whale_text}\n"
        )

        if direction == "BUY":
            buy_list.append((conf, line))
        else:
            sell_list.append((conf, line))

    # Güvene göre sırala
    buy_list.sort(key=lambda x: x[0], reverse=True)
    sell_list.sort(key=lambda x: x[0], reverse=True)

    buy_text = "🚀 *Günün AL Sinyalleri* (Güven ≥ {thr})\n".format(thr=CONFIDENCE_THRESHOLD)
    if not buy_list:
        buy_text += "Şu anda filtreye uyan güçlü AL sinyali yok.\n"
    else:
        for idx, (conf, line) in enumerate(buy_list, start=1):
            buy_text += f"\n{idx}) {line}"

    sell_text = "🔥 *Günün SAT Sinyalleri* (Güven ≥ {thr})\n".format(thr=CONFIDENCE_THRESHOLD)
    if not sell_list:
        sell_text += "Şu anda filtreye uyan güçlü SAT sinyali yok.\n"
    else:
        for idx, (conf, line) in enumerate(sell_list, start=1):
            sell_text += f"\n{idx}) {line}"

    return buy_text, sell_text


def format_market_mood(all_results):
    """
    Genel piyasa modunu çıkarır.
    """
    buy_conf_sum = 0
    sell_conf_sum = 0
    buy_count = 0
    sell_count = 0

    whale_total = 0.0

    for r in all_results:
        if r is None:
            continue
        if r["symbol"] in ("BTC/USDT", "ETH/USDT"):
            continue

        if r["direction"] == "BUY":
            buy_conf_sum += r["confidence"]
            buy_count += 1
        else:
            sell_conf_sum += r["confidence"]
            sell_count += 1

        whale_total += r["flow"]["whale_net_total"]

    if buy_count + sell_count > 0:
        avg_buy = buy_conf_sum / max(buy_count, 1)
        avg_sell = sell_conf_sum / max(sell_count, 1)
    else:
        avg_buy = avg_sell = 0

    if avg_buy > avg_sell + 5:
        trend_dir = "Pozitif (AL ağırlıklı)"
    elif avg_sell > avg_buy + 5:
        trend_dir = "Negatif (SAT ağırlıklı)"
    else:
        trend_dir = "Kararsız / Nötr"

    if whale_total > 0:
        whale_bias = "Balinalar net AL yönlü"
    elif whale_total < 0:
        whale_bias = "Balinalar net SAT yönlü"
    else:
        whale_bias = "Balina etkisi belirgin değil"

    mood_text = (
        "📊 *Piyasa Özeti (OKX – Günlük)*\n\n"
        f"- Genel yön: {trend_dir}\n"
        f"- Ortalama BUY güveni: {round(avg_buy,1)}\n"
        f"- Ortalama SELL güveni: {round(avg_sell,1)}\n"
        f"- Whale net: {round(whale_total,2)} USDT → {whale_bias}\n"
    )
    return mood_text


def run():
    exchange = get_exchange()

    print("[*] OKX piyasaları alınıyor...")
    top_symbols = get_top_n_symbols(exchange, TOP_N_COINS)
    print(f"[*] Top {len(top_symbols)} USDT paritesi bulundu.")

    results = []

    # Önce BTC / ETH
    for sym in ["BTC/USDT", "ETH/USDT"]:
        if sym not in top_symbols:
            top_symbols.append(sym)

    for symbol in top_symbols:
        print(f"[*] Analiz: {symbol}")
        data = analyze_symbol(exchange, symbol)
        results.append(data)
        # Rate limit'e saygı
        time.sleep(0.5)

    # BTC & ETH blok
    btc_data = next((r for r in results if r and r["symbol"] == "BTC/USDT"), None)
    eth_data = next((r for r in results if r and r["symbol"] == "ETH/USDT"), None)
    btc_eth_block = format_btc_eth_block(btc_data, eth_data)

    # Sinyal listeleri
    buy_text, sell_text = format_signal_list(results)

    # Piyasa mood
    mood_text = format_market_mood(results)

    # Final mesaj
    full_message = (
        "🎯 *Kripto Günlük Piyasa Özeti – OKX Screening Bot*\n\n"
        + btc_eth_block
        + "\n\n"
        + buy_text
        + "\n\n"
        + sell_text
        + "\n\n"
        + mood_text
    )

    send_telegram_message(full_message)


if __name__ == "__main__":
    print("[*] Çalışıyor...")
    run()
    print("[*] Bitti.")
