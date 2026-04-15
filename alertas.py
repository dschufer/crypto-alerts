import requests
import os
import json
import time
from datetime import datetime

# ── config ─────────────────────────────────────────────────────
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "8608919442:AAE3tbdfxKXp1ZqKk6WgB8lzeBmo9oBrq2g")
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

SCORE_STRONG    = 65   # comprar/vender ahora
SCORE_MODERATE  = 35   # posible compra/venta
SCORE_ACCEL     = 20   # puntos de cambio para alertar aceleración
STATE_FILE      = "last_state.json"

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
    entry      = ema9_1h

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
        pct      = (closes[-1] - closes[-2]) / closes[-2] * 100 if n > 1 else 0

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
            "pct":    pct,
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
    if score >= SCORE_STRONG:
        return "🟢 COMPRAR AHORA"
    if score >= SCORE_MODERATE:
        return "🟡 POSIBLE COMPRA"
    if score <= -SCORE_STRONG:
        return "🔴 VENDER AHORA"
    if score <= -SCORE_MODERATE:
        return "🟡 POSIBLE VENTA"
    return "⚪ NO OPERAR"

def tf_entry(score):
    if abs(score) >= 80:
        return "5M o 15M"
    return "15M o 1H"

def format_tfs_detail(analysis):
    dir_icon = {"up": "🟢", "down": "🔴", "neu": "🟡"}
    mom_icon = {"uu": "↑↑", "ud": "↑↓", "dd": "↓↓", "du": "↓↑", "flat": "—"}
    lines = []
    for r in analysis:
        d = dir_icon.get(r["dir"], "⚪")
        m = mom_icon.get(r["mom"], "—")
        lines.append(f"  {r['tf']:<4} {d}  vol:{r['vol']:<4}  {m}")
    return "\n".join(lines)

def format_tfs_summary(analysis):
    dir_icon = {"up": "🟢", "down": "🔴", "neu": "🟡"}
    lines = []
    for r in analysis:
        d    = dir_icon.get(r["dir"], "⚪")
        pct  = r["pct"]
        sign = "+" if pct >= 0 else ""
        lines.append(f"  {r['tf']:<4} {d}  {sign}{pct:.2f}%")
    return "\n".join(lines)

# ── estado ───────────────────────────────────────────────────────
def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {"BTC": {"score": None, "alerted": None}, "ETH": {"score": None, "alerted": None}}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)

# ── lógica de alertas ────────────────────────────────────────────
def should_alert(coin, score, last):
    prev_score  = last.get("score")
    prev_alerted = last.get("alerted")

    # señal fuerte nueva
    if abs(score) >= SCORE_STRONG:
        if prev_alerted != "strong":
            return "strong"

    # señal moderada nueva
    elif abs(score) >= SCORE_MODERATE:
        if prev_alerted not in ("strong", "moderate"):
            return "moderate"

    # aceleración: score cambió más de SCORE_ACCEL puntos en la misma dirección
    elif prev_score is not None:
        delta = score - prev_score
        if abs(delta) >= SCORE_ACCEL and score * delta > 0:
            if prev_alerted != "accel":
                return "accel"

    return None

# ── main ─────────────────────────────────────────────────────────
def main():
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    print(f"\n=== Crypto Alert Check — {now} ===")

    last_state = load_state()
    new_state  = {}

    summary_blocks = []
    detail_blocks  = []

    for symbol in SYMBOLS:
        coin = symbol["name"]
        print(f"Analizando {coin}...")

        last = last_state.get(coin, {"score": None, "alerted": None})

        try:
            analysis   = analyze_symbol(symbol)
            score      = calc_score(analysis)
            price      = analysis[0]["price"]
            label      = signal_label(score)
            prev_score = last.get("score")

            print(f"  Score: {score:+d}  |  {label}")

            alert_type = should_alert(coin, score, last)

            # resumen siempre
            summary_blocks.append(
                f"<b>{coin}</b>  {label}  score: {score:+d}\n"
                f"💰 ${price:,.2f}\n"
                f"{format_tfs_summary(analysis)}"
            )

            # detalle según tipo de alerta
            if alert_type == "strong":
                direction   = "LONG" if score > 0 else "SHORT"
                tf          = tf_entry(score)
                action_word = "baje" if direction == "LONG" else "suba"
                no_entrar   = "compres" if direction == "LONG" else "vendas"

                try:
                    entry, stop, target, stop_pct, target_pct = calc_levels(
                        symbol["kraken"], direction, price
                    )
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
                        f"<b>Timeframes {coin}:</b>\n{format_tfs_detail(analysis)}"
                    )
                except Exception as e:
                    detail = (
                        f"━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"<b>{label} — {coin}</b>\n"
                        f"💰 Precio: <b>${price:,.2f}</b>\n"
                        f"📊 Score: <b>{score:+d}/100</b>\n"
                        f"⏱ Temporalidad: <b>{tf}</b>\n"
                        f"<b>Timeframes:</b>\n{format_tfs_detail(analysis)}"
                    )
                detail_blocks.append(detail)
                new_state[coin] = {"score": score, "alerted": "strong"}

            elif alert_type == "moderate":
                direction = "LONG" if score > 0 else "SHORT"
                tf        = tf_entry(score)
                detail = (
                    f"━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"<b>{label} — {coin}</b>\n\n"
                    f"💰 Precio actual : <b>${price:,.2f}</b>\n"
                    f"📊 Score         : <b>{score:+d}/100</b>\n"
                    f"📍 Dirección     : <b>{direction}</b>\n"
                    f"⏱ Temporalidad  : <b>{tf}</b>\n\n"
                    f"⚠️ Señal moderada — esperá que el score suba a 65\n"
                    f"   No entrés todavía\n\n"
                    f"<b>Timeframes {coin}:</b>\n{format_tfs_detail(analysis)}"
                )
                detail_blocks.append(detail)
                new_state[coin] = {"score": score, "alerted": "moderate"}

            elif alert_type == "accel":
                direction = "LONG" if score > 0 else "SHORT"
                delta     = score - prev_score
                detail = (
                    f"━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"⚡ <b>SEÑAL FORMÁNDOSE — {coin}</b>\n\n"
                    f"💰 Precio actual : <b>${price:,.2f}</b>\n"
                    f"📊 Score         : <b>{prev_score:+d} → {score:+d}</b>  ({delta:+d} en 15 min)\n"
                    f"📍 Dirección     : <b>{direction}</b>\n\n"
                    f"👁 Empezá a vigilar el gráfico\n"
                    f"   Todavía no entrés, esperá confirmación\n\n"
                    f"<b>Timeframes {coin}:</b>\n{format_tfs_detail(analysis)}"
                )
                detail_blocks.append(detail)
                new_state[coin] = {"score": score, "alerted": "accel"}

            else:
                # sin alerta nueva, resetear alerted si score volvió a neutral
                if abs(score) < SCORE_MODERATE:
                    new_state[coin] = {"score": score, "alerted": None}
                else:
                    new_state[coin] = {"score": score, "alerted": last.get("alerted")}

        except Exception as e:
            print(f"  Error en {coin}: {e}")
            summary_blocks.append(f"<b>{coin}</b>  ❌ Error al obtener datos")
            new_state[coin] = last

    # armar mensaje
    resumen  = "\n\n".join(summary_blocks)
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
