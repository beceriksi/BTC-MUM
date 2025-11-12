# main.py — TOP100 (market cap) odaklı OKX Spot tarayıcı (USDT>USD), Erken + Güvenli Onay
# Hedef: büyük coinlerde "geç girip tepeye atlama" riskini azaltmak (pullback+breakout, 5m RSI, volatilite freni, cooldown)

import os, time, requests, pandas as pd, numpy as np
from datetime import datetime, timezone

# ====== AYARLAR ======
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID        = os.getenv("CHAT_ID")

OKX_BASE       = "https://www.okx.com"
COINGECKO      = "https://api.coingecko.com/api/v3"

TOP_N_COINS    = int(os.getenv("TOP_N_COINS", "100"))    # CG market cap top-N (base semboller)
QUOTES         = os.getenv("QUOTES", "USDT,USD").split(",")  # Önce USDT, yoksa USD dönüş

# — Büyük coinlere göre sıkılaştırılmış eşikler —
VOL_MIN_EARLY  = float(os.getenv("VOL_MIN_EARLY", "500000"))  # 1m min USDT/USD turnover (erken)
VRATIO_EARLY   = float(os.getenv("VRATIO_EARLY",  "2.6"))     # 1m hacim / EMA(15)
MOM_1M_MIN     = float(os.getenv("MOM_1M_MIN",    "0.0040"))  # ~ +0.40% ivme

VOL_MIN_CONF   = float(os.getenv("VOL_MIN_CONF",  "800000"))  # 1m min turnover (onay)
VRATIO_CONF    = float(os.getenv("VRATIO_CONF",   "3.2"))
PULLBACK_MIN   = float(os.getenv("PULLBACK_MIN",  "0.0020"))  # -0.20%
PULLBACK_MAX   = float(os.getenv("PULLBACK_MAX",  "0.0075"))  # -0.75%
RSI5_CONF_MIN  = float(os.getenv("RSI5_CONF_MIN", "54.0"))    # 5m RSI alt sınır
VOLAT_MAX      = float(os.getenv("VOLAT_MAX",     "0.0070"))  # 1m (H-L)/C ≤ %0.7
COOLDOWN_MIN   = int(os.getenv("COOLDOWN_MIN",    "15"))      # dk
MAX_MSG_COINS  = int(os.getenv("MAX_MSG_COINS",   "12"))

# ====== Yardımcı ======
def ts(): return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

def jget(url, params=None, retries=3, timeout=12):
    for _ in range(retries):
        try:
            r = requests.get(url, params=params, timeout=timeout)
            if r.status_code == 200:
                return r.json()
        except:
            time.sleep(0.25)
    return None

def okx_get(path, params=None, retries=3, timeout=12):
    url = path if path.startswith("http") else OKX_BASE + path
    for _ in range(retries):
        try:
            r = requests.get(url, params=params, timeout=timeout)
            if r.status_code == 200:
                j = r.json()
                if isinstance(j, dict) and j.get("code") == "0":
                    return j.get("data")
        except:
            time.sleep(0.25)
    return None

def telegram(text):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print(text); return
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                      json={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"},
                      timeout=15)
    except:
        pass

def ema(x, n): return x.ewm(span=n, adjust=False).mean()
def rsi(s, n=14):
    d = s.diff(); up = d.clip(lower=0); dn = -d.clip(upper=0)
    rs = up.ewm(alpha=1/n, adjust=False).mean() / (dn.ewm(alpha=1/n, adjust=False).mean() + 1e-12)
    return 100 - (100/(1+rs))

# ====== CoinGecko: Top-N market cap base listesi ======
def top_bases_coingecko(n=TOP_N_COINS):
    # en basit güvenilir uç nokta: /coins/markets
    data = jget(f"{COINGECKO}/coins/markets",
                {"vs_currency":"usd","order":"market_cap_desc","per_page":n,"page":1,"sparkline":"false"}) or []
    bases = []
    for row in data:
        # row['symbol'] küçük harf (örn: 'btc'); row['name'] = 'Bitcoin'
        sym = row.get("symbol","").upper()
        if sym: bases.append(sym)
    return bases

# ====== OKX: Spot tickers ve base eşleşmesi (USDT>USD önceliği) ======
def okx_spot_for_bases(bases, quotes=QUOTES):
    rows = okx_get("/api/v5/market/tickers", {"instType":"SPOT"}) or []
    if not rows: return []

    # Tüm tikkerları base->list eşle
    by_base = {}
    for r in rows:
        inst = r.get("instId","")   # örn "BTC-USDT"
        if "-" not in inst: continue
        base, quote = inst.split("-", 1)
        by_base.setdefault(base.upper(), []).append((inst, quote, float(r.get("volCcy24h","0") or 0.0)))

    chosen = []
    for b in bases:
        li = by_base.get(b, [])
        if not li: continue
        # Önce USDT, yoksa USD; aynı base’de birden fazla varsa 24h hacme göre sırala
        li.sort(key=lambda x: x[2], reverse=True)
        picked = None
        # quote önceliği
        for q in quotes:
            cand = [x for x in li if x[1].upper() == q.upper()]
            if cand:
                picked = cand[0][0]; break
        if not picked:
            picked = li[0][0]  # fallback: en hacimli
        chosen.append(picked)
    return chosen

# ====== KLINE ======
def kline(instId, bar="1m", limit=60):
    d = okx_get("/api/v5/market/candles", {"instId":instId, "bar":bar, "limit":limit})
    if not d: return None
    df = pd.DataFrame(d, columns=["ts","o","h","l","c","vol","volCcy","volQuote","confirm"])
    df = df.astype({"o":"float64","h":"float64","l":"float64","c":"float64","vol":"float64","volCcy":"float64"})
    df["turnover"] = df["volCcy"]
    # newest first → ters çevir
    df = df.iloc[::-1].reset_index(drop=True)
    return df

# ====== ERKEN ======
def early_alert(df1):
    if df1 is None or len(df1) < 40: return False, {}
    # volatilite freni
    hlc = (df1["h"].iloc[-1] - df1["l"].iloc[-1]) / max(df1["c"].iloc[-1], 1e-12)
    if hlc > VOLAT_MAX: return False, {}
    # hacim ve oran
    t = df1["turnover"]
    if t.iloc[-1] < VOL_MIN_EARLY: return False, {}
    base = ema(t, 15)
    v_ratio = float(t.iloc[-1] / (base.iloc[-2] + 1e-12))
    if v_ratio < VRATIO_EARLY: return False, {}
    # yön + trend
    c = df1["c"]
    mom1 = float(c.iloc[-1]/(c.iloc[-2]+1e-12) - 1)
    if mom1 < MOM_1M_MIN: return False, {}
    if c.iloc[-1] <= df1["o"].iloc[-1]: return False, {}
    e20 = float(ema(c, 20).iloc[-1]); e50 = float(ema(c, 50).iloc[-1])
    if e20 <= e50: return False, {}
    return True, {"v_ratio": v_ratio, "mom1": mom1}

# ====== GÜVENLİ ONAY ======
def safe_confirmation(df1, df5):
    if df1 is None or len(df1) < 50: return False, {}
    if df5 is None or len(df5) < 20: return False, {}

    hlc = (df1["h"].iloc[-1] - df1["l"].iloc[-1]) / max(df1["c"].iloc[-1], 1e-12)
    if hlc > VOLAT_MAX: return False, {}

    t = df1["turnover"]; c = df1["c"]
    base = ema(t, 15)
    v_now  = float(t.iloc[-1] / (base.iloc[-2] + 1e-12))
    v_m1   = float(t.iloc[-2] / (base.iloc[-3] + 1e-12))
    v_m2   = float(t.iloc[-3] / (base.iloc[-4] + 1e-12))
    spikes = [k for k,vr in [(-3,v_m2),(-2,v_m1),(-1,v_now)] if vr >= VRATIO_EARLY]
    if not spikes: return False, {}

    k = spikes[0]
    idx = len(c) + k
    spike_close = float(c.iloc[idx])
    since = c.iloc[idx: -1]
    if len(since) < 1: return False, {}
    min_after = float(since.min())
    pull = (min_after / spike_close) - 1.0
    if not (-PULLBACK_MAX <= pull <= -PULLBACK_MIN): return False, {}

    if float(c.iloc[-1]) <= float(since.max()): return False, {}
    if v_now < VRATIO_CONF or float(t.iloc[-1]) < VOL_MIN_CONF: return False, {}

    r5 = float(rsi(df5["c"], 14).iloc[-1])
    if r5 < RSI5_CONF_MIN: return False, {}

    e20 = float(ema(c, 20).iloc[-1]); e50 = float(ema(c, 50).iloc[-1])
    if e20 <= e50: return False, {}

    mom = float(c.iloc[-1]/(c.iloc[-2]+1e-12) - 1.0)
    score = int(min(100, (v_now*16) + (mom*100) + ((r5-50)*3) + 15))
    return True, {"v_ratio": v_now, "pull": pull, "rsi5": r5, "score": score}

# ====== SELL (trend kırılımı + zayıf RSI + son 2-3dk düşüş) ======
def sell_signal(df1, df5):
    if df1 is None or len(df1) < 50 or df5 is None or len(df5) < 20:
        return False, {}
    c = df1["c"]
    e20 = float(ema(c,20).iloc[-1]); e50 = float(ema(c,50).iloc[-1])
    if e20 >= e50: return False, {}
    r5 = float(rsi(df5["c"], 14).iloc[-1])
    if r5 > 45: return False, {}
    drop2 = float(c.iloc[-1]/(c.iloc[-3]+1e-12) - 1)
    if drop2 > -0.012: return False, {}
    score = int(min(100, (abs(drop2)*120) + ((45-r5)*2) + 15))
    return True, {"rsi5": r5, "drop2": drop2, "score": score}

# ====== ANA ======
def main():
    # 1) CG’den TOP100 base
    bases = top_bases_coingecko(TOP_N_COINS)
    # 2) OKX’te karşılığı olan spot pariteleri seç (USDT > USD)
    symbols = okx_spot_for_bases(bases, QUOTES)
    if not symbols:
        telegram(f"⛔ {ts()} — OKX’te TOP{TOP_N_COINS} eşleşmesi bulunamadı."); return

    last_signal_ts = {}  # cooldown
    buys, sells, early = [], [], []

    for i, inst in enumerate(symbols, 1):
        try:
            df1 = kline(inst, "1m", 60)
            if df1 is None: continue
            ok_e, ed = early_alert(df1)
            if ok_e:
                early.append(f"- {inst} | ⚠️ Erken | vRatio:{ed['v_ratio']:.2f} | Δ1m:{ed['mom1']*100:.2f}%")

            df5 = kline(inst, "5m", 50)
            now = time.time(); last_t = last_signal_ts.get(inst, 0)
            cooldown_ok = (now - last_t) >= (COOLDOWN_MIN*60)

            ok_b, bd = safe_confirmation(df1, df5)
            if ok_b and cooldown_ok:
                buys.append((bd["score"], f"- {inst} | 🟢 BUY | vRatio:{bd['v_ratio']:.2f} | Pull:{bd['pull']*100:.2f}% | RSI5:{bd['rsi5']:.1f} | Güven:{bd['score']}"))
                last_signal_ts[inst] = now

            ok_s, sd = sell_signal(df1, df5)
            if ok_s and cooldown_ok:
                sells.append((sd["score"], f"- {inst} | 🔴 SELL | Δ2m:{sd['drop2']*100:.2f}% | RSI5:{sd['rsi5']:.1f} | Güven:{sd['score']}"))
                last_signal_ts[inst] = now

        except:
            pass

        if i % 12 == 0:
            time.sleep(0.25)

    if not buys and not sells and not early:
        print(f"{ts()} — sinyal yok (sessiz)"); return

    buys.sort(key=lambda x: x[0], reverse=True)
    sells.sort(key=lambda x: x[0], reverse=True)

    lines = [f"🧭 *TOP100 (MC) — OKX Spot 1m/5m Tarama*\n⏱ {ts()}\nTaranan: {len(symbols)} coin"]
    if early:
        lines.append("\n⏳ *Erken Uyarılar* (işlem sinyali değildir)")
        lines += early[:MAX_MSG_COINS]
    if buys:
        lines.append("\n📈 *Güvenli BUY Sinyalleri*")
        lines += [m for _, m in buys[:MAX_MSG_COINS]]
    if sells:
        lines.append("\n📉 *SELL Sinyalleri* (temkinli)")
        lines += [m for _, m in sells[:MAX_MSG_COINS]]

    telegram("\n".join(lines))

if __name__ == "__main__":
    main()
