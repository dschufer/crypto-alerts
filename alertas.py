import requests
import os
import json
import time
from datetime import datetime

# ── config ─────────────────────────────────────────────────────
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "8608919442:AAE3tbdfxKXp1ZqKk6WgB8lzeBmo9oBrq2g")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "1259871459")

SYMBOLS = [
    {"name": "BTC", "kraken": "XBTUSD"},
    {"name": "ETH", "kraken": "ETHUSD"},
]

TIMEFRAMES = [
    {"label": "1W",  "kraken": 10080, "weight": 6},
    {"label": "1D",  "kraken": 1440,  "weight": 5},
    {"label": "4H",  "kraken": 240,   "weight": 4},
    {"label": "1H",  "kraken": 60,    "weight": 3},
    {"label": "15M", "kraken": 15,    "weight": 2},
    {"label": "5M",  "kraken": 5,     "weight": 1},
]

SCORE_THRESHOLD = 65
STATE_FILE = "last_state.json"

# ── telegram ────────────────────────────────────────────────────
def send_telegram(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("  Sin credenciales de Telegram")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        r = requests.post(url, json=payload, timeout=10)
        r.raise_for_status()
        print("  Telegram enviado OK")
    except Exception as e:
        print(f"  Error Telegram: {e}")

# ── kraken ───────────────────────────────────────────────────────
def fetch_klines(pair, interval, limit=50):
    url = "https://api.kraken.com/0/public/OHLC"
    params = {"pair": pair, "interval": interval}
    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    data = r.json()
    if data.get("error"):
        raise Exception(f"Kraken error: {data['error']}")
    result = data["result"]
    pair_key = [k for k in result.keys() if k != "last"][0]
    return result[pair_key][-limit:]

# ── ema ─────────────────────────────────────────────────────────
def ema(arr, period):
    k = 2 / (period + 1)
    e = arr[0]
    for val in arr[1:]:
        e = val * k + e * (1 - k)
    return e

# ── niveles de entrada con EMA 9 del 1H ─────────────────────────
def calc_levels(pair, direction, current_price):
    candles_1h = fetch_klines(pair, 60, 30)
    closes_1h  = [float(c[4]) for c in candles_1h]
    ema9_1h    = round(ema(closes_1h, 9), 2)

    entry = ema9_1h

    if direction == "LONG":
        stop   = round(entry * (1 - 0.008), 2)
        risk   = entry - stop
        target = round(entry + risk * 2, 2)
    else:
        stop   = round(entry * (1 + 0.008), 2)
        risk   = stop - entry
        target = round(entry - risk * 2, 2)

    stop_pct   = round((stop - entry) / entry * 100, 2)
    target_pct = round((target - entry) / entry * 100, 2)

    return entry, stop, target, stop_pct, target_pct

# ── análisis ─────────────────────────────────────────────────────
def analyze_symbol(symbol):
    results = []
    for tf in TIMEFRAMES:
        candles = fetch_klines(symbol["kraken"], tf["kraken"], 50)
        closes  = [float(c[4]) for c in candles]
        volumes = [float(c[6]) for c in candles]

        e9  = ema(closes, 9)
        e21 = ema(closes, 21)

        avg_vol   = sum(volumes[:-1]) / max(len(volumes) - 1, 1)
        vol_ratio = volumes[-1] / avg_vol if avg_vol > 0 else 1

        n        = len(closes)
        roc      = (closes[-1] - closes[-4]) / closes[-4] * 100 if n > 4 else 0
        roc_prev = (closes[-2] - closes[-5]) / closes[-5] * 100 if n > 5 else 0
        accel    = roc - roc_prev

        dir_ = "neu"
        if e9 > e21 * 1.001:
            dir_ = "up"
        elif e9 < e21 * 0.999:
            dir_ = "down"

        vol = "low"
        if vol_ratio > 1.5:
            vol = "high"
        elif vol_ratio > 0.8:
            vol = "med"

        mom = "flat"
        if roc > 0 and accel > 0:
            mom = "uu"
        elif roc > 0 and accel <= 0:
            mom = "ud"
        elif roc < 0 and accel < 0:
            mom = "dd"
        elif roc < 0 and accel >= 0:
            mom = "du"

        results.append({
            "tf":     tf["label"],
            "dir":    dir_,
            "vol":    vol,
            "mom":    mom,
            "weight": tf["weight"],
            "price":  closes[-1],
        })
        time.sleep(0.3)
    return results

# ── score ───────────────────────────────────────────────────────
def calc_score(analysis):
    total, max_total = 0, 0
    for r in analysis:
        w = r["weight"]
        max_total += w * 3
        s = 0
        if r["dir"] == "up":
            s += w * 2
        elif r["dir"] == "down":
            s -= w * 2
        if r["dir"] != "neu":
            if r["vol"] == "high":
                s += (1 if r["dir"] == "up" else -1) * w
            elif r["vol"] == "low":
                s -= (0.3 if r["dir"] == "up" else -0.3) * w
        if r["mom"] == "uu":
            s += w * 0.5
        elif r["mom"] == "dd":
            s -= w * 0.5
        total += s
    return round((total / max_total) * 100)

def signal_label(score):
    if score >= 65:
        return "🟢 COMPRAR AHORA"
    if score >= 35:
        return "🟡 POSIBLE COMPRA"
    if score <= -65:
        return "🔴 VENDER AHORA"
    if score <= -35:
        return "🟡 POSIBLE VENTA"
    return "⚪ NO OPERAR"

def tf_entry(score):
    if abs(score) >= 80:
        return "5M o 15M"
    return "15M o 1H"

def format_tfs(analysis):
    dir_icon = {"up": "🟢", "down": "🔴", "neu": "🟡"}
    mom_icon = {"uu": "↑↑", "ud": "↑↓", "dd": "↓↓", "du": "↓↑", "flat": "—"}
    lines = []
    for r in analysis:
        d = dir_icon.get(r["dir"], "⚪")
        m = mom_icon.get(r["mom"], "—")
        lines.append(f"  {r['tf']:<4} {d}  vol:{r['vol']:<4}  {m}")
    return "\n".join(lines)

# ── estado ───────────────────────────────────────────────────────
def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {"BTC": None, "ETH": None}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)

# ── main ─────────────────────────────────────────────────────────
def main():
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    print(f"\n=== Crypto Alert Check — {now} ===")

    last_state  = load_state()
    new_state   = dict(last_state)

    resumen_lines = []
    detail_blocks = []

    for symbol in SYMBOLS:
        coin = symbol["name"]
        print(f"Analizando {coin}...")
        try:
            analysis  = analyze_symbol(symbol)
            score     = calc_score(analysis)
            price     = analysis[0]["price"]
            label     = signal_label(score)

            print(f"  Score: {score:+d}  |  {label}")

            in_zone  = abs(score) >= SCORE_THRESHOLD
            was_zone = last_state.get(coin) is not None and abs(last_state[coin]) >= SCORE_THRESHOLD

            # línea del resumen siempre
            resumen_lines.append(f"{coin}  {label}  score: {score:+d}")

            # bloque de detalle solo si hay señal operable nueva
            if in_zone and not was_zone:
                direction = "LONG" if score > 0 else "SHORT"
                tf        = tf_entry(score)

                try:
                    entry, stop, target, stop_pct, target_pct = calc_levels(
                        symbol["kraken"], direction, price
                    )
                    action_word = "baje" if direction == "LONG" else "suba"
                    no_entrar   = "compres" if direction == "LONG" else "vendas"

                    detail = (
                        f"━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"<b>{label} — {coin}</b>\n\n"
                        f"💰 Precio actual : <b>${price:,.2f}</b>  ← está acá ahora\n"
                        f"📊 Score         : <b>{score:+d}/100</b>\n"
                        f"📍 Dirección     : <b>{direction}</b>\n\n"
                        f"⏳ Esperá que {action_word} a : <b>${entry:,.2f}</b>  (EMA 9 — 1H)\n"
                        f"📍 Entrada       : <b>${entry:,.2f}</b>  ← poné orden límite acá\n"
                        f"🛑 Stop Loss     : <b>${stop:,.2f}</b>  ({stop_pct:+.2f}%)\n"
                        f"🎯 Take Profit   : <b>${target:,.2f}</b>  ({target_pct:+.2f}%)\n"
                        f"⏱ Temporalidad  : <b>{tf}</b>\n\n"
                        f"⚠️ No {no_entrar} en ${price:,.2f}\n"
                        f"   Esperá que el precio {action_word} a ${entry:,.2f}\n\n"
                        f"<b>Timeframes {coin}:</b>\n{format_tfs(analysis)}"
                    )
                    detail_blocks.append(detail)
                except Exception as e:
                    detail_blocks.append(
                        f"━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"<b>{label} — {coin}</b>\n"
                        f"💰 Precio: <b>${price:,.2f}</b>\n"
                        f"📊 Score: <b>{score:+d}/100</b>\n"
                        f"⏱ Temporalidad: <b>{tf}</b>\n"
                        f"<b>Timeframes:</b>\n{format_tfs(analysis)}"
                    )

            new_state[coin] = score if in_zone else None

        except Exception as e:
            print(f"  Error en {coin}: {e}")
            resumen_lines.append(f"{coin}  ❌ Error")

    # armar mensaje final
    resumen = "\n".join(resumen_lines)
    detalles = "\n\n".join(detail_blocks)

    if detalles:
        msg = (
            f"📊 <b>Resumen — BTC y ETH</b>\n"
            f"🕐 {now}\n\n"
            f"{resumen}\n\n"
            f"{detalles}"
        )
    else:
        msg = (
            f"📊 <b>Resumen — BTC y ETH</b>\n"
            f"🕐 {now}\n\n"
            f"{resumen}"
        )

    send_telegram(msg)
    save_state(new_state)
    print("\nDone.")

if __name__ == "__main__":
    main()
