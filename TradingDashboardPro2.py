import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import numpy as np
import requests
import pytz
from datetime import datetime, time
from streamlit_autorefresh import st_autorefresh
import pytz
import os
import json
import sqlite3

DB_FILE = "alerts.db"
st.cache_data.clear()
def get_conn():
    return sqlite3.connect(DB_FILE, check_same_thread=False)

def init_db():
    conn = get_conn()
    c = conn.cursor()

    # Alerts Tabelle
    c.execute("""
        CREATE TABLE IF NOT EXISTS alerts (
            ticker TEXT PRIMARY KEY,
            above REAL,
            below REAL
        )
    """)

    # Trigger Status
    c.execute("""
        CREATE TABLE IF NOT EXISTS triggered (
            ticker TEXT PRIMARY KEY,
            above INTEGER,
            below INTEGER
        )
    """)

    conn.commit()
    conn.close()

init_db()
# -----------------------
# AUTO REFRESH (NUR TOP DATEN)
# -----------------------
def get_market_session():
    et = pytz.timezone("US/Eastern")
    now = datetime.now(et)

    weekday = now.weekday()
    current_time = now.time()

    if weekday >= 5:
        return "WEEKEND"

    if time(4, 0) <= current_time < time(9, 30):
        return "PREMARKET"

    elif time(9, 30) <= current_time < time(16, 0):
        return "RTH"  # Regular Trading Hours

    elif time(16, 0) <= current_time < time(20, 0):
        return "AFTERHOURS"

    else:
        return "CLOSED"

 
st.set_page_config(layout="wide")  # 👈 ganz oben!
SESSION = get_market_session()

if "symbol" not in st.session_state:
    st.session_state.symbol = "BTC-USD"
       
# -----------------------
# MARKET SCANNER (PRO LEVEL)
# -----------------------

@st.cache_data(ttl=86400)
def get_sp500_symbols():
    return [

        # -----------------------
        # MEGA CAPS (Stabilität)
        # -----------------------
        "AAPL","MSFT","NVDA","AMZN","META","GOOGL","TSLA",
        "AVGO","TSM","AMD","NFLX","INTC","ADBE","CRM","LITE",

        # -----------------------
        # HIGH BETA / TRADER STOCKS
        # -----------------------
        "COIN","PLTR","RIVN","SOFI","SNAP","ROKU",
        "UPST","AFRM","DKNG","SHOP","SQ","PYPL",

        # -----------------------
        # AI / MOMENTUM / HALBLEITER
        # -----------------------
        "SMCI","ARM","MU","ASML","LRCX","KLAC","MRVL",

        # -----------------------
        # FINANCIALS (LIQUID)
        # -----------------------
        "JPM","GS","BAC","MS","SCHW",

        # -----------------------
        # ENERGY (VOLATIL)
        # -----------------------
        "XOM","CVX","OXY","SLB","HAL","ENR.DE",

        # -----------------------
        # HEALTHCARE (MOVES OFTEN NEWS DRIVEN)
        # -----------------------
        "LLY","UNH","JNJ","MRNA","BNTX",

        # -----------------------
        # INDUSTRIAL / MACRO SENSITIVE
        # -----------------------
        "CAT","BA","GE","DE","NOC",

        # -----------------------
        # ETFS (SEHR WICHTIG!)
        # -----------------------
        "SPY","QQQ","IWM","DIA","XLF","XLK","XLE",
        
        # -----------------------
        # INDIZES
        # -----------------------
        "^GSPC","^NDX","^DJI",

        # -----------------------
        # VOLATILITY / HEDGE
        # -----------------------
        "VIXY","UVXY",

        # -----------------------
        # CRYPTO
        # -----------------------
        "BTC-USD","ETH-USD","SOL-USD","XRP_USD","ADA_USD","DOGE"

    ]

def filter_symbols_by_session(symbols, session):
    if session == "RTH":
        symbols = symbols[:100]
    else:
        symbols = symbols[:30]

    if session == "WEEKEND":
        return [s for s in symbols if "=F" in s or "USD" in s]

    return symbols

def compute_atr(df):
    tr = np.maximum(
        df["High"] - df["Low"],
        np.maximum(
            abs(df["High"] - df["Close"].shift(1)),
            abs(df["Low"] - df["Close"].shift(1))
        )
    )
    return tr.ewm(span=14, adjust=False).mean()

def get_active_vwap(row):
    if row["Session"] == "RTH":
        return row["VWAP_RTH"]
    elif row["Session"] == "PREMARKET":
        return row["VWAP_PRE"]
    elif row["Session"] == "AFTERHOURS":
        return row["VWAP_AH"]
    return row["Close"]

def download_data(symbols):
    data_all = {}

    try:
        d = yf.download(
            tickers=" ".join(symbols),
            period="5d",
            interval="5m",
            group_by="ticker",
            threads=True,
            progress=False
        )
    except:
        return data_all

    if isinstance(d.columns, pd.MultiIndex):
        for ticker in symbols:
            if ticker in d.columns.get_level_values(0):
                df = d[ticker].copy()
                df = df.dropna(subset=["Close"])
                if not df.empty:
                    data_all[ticker] = df
    else:
        # fallback (nur 1 ticker)
        data_all[symbols[0]] = d.dropna()

    return data_all

def calculate_sl_tp(df, i, setup, rr_target=2):
    price = df["Close"].iloc[i]
    atr = df["ATR"].iloc[i]
    vwap = df["VWAP_RTH"].iloc[i]

    if np.isnan(price) or np.isnan(atr) or np.isnan(vwap):
        return np.nan, np.nan

    # Sicherheitslimits
    min_risk = atr * 0.5
    max_risk = atr * 3

    # -----------------------
    # LONG
    # -----------------------
    if setup == "LONG":
        swing_low = df["Low"].iloc[max(0, i-10):i].min()

        if np.isnan(swing_low):
            swing_low = price - atr

        sl_vwap = vwap - atr * 0.5
        sl = min(swing_low, sl_vwap)

        if sl >= price:
            sl = price - min_risk

        risk = price - sl
        risk = max(min_risk, min(risk, max_risk))

        tp = price + risk * rr_target
        return round(sl, 2), round(tp, 2)

    # -----------------------
    # SHORT
    # -----------------------
    elif setup == "SHORT":
        swing_high = df["High"].iloc[max(0, i-10):i].max()

        if np.isnan(swing_high):
            swing_high = price + atr

        sl_vwap = vwap + atr * 0.5
        sl = max(swing_high, sl_vwap)

        if sl <= price:
            sl = price + min_risk

        risk = sl - price
        risk = max(min_risk, min(risk, max_risk))

        tp = price - risk * rr_target
        return round(sl, 2), round(tp, 2)

    return np.nan, np.nan

st_autorefresh(interval=60000, key="alerts")  # alle 60s
# -----------------------
# TELEGRAM ALERTS
# -----------------------

def send_telegram(msg):

    TOKEN = st.secrets["TELEGRAM_TOKEN"]
    CHAT_ID = st.secrets["TELEGRAM_CHAT_ID"]
    
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    
    try:
        response = requests.post(
            url,
            data={"chat_id": CHAT_ID, "text": msg},
            timeout=5
        )
        if response.status_code != 200:
            st.warning(f"Telegram failed: {response.text}")
    except Exception as e:
        st.warning(f"Telegram Error: {e}")

ALERT_FILE = "price_alerts.json"

# -----------------------
# LOAD / SAVE
# -----------------------
def save_alerts_sql(alerts_dict):
    conn = get_conn()
    c = conn.cursor()

    c.execute("DELETE FROM alerts")

    for ticker, v in alerts_dict.items():
        c.execute(
            "INSERT INTO alerts (ticker, above, below) VALUES (?, ?, ?)",
            (ticker, v["above"], v["below"])
        )

    conn.commit()
    conn.close()


def load_alerts_sql():
    conn = get_conn()
    c = conn.cursor()

    c.execute("SELECT ticker, above, below FROM alerts")
    rows = c.fetchall()

    conn.close()

    return {
        r[0]: {"above": r[1], "below": r[2]}
        for r in rows
    }
# -----------------------
# SIDEBAR UI
# -----------------------
st.sidebar.header("📊 Preisalarme")

PriceAlerttickers = []  # 🔥 IMMER vorher definieren

for i in range(2):

    ticker_input = st.sidebar.text_input(f"Ticker {i+1}", key=f"ticker_{i}")

    # 👉 Label sauber lösen (kein undefinierter Name!)
    label = ticker_input if ticker_input else f"Ticker {i+1}"

    price_above = st.sidebar.number_input(f"{label} ≥ Preis", key=f"above_{i}", value=0.0)
    price_below = st.sidebar.number_input(f"{label} ≤ Preis", key=f"below_{i}", value=0.0)

    if ticker_input:
        PriceAlerttickers.append({
            "ticker": ticker_input.upper(),
            "above": price_above,
            "below": price_below
        })
    
def load_triggered_sql():
    conn = get_conn()
    c = conn.cursor()

    c.execute("SELECT ticker, above, below FROM triggered")
    rows = c.fetchall()

    conn.close()

    return {
        r[0]: {"above": bool(r[1]), "below": bool(r[2])}
        for r in rows
    }


def save_triggered_sql(triggered):
    conn = get_conn()
    c = conn.cursor()

    c.execute("DELETE FROM triggered")

    for ticker, v in triggered.items():
        c.execute(
            "INSERT INTO triggered (ticker, above, below) VALUES (?, ?, ?)",
            (ticker, int(v["above"]), int(v["below"]))
        )

    conn.commit()
    conn.close()  


if st.sidebar.button("💾 Alarme speichern"):

    if len(PriceAlerttickers) == 0:
        st.sidebar.warning("Bitte mindestens einen Ticker eingeben")

    else:
        alerts = {
            t["ticker"]: {
                "above": t["above"],
                "below": t["below"]
            } for t in PriceAlerttickers
        }

        save_alerts_sql(alerts)
        st.sidebar.success("Gespeichert!")
        
def check_alerts():
    alerts = load_alerts_sql()
    triggered = load_triggered_sql()

    if not alerts:
        return

    for ticker, levels in alerts.items():

        try:
            data = yf.download(ticker, period="5d", interval="5m")

            if data.empty:
                continue

            close = data["Close"]

            if isinstance(close, pd.DataFrame):
                close = close.iloc[:, 0]

            price = float(close.dropna().iloc[-1])

        except Exception as e:
            st.write(f"Fehler bei {ticker}: {e}")
            continue

        if ticker not in triggered:
            triggered[ticker] = {"above": False, "below": False}

        # 🔼 ABOVE
        if levels["above"] > 0:
            if price >= levels["above"] and not triggered[ticker]["above"]:
                send_telegram(f"🚀 {ticker} über {levels['above']} → {price:.2f}")
                triggered[ticker]["above"] = True

            if price < levels["above"]:
                triggered[ticker]["above"] = False

        # 🔽 BELOW
        if levels["below"] > 0:
            if price <= levels["below"] and not triggered[ticker]["below"]:
                send_telegram(f"📉 {ticker} unter {levels['below']} → {price:.2f}")
                triggered[ticker]["below"] = True

            if price > levels["below"]:
                triggered[ticker]["below"] = False

    save_triggered_sql(triggered)
    
check_alerts()

st.write("Alerts:", load_alerts_sql())
st.write("Triggered:", load_triggered_sql())

def mark_premarket(df):
    import pytz
    from datetime import time

    et = pytz.timezone("US/Eastern")

    # 👉 Fallback: Session IMMER setzen
    df["Session"] = "RTH"

    try:
        # Index prüfen
        if not isinstance(df.index, pd.DatetimeIndex):
            return df

        # Timezone fix
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        df.index = df.index.tz_convert(et)

        times = df.index.time

        df.loc[(times >= time(4,0)) & (times < time(9,30)), "Session"] = "PREMARKET"
        df.loc[(times >= time(16,0)) & (times < time(20,0)), "Session"] = "AFTERHOURS"

    except Exception as e:
        # 👉 niemals crashen!
        pass

    return df

@st.cache_data(ttl=300)
def scan_market(limit=100):
    symbols = filter_symbols_by_session(get_sp500_symbols(), SESSION)[:limit]
    results = []

    data_all = {}
    chunks = np.array_split(symbols, 3)

    # --- Download ---
    data_all = download_data(symbols)


    # --- Scan ---
    for s in symbols:
        df = data_all.get(s)
        if df is None or len(df) < 10:
            continue

        try:
            df["Session"] = "RTH"  # einfacher Fallback (Scanner nutzt keine echten Sessions)

            df = mark_premarket(df)
            
            df = mark_premarket(df)

            # 🔥 HARD GUARANTEE
            if not isinstance(df, pd.DataFrame):
                continue

            if "Session" not in df.columns:
                df["Session"] = "RTH"

            if df.empty:
                continue

            if "Session" not in df.columns:
                df["Session"] = "RTH"

            if "Volume" not in df.columns:
                continue

            df["Vol_RTH"] = np.where(
                df["Session"].to_numpy() == "RTH",
                df["Volume"].to_numpy(),
                np.nan
)
            df["Vol_Avg_RTH"] = df["Vol_RTH"].rolling(20, min_periods=5).mean()
            df["VWAP_RTH"] = (df["Close"] * df["Volume"]).groupby(df.index.date).cumsum() / df["Volume"].groupby(df.index.date).cumsum()
            ema20 = df["Close"].ewm(span=20).mean()
            ema50 = df["Close"].ewm(span=50).mean()
            ema200 = df["Close"].ewm(span=200).mean()

            price = df["Close"].iloc[-1]

            # --- Liquidity ---
            avg_vol = df["Vol_Avg_RTH"].iloc[-1]
            dollar_vol = price * avg_vol

            score = 0

            # 🔹 Liquidity
            if dollar_vol > 5_000_000:
                score += 1
            if dollar_vol > 20_000_000:
                score += 1

            df["ATR"] = compute_atr(df)
            
            if df["ATR"].isna().all():
                continue
            atr = df["ATR"].iloc[-1]
            atr_pct = atr / price

            # 🔹 Volatility
            if atr_pct > 0.003:
                score += 1
            if atr_pct > 0.01:
                score += 1

            # -----------------------
            # DMI + ADX
            # -----------------------
            up_move = df["High"].diff()
            down_move = -df["Low"].diff()

            df["+DM"] = np.where((up_move > down_move) & (up_move > 0), up_move, 0)
            df["-DM"] = np.where((down_move > up_move) & (down_move > 0), down_move, 0)

            plus_dm = df["+DM"].ewm(span=14, adjust=False).mean()
            minus_dm = df["-DM"].ewm(span=14, adjust=False).mean()

            df["+DI"] = 100 * (plus_dm / df["ATR"])
            df["-DI"] = 100 * (minus_dm / df["ATR"])

            dx = (np.abs(df["+DI"] - df["-DI"]) / (df["+DI"] + df["-DI"])) * 100
            df["ADX"] = dx.ewm(span=14, adjust=False).mean()

            # Trend Flags
            df["Trend_Strong"] = df["ADX"] > 25
            df["Trend_Long"] = df["+DI"] > df["-DI"]
            df["Trend_Short"] = df["-DI"] > df["+DI"]
            
            if avg_vol is None or avg_vol == 0 or np.isnan(avg_vol):
                continue
            # --- Relative Volume ---
            rel_vol = df["Volume"].iloc[-1] / avg_vol

            # 🔹 Volume
            if rel_vol > 1.2:
                score += 1
            if rel_vol > 1.5:
                score += 1
            
            curr = df.iloc[-1]
            vwap = get_active_vwap(curr)

            delta = df["Close"].diff()
            gain = delta.clip(lower=0)
            loss = -delta.clip(upper=0)
            avg_gain = gain.ewm(alpha=1/14).mean()
            avg_loss = loss.ewm(alpha=1/14).mean().replace(0,1e-10)
            rsi = 100 - (100 / (1 + avg_gain / avg_loss))
            rsi = rsi.iloc[-1]

            vol_spike = df["Volume"].iloc[-1] > 1.5 * df["Volume"].rolling(20).mean().iloc[-1]
            sweep_high = df["High"].iloc[-2] > df["High"].rolling(20).max().iloc[-3]
            sweep_low = df["Low"].iloc[-2] < df["Low"].rolling(20).min().iloc[-3]

            trend_bull = ema20.iloc[-1] > ema50.iloc[-1]
            trend_bear = ema20.iloc[-1] < ema50.iloc[-1]

            # -----------------------
            # SMART SCORING SYSTEM
            # -----------------------

            long_score = 0
            short_score = 0

            # 🔹 VWAP Position
            if price > vwap:
                long_score += 1
            else:
                short_score += 1

            # 🔹 Trend (EMA)
            if ema20.iloc[-1] > ema50.iloc[-1]:
                long_score += 1
            else:
                short_score += 1

            # 🔹 RSI Momentum
            if rsi > 55:
                long_score += 1
            elif rsi < 45:
                short_score += 1

            # 🔹 Volume Spike
            if vol_spike:
                long_score += 1
                short_score += 1  # beide profitieren

            # 🔹 Liquidity Sweeps
            if sweep_low:
                long_score += 1
            if sweep_high:
                short_score += 1

            # -----------------------
            # FINAL SCORE
            # -----------------------

            base_score = 0

            # Liquidity
            if dollar_vol > 5_000_000:
                base_score += 1
            if dollar_vol > 20_000_000:
                base_score += 1

            # Volatility
            if atr_pct > 0.003:
                base_score += 1
            if atr_pct > 0.01:
                base_score += 1

            # Relative Volume
            if rel_vol > 1.2:
                base_score += 1
            if rel_vol > 1.5:
                base_score += 1

            # Gesamtbewertung
            total_score = max(long_score, short_score) + base_score

            # -----------------------
            # SETUP ENTSCHEIDUNG
            # -----------------------

            setup = None

            if total_score >= 7:
                setup = "LONG" if long_score > short_score else "SHORT"
            else:
                continue

            delta_score = long_score - short_score
            score = total_score

            i = len(df) - 1
            sl, tp = calculate_sl_tp(df, i, setup, rr_target=2)

            if np.isnan(sl) or np.isnan(tp):
                continue
            
            risk = abs(price - sl)
            reward = abs(tp - price)

            if risk == 0:
                continue

            rr = reward / risk

            # 🔹 QUALITY FILTER
            if rr < 1.5:
                continue

            if setup:
                results.append({
                    "symbol": s,
                    "price": round(price,2),
                    "score": score,
                    "delta": delta_score,
                    "setup": setup,
                    "sl": sl,
                    "tp": tp,
                })
                
            signal = {
                "symbol": s,
                "type": setup,
                "price": price,
                "sl": sl,
                "tp": tp,
                "rr": rr,
                "score": score,
                "delta": delta_score
}
            
            # -----------------------
            # ANTI-SPAM LOGIK
            # -----------------------

            if "sent_signals" not in st.session_state:
                st.session_state.sent_signals = set()

            signal_id = f"{signal['symbol']}_{signal['type']}_{round(signal['price'],1)}"

            if signal_id not in st.session_state.sent_signals and total_score >= 5:

                message = (
                    f"🚨 {signal['symbol']} {signal['type']}\n\n"
                    f"Entry: {signal['price']:.2f}\n"
                    f"SL: {signal['sl']:.2f}\n"
                    f"TP: {signal['tp']:.2f}\n"
                    f"RR: {signal['rr']:.2f}\n\n"
                    f"Score: {signal['score']} | Δ {signal['delta']}"
                )

                send_telegram(message)

                st.session_state.sent_signals.add(signal_id)

        except Exception as e:
            st.write(f"Fehler bei {s}: {e}")
            continue

    df_res = pd.DataFrame(results)
    if df_res.empty:
        return [], []

    gainers = df_res[df_res['setup']=="LONG"].sort_values("score", ascending=False)
    losers  = df_res[df_res['setup']=="SHORT"].sort_values("score", ascending=False)

    # --- Immer 10 zurückgeben ---
    def pad(df_list):
        lst = df_list.to_dict("records")
        return lst + [{}]*(10-len(lst)) if len(lst)<10 else lst[:10]

    return pad(gainers), pad(losers)


# -----------------------
# UI
# -----------------------

st.sidebar.subheader("🔥 Scanner PRO MAX")

def render_list(title, stocks):
    st.sidebar.write(f"**{title}**")

    if not stocks:
        st.sidebar.caption("Keine Daten")
        return

    for i, s in enumerate(stocks):
        if not isinstance(s, dict):
            continue

        ticker = s.get("symbol")
        setup = s.get("setup", "-")
        score = s.get("score", "-")
        delta = s.get("delta", "-")

        if not ticker:
            continue

        label = f"{ticker} | {setup} | ⭐{score} Δ{delta}"

        if st.sidebar.button(label, key=f"{title}_{i}_{ticker}"):
            st.session_state.symbol = ticker

with st.sidebar.expander("🔥 Scanner PRO MAX", expanded=False):
    
    limit = st.slider(
        "Universe Size",
        50, 500, 100,
        step=50,
        key="scanner_limit"
    )

live_mode = st.sidebar.checkbox("⚡ Live Mode (RTH only)", True)
show_rth_only_vwap = st.sidebar.checkbox("VWAP nur RTH", True)
color_sessions = st.sidebar.checkbox("Sessions farbig", True)



if live_mode:
    if SESSION == "WEEKEND":
        st.caption("Weekend Mode: nur Crypto + Futures")
    elif SESSION != "RTH":
        st.caption(f"Off-hours: {SESSION}")

gainers, losers = scan_market(limit)

render_list("Top Momentum ↑", gainers)
render_list("Top Breakdown ↓", losers)

# -----------------------
# SIDEBAR
# -----------------------

symbol_input = st.sidebar.text_input(
    "Ticker",
    value=st.session_state.symbol,
    key="ticker_input"
).upper()

st.sidebar.markdown("---")

if st.sidebar.button("🔄 Refresh Data"):
    st.cache_data.clear()
    st.rerun()
    
# Nur updaten wenn User wirklich tippt
if symbol_input != st.session_state.symbol:
    st.session_state.symbol = symbol_input
    
symbol = st.session_state.symbol   

if st.sidebar.button("🧪 Test LONG Signal"):
    send_telegram("🚀 TEST LONG SIGNAL funktioniert!")
# -----------------------
# AUTO PERIOD / INTERVAL FIX
# -----------------------
valid_map = {
    "1m": ["1d", "5d"],
    "5m": ["1d", "5d"],
    "15m": ["5d", "1mo"],
    "1h": ["1mo", "3mo"],
    "4h": ["3mo", "6mo"],
    "1d": ["1y", "max"]
}

# --- DEFAULTS ---
if "interval_select" not in st.session_state:
    st.session_state.interval_select = "5m"

if "period_select" not in st.session_state:
    st.session_state.period_select = valid_map["5m"][1]

# --- INTERVAL ---
interval = st.sidebar.selectbox(
    "Timeframe",
    list(valid_map.keys()),
    key="interval_select"
)

# 🔥 WICHTIG: HARTE VALIDIERUNG
valid_periods = valid_map[interval]

if st.session_state.period_select not in valid_periods:
    st.session_state.period_select = valid_periods[0]

# --- PERIOD ---
period = st.sidebar.selectbox(
    "Period",
    valid_periods,
    index=valid_periods.index(st.session_state.period_select),  # 👈 CRUCIAL
    key="period_select"
)

st.sidebar.caption(f"Aktive Kombi: {interval} / {period}")

# -----------------------
# DATA
# -----------------------
@st.cache_data(ttl=5)
def load_fast_price(symbol):
    try:
        df = yf.download(symbol, period="1d", interval="1m", progress=False, threads=False)
    except:
        return pd.DataFrame()

    if df.empty or "Close" not in df.columns:
        return pd.DataFrame()

    # MultiIndex auflösen (falls vorhanden)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    # Leere oder ungültige Zeilen entfernen
    df = df.dropna(subset=["Close"])

    return df

@st.cache_data(ttl=10)
def load_global_prices(symbol):
    eu_map = {
        "TSLA": "TSLA.DE",
        "NVDA": "NVDA.DE",
        "AAPL": "AAPL.DE",
        "MSFT": "MSF.DE",
        "AMZN": "AMZ.DE",
        "META": "META.DE",
        "GOOGL": "GOOGL.DE",
        "AVGO": "AVGO.DE",
        "TSM": "TSM.DE",
        "AMD": "AMD.DE",
        "NFLX": "NFLX.DE",
        "INTC": "INTC.DE",
        "ADBE": "ADBE.DE",
        "CRM": "CRM.DE",
        "LITE": "LITE.DE",
        "COIN": "COIN.DE",
        "PLTR": "PLTR.DE",
        "RIVN": "RIVN.DE",
        "SOFI": "SOFI.DE",
        "SNAP": "SNAP.DE",
        "ROKU": "ROKU.DE",
        "UPST": "UPST.DE",
        "AFRM": "AFRM.DE",
        "DKNG": "DKNG.DE",
        "SHOP": "SHOP.DE",
        "SQ": "SQ.DE",
        "PYPL":"PYPL.DE",
        "RHM" :"RHM.DE",
        "BASF" :"BAS.DE",
        "ENR" :"ENR.DE",

        # -----------------------
        # AI / MOMENTUM / HALBLEITER
        # -----------------------
        "SMCI": "SMCI.DE",
        "ARM": "ARM.DE",
        "MU":"MU.DE",
        "ASML": "ASML.DE",
        "LRCX": "LRCX.DE",
        "KLAC": "KLAC.DE",
        "MRVL": "MRVL.DE"
    }

    eu_symbol = eu_map.get(symbol)

    def get_last_price(sym):
        if not sym:
            return np.nan
        try:
            df = yf.download(sym, period="1d", interval="5m", progress=False)
            if df.empty:
                return np.nan
            return float(df["Close"].iloc[-1])
        except:
            return np.nan

    eu_price = get_last_price(eu_symbol)

    return eu_price, eu_symbol

@st.cache_data(ttl=60)
def load_data_with_premarket(symbol, period, interval):
    # prepost=True liefert auch Pre- und After-Hours
    df = yf.download(
        symbol, 
        period=period, 
        interval=interval, 
        progress=False, 
        prepost=True
    )
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df.dropna(subset=["Close"])

@st.cache_data(ttl=60)
def load_multi_exchange(symbol, period, interval):
    eu_map = {
        "TSLA": "TSLA.DE",
        "NVDA": "NVDA.DE",
        "AAPL": "AAPL.DE",
        "MSFT": "MSF.DE",
        "AMZN": "AMZ.DE"
    }

    symbol_eu = eu_map.get(symbol)

    # -----------------------
    # US DATA
    # -----------------------
    df_us = yf.download(
        symbol,
        period=period,
        interval=interval,
        progress=False,
        prepost=True
    )

    if df_us is None or df_us.empty:
        return pd.DataFrame(), None

    if isinstance(df_us.columns, pd.MultiIndex):
        df_us.columns = df_us.columns.get_level_values(0)

    df_us = df_us.dropna()

    # -----------------------
    # NO EU SYMBOL
    # -----------------------
    if not symbol_eu:
        df_us["EU_Close"] = np.nan
        df_us["Spread"] = np.nan
        return df_us, None

    # -----------------------
    # EU DATA
    # -----------------------
    df_eu = yf.download(
        symbol_eu,
        period=period,
        interval=interval,
        progress=False,
        prepost=True
    )

    # 🔴 WICHTIGER FIX
    if df_eu is None or df_eu.empty or "Close" not in df_eu.columns:
        df_us["EU_Close"] = np.nan
        df_us["Spread"] = np.nan
        return df_us, symbol_eu

    if isinstance(df_eu.columns, pd.MultiIndex):
        df_eu.columns = df_eu.columns.get_level_values(0)

    df_eu = df_eu.dropna()

    # -----------------------
    # TIMEZONE FIX
    # -----------------------
    try:
        if df_us.index.tz is None:
            df_us.index = df_us.index.tz_localize("UTC")
        if df_eu.index.tz is None:
            df_eu.index = df_eu.index.tz_localize("UTC")

        df_us.index = df_us.index.tz_convert("US/Eastern")
        df_eu.index = df_eu.index.tz_convert("US/Eastern")
    except Exception as e:
        print("TZ Error:", e)

    # -----------------------
    # MERGE
    # -----------------------
    df_us["EU_Close"] = df_eu["Close"].reindex(df_us.index, method="ffill")
    df_us["Spread"] = df_us["Close"] - df_us["EU_Close"]

    return df_us, symbol_eu

def normalize_df(df):
    # MultiIndex komplett entfernen
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    # Sicherstellen: alle OHLCV sind Series
    for col in ["Open", "High", "Low", "Close", "Volume"]:
        if col in df.columns:
            if isinstance(df[col], pd.DataFrame):
                df[col] = df[col].squeeze()  # <- WICHTIG

    return df

df, symbol_eu = load_multi_exchange(symbol, period, interval)
df = normalize_df(df)
df = df.loc[:, ~df.columns.duplicated()]

# 2️⃣ Leeren DataFrame abfangen
if df.empty:
    st.warning("Keine Daten verfügbar")
    st.stop()

# 3️⃣ Premarket markieren
#df = mark_premarket(df)

# -----------------------
# VOLUME CLEAN (FIX)
# -----------------------
if "Session" not in df.columns:
    df["Session"] = "RTH"

df["Vol_RTH"] = np.where(
    df["Session"].to_numpy() == "RTH",
    df["Volume"].to_numpy(),
    np.nan
)
df["Vol_Avg_RTH"] = df["Vol_RTH"].rolling(20, min_periods=5).mean()

# Sicherstellen, dass Spalte existiert
if "EU_Close" not in df.columns:
    df["EU_Close"] = np.nan

# Forward-Fill nur, wenn es echte Werte gibt
if df["EU_Close"].notna().any():
    df["EU_Close"] = df["EU_Close"].fillna(method="ffill")
else:
    df["EU_Close"] = np.nan

# Spread berechnen (Fallback 0)
df["Spread"] = df["Close"] - df["EU_Close"].fillna(df["Close"])

if len(df) == 0:
    st.stop()

df = df.tail(300)

# -----------------------
# MTF
# -----------------------

@st.cache_data(ttl=30)
def load_mtf(df_base):
    df_5m = df_base.copy()

    df_15m = df_5m.resample("15min").agg({
        "Open": "first",
        "High": "max",
        "Low": "min",
        "Close": "last",
        "Volume": "sum"
    }).dropna()

    return df_5m, df_15m

df_5m, df_15m = load_mtf(df)

def mtf_bias(df):
    close = df["Close"]

    # Falls MultiIndex / DataFrame → auf Series reduzieren
    if isinstance(close, pd.DataFrame):
        close = close.iloc[:, 0]

    ema20 = close.ewm(span=20).mean()
    ema50 = close.ewm(span=50).mean()

    if len(ema50.dropna()) == 0:
        return "neutral"

    return "bull" if ema20.iloc[-1] > ema50.iloc[-1] else "bear"


bias_5m = mtf_bias(df_5m)
bias_15m = mtf_bias(df_15m)

# -----------------------
# INDICATORS
# -----------------------
df["EMA9"]  = df["Close"].ewm(span=9, adjust=False).mean()
df["EMA20"] = df["Close"].ewm(span=20, adjust=False).mean()
df["EMA50"] = df["Close"].ewm(span=50, adjust=False).mean()
df["EMA200"] = df["Close"].ewm(span=200, adjust=False).mean()
# -----------------------
# ATR (GLOBAL FIX)
# -----------------------
df["ATR"] = compute_atr(df)
df["ATR_pct"] = df["ATR"] / df["Close"]

delta = df["Close"].diff()
gain = delta.clip(lower=0)
loss = -delta.clip(upper=0)

avg_gain = gain.ewm(alpha=1/14).mean()
avg_loss = loss.ewm(alpha=1/14).mean()

rs = avg_gain / avg_loss.replace(0,1e-10)
df["RSI"] = 100 - (100 / (1 + rs))

ema12 = df["Close"].ewm(span=12).mean()
ema26 = df["Close"].ewm(span=26).mean()

df["MACD"] = ema12 - ema26
df["MACD_signal"] = df["MACD"].ewm(span=9).mean()
df["MACD_hist"] = df["MACD"] - df["MACD_signal"]

df["date"] = df.index.date

def compute_vwap_suite(df):
    df = df.copy()

    tp = (df["High"] + df["Low"] + df["Close"]) / 3

    # --- RTH VWAP ---
    df["Vol_RTH"] = np.where(df["Session"] == "RTH", df["Volume"], 0)

    df["pv_rth"] = tp * df["Vol_RTH"]
    df["cum_vol_rth"] = df.groupby(df.index.date)["Vol_RTH"].cumsum()
    df["cum_pv_rth"] = df.groupby(df.index.date)["pv_rth"].cumsum()

    df["VWAP_RTH"] = df["cum_pv_rth"] / df["cum_vol_rth"].replace(0, np.nan)

    # --- PREMARKET VWAP ---
    df["vol_pre"] = np.where(df["Session"] == "PREMARKET", df["Volume"], 0)
    df["pv_pre"] = tp * df["vol_pre"]

    df["cum_vol_pre"] = df.groupby(df.index.date)["vol_pre"].cumsum()
    df["cum_pv_pre"] = df.groupby(df.index.date)["pv_pre"].cumsum()

    df["VWAP_PRE"] = df["cum_pv_pre"] / df["cum_vol_pre"]

    # --- AFTERHOURS VWAP ---
    df["vol_ah"] = np.where(df["Session"] == "AFTERHOURS", df["Volume"], 0)
    df["pv_ah"] = tp * df["vol_ah"]

    df["cum_vol_ah"] = df.groupby(df.index.date)["vol_ah"].cumsum()
    df["cum_pv_ah"] = df.groupby(df.index.date)["pv_ah"].cumsum()

    df["VWAP_AH"] = df["cum_pv_ah"] / df["cum_vol_ah"]

    return df

df = compute_vwap_suite(df)

typical_price = (df["High"] + df["Low"] + df["Close"]) / 3
vwap_dev = (typical_price - df["VWAP_RTH"]).rolling(20).std()

df["VWAP_upper2"] = df["VWAP_RTH"] + 2*vwap_dev
df["VWAP_lower2"] = df["VWAP_RTH"] - 2*vwap_dev

up_move = df["High"].diff()
down_move = -df["Low"].diff()

df["+DM"] = np.where((up_move > down_move) & (up_move > 0), up_move, 0)
df["-DM"] = np.where((down_move > up_move) & (down_move > 0), down_move, 0)

plus_dm = df["+DM"].ewm(span=14, adjust=False).mean()
minus_dm = df["-DM"].ewm(span=14, adjust=False).mean()

df["+DI"] = 100 * (plus_dm / df["ATR"])
df["-DI"] = 100 * (minus_dm / df["ATR"])

dx = (np.abs(df["+DI"] - df["-DI"]) / (df["+DI"] + df["-DI"])) * 100
df["ADX"] = dx.ewm(span=14, adjust=False).mean()

df["Trend_Strong"] = df["ADX"] > 25
df["Trend_Long"] = df["+DI"] > df["-DI"]
df["Trend_Short"] = df["-DI"] > df["+DI"]

# echtes Volumen
df["Vol_Current"] = df["Volume"]

# Average NUR auf echte Daten (keine 0!)
vol_clean = df["Volume"].replace(0, np.nan)

df["Vol_Avg"] = vol_clean.rolling(20, min_periods=5).mean()

df["Daily_High"] = df["High"].rolling("1D").max()
df["Daily_Low"] = df["Low"].rolling("1D").min()

up_move = df["High"].diff()
down_move = -df["Low"].diff()

df["+DM"] = np.where((up_move > down_move) & (up_move > 0), up_move, 0)
df["-DM"] = np.where((down_move > up_move) & (down_move > 0), down_move, 0)

tr = np.maximum(df["High"] - df["Low"], 
       np.maximum(abs(df["High"] - df["Close"].shift(1)), abs(df["Low"] - df["Close"].shift(1))))
atr = tr.ewm(span=14, adjust=False).mean()

df["+DI"] = 100 * (df["+DM"].ewm(span=14, adjust=False).mean() / atr)
df["-DI"] = 100 * (df["-DM"].ewm(span=14, adjust=False).mean() / atr)

dx = (abs(df["+DI"] - df["-DI"]) / (df["+DI"] + df["-DI"])) * 100
df["ADX"] = dx.ewm(span=14, adjust=False).mean()

kc_mult = 1.5
df["KC_MID"] = df["Close"].ewm(span=20, adjust=False).mean()  # zentrale Linie, EMA20
df["KC_UPPER"] = df["KC_MID"] + kc_mult * df["ATR"]
df["KC_LOWER"] = df["KC_MID"] - kc_mult * df["ATR"]

# -----------------------
# BOLLINGER BANDS
# -----------------------

bb_period = 20
bb_std = 2

if "BB_UPPER" not in df.columns:
    df["BB_MID"] = df["Close"].rolling(20).mean()
    df["BB_STD"] = df["Close"].rolling(20).std()
    df["BB_UPPER"] = df["BB_MID"] + 2*df["BB_STD"]
    df["BB_LOWER"] = df["BB_MID"] - 2*df["BB_STD"]
    
vwap_bb_cols = ["VWAP_RTH", "VWAP_upper2", "VWAP_lower2", "BB_UPPER", "BB_LOWER", "BB_MID"]

for col in vwap_bb_cols:
    if col not in df.columns:
        df[col] = np.nan
    # Forward-Fill nur, wenn mind. 1 gültiger Wert existiert
    if df[col].notna().any():
        df[col] = df[col].ffill()
    else:
        df[col] = np.nan

# Bollinger Squeeze
df["BB_WIDTH"] = df["BB_UPPER"] - df["BB_LOWER"]
# -----------------------
# DELTA
# -----------------------

df["delta"] = np.where(df["Close"] > df["Open"], df["Volume"], -df["Volume"])
df["cum_delta"] = df["delta"].cumsum()

# -----------------------
# MARKET REGIME
# -----------------------

def detect_market_regime(df):
    price = df["Close"]
    # VWAP pro Zeile auswählen
    vwap = df.apply(get_active_vwap, axis=1)

    # VWAP Cross Count
    crosses = ((price > vwap) != (price.shift(1) > vwap.shift(1))).rolling(20).sum()

    # Trend Stärke (EMA Spread)
    ema20 = df["EMA20"]
    ema50 = df["EMA50"]
    trend_strength = abs(ema20 - ema50) / price

    if crosses.iloc[-1] > 3 and trend_strength.iloc[-1] < 0.002:
        return "RANGE"
    else:
        return "TREND"

df["MarketRegime"] = detect_market_regime(df)

# -----------------------
# SELL THE NEWS DETECTOR
# -----------------------

lookback = 20
df["high_max"] = df["High"].rolling(lookback).max()
df["low_min"] = df["Low"].rolling(lookback).min()

df["sweep_high"] = df["High"] > df["high_max"].shift(1)
df["sweep_low"] = df["Low"] < df["low_min"].shift(1)

df["vol_mean"] = df["Volume"].rolling(20).mean()
df["vol_spike"] = df["Volume"] > df["Vol_Avg_RTH"] * 1.5

df["SellNewsShort"] = False
df["SellNewsLong"] = False

start = max(2, len(df) - 100)


for i in range(start, len(df)):
    prev = df.iloc[i-1]
    curr = df.iloc[i]

    # SHORT: Fake Breakout nach oben → Reversal
    if (
        prev["sweep_high"] and
        prev["Close"] > prev["VWAP_RTH"] and   # vorher stark
        curr["Close"] < curr["VWAP_RTH"] and   # jetzt schwach
        curr["Close"] < prev["Close"]      # Momentum kippt
    ):
        df.at[df.index[i], "SellNewsShort"] = True

    # LONG: Fake Breakdown → Reversal
    if (
        prev["sweep_low"] and
        prev["Close"] < prev["VWAP_RTH"] and
        curr["Close"] > curr["VWAP_RTH"] and
        curr["Close"] > prev["Close"]
    ):
        df.at[df.index[i], "SellNewsLong"] = True  
        


# -----------------------
# SMART MONEY (FIXED)
# -----------------------

df["LongScore"] = 0
df["ShortScore"] = 0

vol_avg = df["Volume"].rolling(20).mean()

start = max(2, len(df) - 100)

df["VWAP_Reclaim_Long"] = (
    (df["Close"].shift(1) < df["VWAP_RTH"].shift(1)) &
    (df["Close"] > df["VWAP_RTH"])
)

df["VWAP_Reclaim_Short"] = (
    (df["Close"].shift(1) > df["VWAP_RTH"].shift(1)) &
    (df["Close"] < df["VWAP_RTH"])
)     
df["VWAP_Extreme_High"] = df["Close"] > df["VWAP_upper2"]
df["VWAP_Extreme_Low"]  = df["Close"] < df["VWAP_lower2"]

df["HH"] = df["High"] > df["High"].shift(1)
df["LL"] = df["Low"] < df["Low"].shift(1)

ema_kc = df["Close"].ewm(span=20).mean()
kc_mult = 1.5

df["KC_UPPER"] = ema_kc + kc_mult * df["ATR"]
df["KC_LOWER"] = ema_kc - kc_mult * df["ATR"]

df["KC_Above"] = df["Close"] > df["KC_UPPER"]
df["KC_Below"] = df["Close"] < df["KC_LOWER"]

df["LongSignal"] = False
df["ShortSignal"] = False

df["LongScore"] = df["LongScore"].astype(float)
df["ShortScore"] = df["ShortScore"].astype(float)
 
for i in range(start, len(df)):
    prev = df.iloc[i-1]
    curr = df.iloc[i]
    
    vwap = get_active_vwap(curr)

    score_long = 0

    weights = {
        "sweep": 2,
        "vwap": 2,
        "volume": 1.5,
        "trend": 2,
        "delta": 1,
        "confirmation": 2,
        "mtf": 1.5,
        "sellnews": 2
    }
    
    # Core Faktoren
    if prev["sweep_low"]:
        score_long += weights["sweep"]


    if curr["Close"] > vwap:
        score_long += weights["vwap"]

    if curr["vol_spike"]:
        score_long += weights["volume"]

    # Trend (MTF)
    if bias_5m == "bull":
        score_long += weights["trend"]

    if bias_15m == "bull":
        score_long += weights["mtf"]

    # Delta
    if curr["delta"] > 0:
        score_long += weights["delta"]

    # Confirmation (Boost)
    if prev["sweep_low"] and curr["Close"] > curr["VWAP_RTH"]:
        score_long += weights["confirmation"]

    # Sell the news Reversal
    if df["SellNewsLong"].iloc[i]:
        score_long += weights["sellnews"]

    score_short = 0

    weights = {
        "sweep": 2,
        "vwap": 2,
        "volume": 1.5,
        "trend": 2,
        "delta": 1,
        "confirmation": 2,
        "mtf": 1.5,
        "sellnews": 2
    }

    # Core Faktoren
    if prev["sweep_high"]:
        score_short += weights["sweep"]
        
    if curr["Close"] < vwap:
        score_short += weights["vwap"]

    if curr["vol_spike"]:
        score_short += weights["volume"]

    # Trend (MTF)
    if bias_5m == "bear":
        score_short += weights["trend"]

    if bias_15m == "bear":
        score_short += weights["mtf"]

    # Delta
    if curr["delta"] < 0:
        score_short += weights["delta"]

    # Confirmation (Boost)
    if prev["sweep_high"] and curr["Close"] < curr["VWAP_RTH"]:
        score_short += weights["confirmation"]

    # Sell the news Reversal
    if df["SellNewsShort"].iloc[i]:
        score_short += weights["sellnews"]

    regime = detect_market_regime(df)

    # Mean Reversion Boost
    if regime == "RANGE":
        if curr["Close"] > curr["VWAP_upper2"]:
            score_short += 2
        if curr["Close"] < curr["VWAP_lower2"]:
            score_long += 2

    # Trend Boost
    if regime == "TREND":
        if curr["Close"] > vwap:
            score_long += 1
        if curr["Close"] < vwap:
            score_short += 1
            
            
        # --- NEU: ATR Filter ---
    if curr["ATR_pct"] > 0.005:
        if curr["Close"] > vwap:
            score_long += 1
        else:
            score_short += 1

    # --- NEU: KC Trend ---
    if curr["KC_Below"]:
        score_short += 1


    # --- NEU: KC Trend ---
    if curr["KC_Above"]:
        score_long += 1   

    # --- NEU: Trend Strength ---
    if curr["Trend_Strong"] and curr["Trend_Long"]:
        score_long += 2  
        
    # --- NEU: Trend Strength ---
    if curr["Trend_Strong"] and curr["Trend_Short"]:
        score_short += 2      
            
    if df["VWAP_Reclaim_Long"].iloc[i]:
        score_long += 2

    if df["VWAP_Reclaim_Short"].iloc[i]:
        score_short += 2            
            
    if curr["VWAP_Extreme_Low"]:
        score_long += 2

    if curr["VWAP_Extreme_High"]:
        score_short += 2      
        
    if curr["HH"]:
        score_long += 1

    if curr["LL"]:
        score_short += 1
        
    # ganz am Ende des Loops
    score_long = min(score_long, 10)
    score_short = min(score_short, 10)

    df.at[df.index[i], "LongScore"] = score_long
    df.at[df.index[i], "ShortScore"] = score_short
       

# -----------------------
# SIGNAL GENERATION (FIXED)
# -----------------------

df["ScoreDelta"] = df["LongScore"] - df["ShortScore"]

df["LongSignal"] = False
df["ShortSignal"] = False

start = max(2, len(df) - 100)

MIN_SCORE = 5
DELTA_THRESHOLD = 2

for i in range(start, len(df)):

    long_score = df["LongScore"].iloc[i]
    short_score = df["ShortScore"].iloc[i]
    delta = long_score - short_score

    if i > start:
        prev_long = df["LongSignal"].iloc[i-1]
        prev_short = df["ShortSignal"].iloc[i-1]

        if prev_long and long_score >= short_score:
            df.at[df.index[i], "LongSignal"] = True
            continue

        if prev_short and short_score >= long_score:
            df.at[df.index[i], "ShortSignal"] = True
            continue

    # Mindestqualität
    if max(long_score, short_score) < MIN_SCORE:
        continue

    # Entscheidung
    if long_score > short_score and delta >= DELTA_THRESHOLD:
        df.at[df.index[i], "LongSignal"] = True

    elif short_score > long_score and delta <= -DELTA_THRESHOLD:
        df.at[df.index[i], "ShortSignal"] = True
        
# -----------------------
# SL / TP (SMART VERSION)
# -----------------------
def calculate_sl_tp(df, i, rr_target=2):
    price = df["Close"].iloc[i] 
    atr = df["ATR"].iloc[i] 
    vwap = df["VWAP_RTH"].iloc[i] 
    if np.isnan(price) or np.isnan(atr) or np.isnan(vwap):
        return np.nan, np.nan 
    # Sicherheitslimits
    min_risk = atr * 0.5 
    max_risk = atr * 3 
    # verhindert riesige TP 
    if df["LongSignal"].iloc[i]:
        swing_low = df["Low"].iloc[max(0, i-10):i].min() 
        if np.isnan(swing_low): 
            swing_low = price - atr 
            sl_vwap = vwap - atr * 0.5 
            sl = min(swing_low, sl_vwap) 
            # FIX: SL darf nicht über Preis liegen 
            if sl >= price: 
                sl = price - min_risk 
                risk = price - sl 
                risk = max(min_risk, min(risk, max_risk)) 
                tp = price + risk * rr_target 
            return sl, tp 

    elif df["ShortSignal"].iloc[i]: 
        swing_high = df["High"].iloc[max(0, i-10):i].max()
        if np.isnan(swing_high): 
            swing_high = price + atr 
            sl_vwap = vwap + atr * 0.5 
            sl = max(swing_high, sl_vwap) 
            # FIX: SL darf nicht unter Preis liegen 
            if sl <= price: 
                sl = price + min_risk 
                risk = sl - price 
                risk = max(min_risk, min(risk, max_risk)) 
                tp = price - risk * rr_target 
            return sl, tp 
    return np.nan, np.nan


ATR_MULT_SL = 1.5
ATR_MULT_TP = 2.5

for i in range(1, len(df)):

    price = df["Close"].iloc[i]
    atr = df["ATR"].iloc[i]

    new_long = df["LongSignal"].iloc[i] and not df["LongSignal"].iloc[i-1]
    new_short = df["ShortSignal"].iloc[i] and not df["ShortSignal"].iloc[i-1]

    # 1) Neue Signale → NUR HIER initial SL/TP setzen
    if new_long:
        sl, tp = calculate_sl_tp(df, i, rr_target=2)
        if sl is not None:
            df.at[df.index[i], "SL"] = sl
            df.at[df.index[i], "TP"] = tp

    elif new_short:
        sl, tp = calculate_sl_tp(df, i, rr_target=2)
        if sl is not None:
            df.at[df.index[i], "SL"] = sl
            df.at[df.index[i], "TP"] = tp

    # 2) Trailing SL
    if df["LongSignal"].iloc[i-1]:
        prev_sl = df["SL"].iloc[i-1]
        new_sl = price - atr * 1.2
        df.at[df.index[i], "SL"] = max(prev_sl, new_sl)

    if df["ShortSignal"].iloc[i-1]:
        prev_sl = df["SL"].iloc[i-1]
        new_sl = price + atr * 1.2
        df.at[df.index[i], "SL"] = min(prev_sl, new_sl)
 
df["Date"] = df.index.date

orb_highs = []
orb_lows = []

for date, group in df.groupby("Date"):
    orb = group.between_time("09:30", "09:45")

    high = orb["High"].max()
    low = orb["Low"].min()

    orb_highs.extend([high] * len(group))
    orb_lows.extend([low] * len(group))

df["ORB_High"] = orb_highs
df["ORB_Low"] = orb_lows
  
def get_smart_signal(df, i):
    long_score = df["LongScore"].iloc[i]
    short_score = df["ShortScore"].iloc[i]
    delta = long_score - short_score

    if long_score >= MIN_SCORE and delta >= DELTA_THRESHOLD:
        return "LONG", long_score, short_score, delta
    elif short_score >= MIN_SCORE and delta <= -DELTA_THRESHOLD:
        return "SHORT", long_score, short_score, delta
    else:
        return "NEUTRAL", long_score, short_score, delta  
     
def get_entry_signal(df, i, bias):
    price = df["Close"].iloc[i]
    vwap = df["VWAP_RTH"].iloc[i]

    long_score = df["LongScore"].iloc[i]
    short_score = df["ShortScore"].iloc[i]
    delta = long_score - short_score

    volume = df["Volume"].iloc[i]
    avg_volume = df["Volume"].rolling(20).mean().iloc[i]

    # ORB Levels (musst du vorher berechnet haben)
    orb_high = df["ORB_High"].iloc[i]
    orb_low = df["ORB_Low"].iloc[i]

    # SL / TP holen
    sl = df["SL"].iloc[i]
    tp = df["TP"].iloc[i]

    if np.isnan(sl) or np.isnan(tp):
        return None

    rr = abs((tp - price) / (price - sl)) if price != sl else 0

    # -----------------------
    # 🚀 LONG SETUP
    # -----------------------
    if (
    
        bias == "LONG" and
        long_score >= MIN_SCORE and
        delta >= DELTA_THRESHOLD and
        price > vwap and
        price > orb_high and
        volume > avg_volume * VOLUME_FACTOR and
        rr >= MIN_RR
    ):
        return {
            "type": "LONG",
            "price": price,
            "sl": sl,
            "tp": tp,
            "rr": rr,
            "delta": delta
        }

    # -----------------------
    # 🔻 SHORT SETUP
    # -----------------------
    if (
        bias == "SHORT" and
        short_score >= MIN_SCORE and
        delta <= -DELTA_THRESHOLD and
        price < vwap and
        price < orb_low and
        volume > avg_volume * VOLUME_FACTOR and
        rr >= MIN_RR
    ):
        return {
            "type": "SHORT",
            "price": price,
            "sl": sl,
            "tp": tp,
            "rr": rr,
            "delta": delta
        }

    return None 
  
# -----------------------
# Berechne LongScore, ShortScore und Delta
# -----------------------
df["LongScore"] = df.get("LongScore", 0.0)
df["ShortScore"] = df.get("ShortScore", 0.0)

# Berechne Delta
df["Delta"] = df["LongScore"] - df["ShortScore"]

# SMART Signal Ausgabe für die letzte Kerze
i = len(df) - 1
ls = df["LongScore"].iloc[i]
ss = df["ShortScore"].iloc[i]
delta = df["Delta"].iloc[i]

# Bestimme Signaltyp
if ls > ss and delta >= DELTA_THRESHOLD:
    signal_type = "LONG"
elif ss > ls and delta <= -DELTA_THRESHOLD:
    signal_type = "SHORT"
else:
    signal_type = "NEUTRAL"

# Ausgabe
print(f"SMART {signal_type} | Score: {ls:.2f} | Δ {delta:.2f}")
    
i = len(df) - 1

smart_type, long_score, short_score, delta = get_smart_signal(df, i)
signal = get_entry_signal(df, i, smart_type)


# -----------------------
# PRICE METRICS
# -----------------------

def safe_last(series, default=np.nan):
    try:
        val = series.iloc[-1]
        if pd.isna(val):
            return default
        return float(val)
    except:
        return default  

# Aktueller Preis aus 1m-Daten inkl. Pre/Post
df_fast = load_fast_price(symbol)  # schon vorhanden
if df_fast.empty or len(df_fast) < 2:
    st.warning("Keine Live-Daten verfügbar")
    st.stop()

# 1️⃣ Letzter RTH Close (Last Price)
rth_df = df[df["Session"] == "RTH"]
if not rth_df.empty:
    last_rth_price = rth_df["Close"].iloc[-1]
else:
    # fallback, z.B. letzte verfügbare Kerze
    last_rth_price = df["Close"].iloc[-1]
    
def last_valid(series):
    try:
        return float(series.dropna().iloc[-1])
    except:
        return 0.0

# Absicherung
if df_fast.empty or "Close" not in df_fast.columns:
    current_price = 0.0
else:
    current_price = last_valid(df_fast["Close"])


if last_rth_price is not None and current_price is not None:
    delta_price = last_rth_price - current_price
else:
    delta_price = None
    
delta_percent = (delta_price / last_rth_price) * 100 if last_rth_price != 0 else 0

# Optional: EU-Preis
eu_price, eu_symbol = load_global_prices(symbol)
eu_display = f" | EU: ${eu_price:.2f}" if not np.isnan(eu_price) else ""

vwap_last = (df_fast["Close"] * df_fast["Volume"]).cumsum() / df_fast["Volume"].cumsum()
vwap_last = vwap_last.iloc[-1]

rsi_last = df["RSI"].iloc[-1]

# EMA
df["EMA9"]  = df["Close"].ewm(span=9, adjust=False).mean()
df["EMA20"] = df["Close"].ewm(span=20, adjust=False).mean()
df["EMA50"] = df["Close"].ewm(span=50, adjust=False).mean()
df["EMA200"] = df["Close"].ewm(span=200, adjust=False).mean()

# Volumen
df["Vol_Current"] = df["Volume"]
df["Vol_Avg"] = df["Volume"].rolling(20).mean()
df["Volume"] = df["Volume"].replace(0, np.nan)
df["Volume"] = df["Volume"].ffill()

# Daily High / Low
df["Daily_High"] = df["High"].rolling("1D").max()
df["Daily_Low"] = df["Low"].rolling("1D").min()

# DMI / ADX
tr = np.maximum(df["High"] - df["Low"], 
                np.maximum(abs(df["High"] - df["Close"].shift(1)), abs(df["Low"] - df["Close"].shift(1))))
atr = tr.ewm(span=14, adjust=False).mean()

up_move = df["High"].diff()
down_move = -df["Low"].diff()

df["+DM"] = np.where((up_move > down_move) & (up_move > 0), up_move, 0)
df["-DM"] = np.where((down_move > up_move) & (down_move > 0), down_move, 0)

df["+DI"] = 100 * (df["+DM"].ewm(span=14, adjust=False).mean() / atr)
df["-DI"] = 100 * (df["-DM"].ewm(span=14, adjust=False).mean() / atr)

dx = (abs(df["+DI"] - df["-DI"]) / (df["+DI"] + df["-DI"])) * 100
df["ADX"] = dx.ewm(span=14, adjust=False).mean()

# -----------------------
# AUTO REFRESH (NUR METRICS)
# -----------------------


st_autorefresh(interval=60000, key="price_refresh")

@st.cache_data(ttl=86400)
def get_company_name(symbol):
    try:
        t = yf.Ticker(symbol)

        # Versuch 1: info
        info = t.info
        name = info.get("shortName") or info.get("longName")

        if name:
            return name

        # Versuch 2: history metadata (stabiler!)
        hist = t.history(period="1d")
        if hasattr(hist, "attrs"):
            meta = hist.attrs
            name = meta.get("shortName") or meta.get("longName")
            if name:
                return name

        return symbol

    except Exception as e:
        print("Name error:", e)
        return symbol
    
name = get_company_name(symbol)

display_name = f"{name} ({symbol})" if name != symbol else symbol

col1, col2, col3 = st.columns(3)

# 4️⃣ Metric
col1.metric(
    label=f"{display_name} (RTH){eu_display}", 
    value=f"${current_price:.2f}"
)

col2.metric(
    label="Current Price",
    value=f"${last_rth_price:.2f}",
    delta=f"{delta_price:+.2f} ({delta_percent:+.2f}%)"
)

col3.metric("VWAP  \n  (volume weighted average price)", f"${vwap_last:.2f}")

col5, col6, col7 = st.columns(3)

macd_value = round(df["MACD"].iloc[-1], 2)
col5.metric("MACD  \n  (moving average convergence divergence)", macd_value)

daily_high = round(df["Daily_High"].iloc[-1], 2)
col6.metric("Daily_High", daily_high)

daily_low = round(df["Daily_Low"].iloc[-1], 2)
col7.metric("Daily_Low", daily_low)

# ROW 3 (Commodities / weitere Futures)
col8, col9, col10 = st.columns(3)

rsi_last = df["RSI"].iloc[-1]
col8.metric("RSI  \n  (relative strength index)", f"{rsi_last:.2f}")

def safe_metric(val):
    if pd.isna(val) or np.isinf(val):
        return 0
    return float(val)

volume_value = safe_metric(df["Vol_Current"].iloc[-1])
volume_average = safe_metric(df["Vol_Avg_RTH"].iloc[-1])
col9.metric("Vol_Current", volume_value)

# Optional: andere Metriken daneben
volume_average = round(df["Vol_Avg"].iloc[-1], 2)
col10.metric("Vol_Avg", volume_average)

# ROW 4 (EMA)
col11, col12, col13 = st.columns(3)

# EMA9 für das aktuell geladene Symbol
ema9 = round(df["EMA9"].iloc[-1], 2)
ema20 = round(df["EMA20"].iloc[-1], 2)
ema50 = round(df["EMA50"].iloc[-1], 2)

col11.metric("EMA9  \n (exponential moving average 9days)", ema9)
col12.metric("EMA20  \n (exponential moving average 20days)", ema20)
col13.metric("EMA50  \n (exponential moving average 50days)", ema50)

# ROW 5 (EMA)
col14, col15, col16 = st.columns(3)

ema200 = round(df["EMA200"].iloc[-1], 2)
atr = round(df["ATR"].iloc[-1], 2)
adx = round(df["ADX"].iloc[-1], 2)

col14.metric("EMA200  \n  (exponential moving average 200days)", ema200)
col15.metric("ATR  \n  (average true range)", atr)
col16.metric("ADX  \n  (average directional index)", adx)

score = 0

if df["Close"].iloc[-1] > df["EMA200"].iloc[-1]:
    score += 1
if df["EMA50"].iloc[-1] > df["EMA200"].iloc[-1]:
    score += 1
if df["ADX"].iloc[-1] > 25:
    score += 1

if score >= 2:
    trend = "Bullish"
elif score <= 1:
    trend = "Bearish"
else:
    trend = "Neutral"

color_map = {
    "Bullish": "green",
    "Bearish": "red",
    "Neutral": "gray"
}

st.markdown(
    f"<span style='color:{color_map[trend]}; font-size:24px; font-weight:bold'>Trend: {trend} (Score {score}/3)</span>",
    unsafe_allow_html=True
)

st.caption(f"Session: {SESSION}")

# -----------------------
# SUPPORT / RESISTANCE
# -----------------------
def detect_levels_pro(df, window=10, tolerance=0.002):
    supports = []
    resistances = []

    lows = df["Low"].to_numpy()
    highs = df["High"].to_numpy()
    times = np.arange(len(df))

    # -----------------------
    # 1. SWING DETECTION
    # -----------------------
    for i in range(window, len(df) - window):
        if lows[i] == min(lows[i-window:i+window]):
            supports.append((lows[i], i))
        if highs[i] == max(highs[i-window:i+window]):
            resistances.append((highs[i], i))

    # -----------------------
    # 2. CLUSTER LEVELS
    # -----------------------
    def cluster_levels(levels):
        clustered = []
        for price, idx in levels:
            found = False
            for c in clustered:
                if abs(price - c["price"]) / c["price"] < tolerance:
                    c["touches"] += 1
                    c["last_touch"] = idx
                    c["price"] = (c["price"] + price) / 2
                    found = True
                    break
            if not found:
                clustered.append({
                    "price": price,
                    "touches": 1,
                    "last_touch": idx
                })
        return clustered

    sup_cluster = cluster_levels(supports)
    res_cluster = cluster_levels(resistances)

    current_idx = len(df) - 1
    current_price = df["Close"].iloc[-1]

    # -----------------------
    # 3. SCORE LEVELS
    # -----------------------
    def score_level(level):
        age = current_idx - level["last_touch"]
        recency = np.exp(-age / 50)  # 🔥 decay
        strength = level["touches"]
        return strength * recency

    for l in sup_cluster:
        l["score"] = score_level(l)

    for l in res_cluster:
        l["score"] = score_level(l)

    # -----------------------
    # 4. BREAKOUT + FLIP
    # -----------------------
    flipped_supports = []
    flipped_resistances = []

    for r in res_cluster:
        if current_price > r["price"]:
            flipped_supports.append(r)

    for s in sup_cluster:
        if current_price < s["price"]:
            flipped_resistances.append(s)

    # -----------------------
    # 5. MERGE
    # -----------------------
    final_supports = sup_cluster + flipped_supports
    final_resistances = res_cluster + flipped_resistances

    # -----------------------
    # 6. SORT BY STRENGTH + DISTANCE
    # -----------------------
    def sort_levels(levels, direction="support"):
        return sorted(
            levels,
            key=lambda x: (
                -x["score"], 
                abs(x["price"] - current_price)
            )
        )

    final_supports = sort_levels(final_supports)[:5]
    final_resistances = sort_levels(final_resistances)[:5]

    return final_supports, final_resistances

sup_levels, res_levels = detect_levels_pro(df)

supports = [l["price"] for l in sup_levels]
resistances = [l["price"] for l in res_levels]


# -----------------------
# SUBPLOTS (FIXED UI)
# -----------------------

show_score = True
show_volume = True
show_rsi = True
show_macd = True

# -----------------------
# SUBPLOTS
# -----------------------
rows_map = {
    "Price": 1,
    "Volume": 2,
    "Score": 3,
    "RSI": 4,
    "MACD": 5
}
rows = 5
titles = ["Price", "Volume", "Score", "RSI", "MACD"]

row_heights = [0.55, 0.15, 0.12, 0.08, 0.1]  # Price groß, Volume sichtbar, andere klein

fig = make_subplots(
    rows=rows,
    cols=1,
    shared_xaxes=True,
    vertical_spacing=0.03,
    row_heights=row_heights,
    subplot_titles=titles
)

price_row = rows_map["Price"]
volume_row = rows_map["Volume"]
score_row = rows_map["Score"]
rsi_row = rows_map["RSI"]
macd_row = rows_map["MACD"]

# -----------------------
# PRICE CHART
# -----------------------
for sess, col in [("RTH", df[df["Session"]=="RTH"]),
                  ("PRE", df[df["Session"]=="PREMARKET"]),
                  ("AH", df[df["Session"]=="AFTERHOURS"])]:
    fig.add_trace(go.Candlestick(
        x=col.index, open=col["Open"], high=col["High"],
        low=col["Low"], close=col["Close"], name=sess,
        increasing_line_color='green' if sess=="RTH" else 'lightgreen',
        decreasing_line_color='red' if sess=="RTH" else 'lightcoral',
        opacity=1 if sess=="RTH" else 0.5
    ), row=price_row, col=1)

# VWAP
for sess, col, color in [("RTH", df[df["Session"]=="RTH"], "yellow"),
                         ("PRE", df[df["Session"]=="PREMARKET"], "orange"),
                         ("AH", df[df["Session"]=="AFTERHOURS"], "purple")]:
    col_name = f"VWAP_{sess}"
    if col_name in col.columns:
        fig.add_trace(go.Scatter(
            x=col.index, y=col[col_name], line=dict(color=color, width=3), name=col_name
        ), row=price_row, col=1)

# EMA
for ema in ["EMA20","EMA50","EMA200"]:
    if ema in df.columns:
        fig.add_trace(go.Scatter(x=df.index, y=df[ema], name=ema), row=price_row, col=1)

# Bollinger / Keltner
for bb in ["BB_UPPER","BB_MID","BB_LOWER"]:
    if bb in df.columns:
        dash = "dot" if bb != "BB_MID" else None
        fig.add_trace(go.Scatter(
            x=df.index, y=df[bb], name=bb, line=dict(width=1, dash=dash)
        ), row=price_row, col=1)

for kc in ["VWAP_upper2","VWAP_lower2","KC_UPPER","KC_MID","KC_LOWER"]:
    if kc in df.columns:
        line_kwargs = dict(line=dict(width=1))
        if kc == "KC_MID":
            line_kwargs["line"]["color"]="blue"
        fig.add_trace(go.Scatter(x=df.index, y=df[kc], name=kc, **line_kwargs), row=price_row, col=1)

# Signals
fig.add_trace(go.Scatter(
    x=df[df["LongSignal"]].index, y=df[df["LongSignal"]]["Close"],
    mode="markers+text",
    marker=dict(symbol="triangle-up", size=12, color="lime"),
    #text=[i.strftime("%Y-%m-%d %H:%M") for i in df[df["LongSignal"]].index],
    textposition="top center",
    hovertemplate="%{text}<br>Price: %{y}<extra></extra>",
    name="LONG"
), row=price_row, col=1)

fig.add_trace(go.Scatter(
    x=df[df["ShortSignal"]].index, y=df[df["ShortSignal"]]["Close"],
    mode="markers+text",
    marker=dict(symbol="triangle-down", size=12, color="red"),
    #text=[i.strftime("%Y-%m-%d %H:%M") for i in df[df["ShortSignal"]].index],
    textposition="bottom center",
    hovertemplate="%{text}<br>Price: %{y}<extra></extra>",
    name="SHORT"
), row=price_row, col=1)

# Support / Resistance
for s in supports:
    fig.add_hline(y=s, line_dash="dash", line_color="green",
                  row=price_row, col=1,
                  annotation_text=f"S {s:.2f}", annotation_position="bottom left")
for r in resistances:
    fig.add_hline(y=r, line_dash="dash", line_color="red",
                  row=price_row, col=1,
                  annotation_text=f"R {r:.2f}", annotation_position="top left")

# -----------------------
# SL / TP LINES
# -----------------------

# Letzten gültigen SL/TP finden
last_sl = df["SL"].dropna().iloc[-1] if df["SL"].notna().any() else None
last_tp = df["TP"].dropna().iloc[-1] if df["TP"].notna().any() else None

if last_sl is not None:
    fig.add_hline(
        y=last_sl,
        line_dash="dot",
        line_color="red",
        row=price_row, col=1,
        annotation_text=f"SL {last_sl:.2f}",
        annotation_position="bottom right"
    )

if last_tp is not None:
    fig.add_hline(
        y=last_tp,
        line_dash="dot",
        line_color="green",
        row=price_row, col=1,
        annotation_text=f"TP {last_tp:.2f}",
        annotation_position="top right"
    )
# -----------------------
# VOLUME
# -----------------------
if "Volume" in df.columns:
    fig.add_trace(go.Bar(x=df.index, y=df["Volume"], name="Volume"), row=volume_row, col=1)

    last_vol = df["Volume"].iloc[-1]

    fig.add_annotation(
        x=df.index[-1],
        y=last_vol,
        text=f"Vol {last_vol:.0f}",
        showarrow=False,
        xanchor="left",
        row=volume_row, col=1
    )
# -----------------------
# SCORE
# -----------------------
if "LongScore" in df.columns and "ShortScore" in df.columns:
    fig.add_trace(go.Scatter(x=df.index, y=df["LongScore"], name="Long Score",
                             line=dict(width=1, dash="dot")), row=score_row, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df["ShortScore"], name="Short Score",
                             line=dict(width=1, dash="dot")), row=score_row, col=1)
    fig.add_hline(y=5, line_dash="dash", row=score_row, col=1)
    fig.update_yaxes(range=[0,8], row=score_row, col=1)
    
    last_long_score = df["LongScore"].iloc[-1]
    last_short_score = df["ShortScore"].iloc[-1]

    fig.add_annotation(
        x=df.index[-1],
        y=last_long_score,
        text=f"L {last_long_score:.1f}",
        showarrow=False,
        row=score_row, col=1
    )

    fig.add_annotation(
        x=df.index[-1],
        y=last_short_score,
        text=f"S {last_short_score:.1f}",
        showarrow=False,
        row=score_row, col=1
    )

# -----------------------
# RSI
# -----------------------
if "RSI" in df.columns:
    fig.add_trace(go.Scatter(x=df.index, y=df["RSI"], name="RSI"), row=rsi_row, col=1)
    fig.add_hline(y=70, line_dash="dot", row=rsi_row, col=1)
    fig.add_hline(y=30, line_dash="dot", row=rsi_row, col=1)
    
    last_rsi = df["RSI"].iloc[-1]

    fig.add_annotation(
        x=df.index[-1],
        y=last_rsi,
        text=f"RSI {last_rsi:.1f}",
        showarrow=False,
        row=rsi_row, col=1
    )

# -----------------------
# MACD
# -----------------------
if "MACD" in df.columns and "MACD_signal" in df.columns and "MACD_hist" in df.columns:
    fig.add_trace(go.Scatter(x=df.index, y=df["MACD"], name="MACD"), row=macd_row, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df["MACD_signal"], name="Signal"), row=macd_row, col=1)
    fig.add_trace(go.Bar(x=df.index, y=df["MACD_hist"], name="Histogram"), row=macd_row, col=1)

    last_macd = df["MACD"].iloc[-1]
    last_signal = df["MACD_signal"].iloc[-1]

    fig.add_annotation(
        x=df.index[-1],
        y=last_macd,
        text=f"M {last_macd:.2f}",
        showarrow=False,
        row=macd_row, col=1
    )

    fig.add_annotation(
        x=df.index[-1],
        y=last_signal,
        text=f"S {last_signal:.2f}",
        showarrow=False,
        row=macd_row, col=1
    )
# -----------------------
# LAYOUT
# -----------------------
fig.update_layout(
    template="plotly_dark",
    height=1200,
    paper_bgcolor="#0e1117",
    plot_bgcolor="#0e1117",
    font=dict(color="#e6e6e6"),
    hovermode="x unified",
    xaxis_rangeslider_visible=False,
    dragmode="pan",
    uirevision="fixed"
)

# X-Achse NUR beim Price Chart anzeigen
for i in range(1, rows+1):
    if i == price_row:
        fig.update_xaxes(showticklabels=True, showspikes=True, rangebreaks=[dict(bounds=["sat", "mon"])], spikemode="across", row=i, col=1)
        fig.update_yaxes(showspikes=True)
    else:
        fig.update_xaxes(showticklabels=False, showspikes=True, rangebreaks=[dict(bounds=["sat", "mon"])], spikemode="across", row=i, col=1)  
        fig.update_yaxes(showspikes=True)
        
        
st.markdown("""
<style>

/* -------- GLOBAL DARK MODE -------- */
html, body, [class*="css"]  {
    background-color: #0e1117 !important;
    color: #e6e6e6 !important;
}

/* MAIN CONTAINER */
.stApp {
    background-color: #0e1117 !important;
}

/* BLOCK CONTAINER */
.block-container {
    background-color: #0e1117 !important;
}

/* TEXT FIX */
h1, h2, h3, h4, h5, h6, p, span, label {
    color: #e6e6e6 !important;
}

/* SIDEBAR */
section[data-testid="stSidebar"] {
    background-color: #111827 !important;
}

section[data-testid="stSidebar"] * {
    color: #e6e6e6 !important;
}

/* INPUTS */
input, textarea, div[data-baseweb="select"] {
    background-color: #1f2937 !important;
    color: #e6e6e6 !important;
}

/* BUTTONS */
.stButton>button {
    background-color: #1f2937 !important;
    color: #e6e6e6 !important;
    border-radius: 8px;
}

/* METRICS */
[data-testid="metric-container"] {
    background-color: #111827;
    padding: 12px;
    border-radius: 12px;
}

/* REMOVE WHITE BLOCKS */
[data-testid="stVerticalBlock"] {
    background-color: transparent !important;
}

/* Selectbox in Sidebar */
section[data-testid="stSidebar"] div[data-baseweb="select"] > div {
    background-color: #1f2937 !important;  /* dunkler Hintergrund */
    color: #f3f4f6 !important;             /* heller Text */
}

section[data-testid="stSidebar"] div[data-baseweb="select"] span {
    color: #f3f4f6 !important;             /* ausgewählter Text */
}

</style>
""", unsafe_allow_html=True)

st.plotly_chart(fig, use_container_width=True,
        config={
        "scrollZoom": True,   # 🔥 Gamechanger
        "displaylogo": False,
        "modeBarButtonsToRemove": [
            "zoom2d", "lasso2d", "select2d"
        ]
    })

# -----------------------
# ALERT / SIGNAL OUTPUT
# -----------------------

last_long = df["LongSignal"].iloc[-1]
last_short = df["ShortSignal"].iloc[-1]

MIN_SCORE = 5
DELTA_THRESHOLD = 2
MIN_RR = 1.5
VOLUME_FACTOR = 1.2

last_idx = len(df) - 1

long_score = df["LongScore"].iloc[last_idx]
short_score = df["ShortScore"].iloc[last_idx]
delta = long_score - short_score

# Signaltyp bestimmen
signal_type = None
if long_score >= MIN_SCORE and delta >= DELTA_THRESHOLD:
    signal_type = "LONG"
elif short_score >= MIN_SCORE and delta <= -DELTA_THRESHOLD:
    signal_type = "SHORT"
else:
    signal_type = "NEUTRAL"

confidence = max(long_score, short_score)

signal_idx = None
signal_type = None

for i in range(len(df)-1, -1, -1):
    long_score = df["LongScore"].iloc[i]
    short_score = df["ShortScore"].iloc[i]
    delta = long_score - short_score

    if long_score >= MIN_SCORE and delta >= DELTA_THRESHOLD:
        signal_idx = i
        signal_type = "LONG"
        break
    elif short_score >= MIN_SCORE and delta <= -DELTA_THRESHOLD:
        signal_idx = i
        signal_type = "SHORT"
        break
    
# Falls kein Signal gefunden, fallback auf letzte Kerze
if signal_idx is None:
    signal_idx = len(df)-1
    signal_type = "NEUTRAL"    
    
col20, col21, col22 = st.columns(3)

col20.metric(
    "Signal Strength",
    f"{confidence:.2f}"
)

col21.metric(
    "Long Score",
    f"{long_score:.2f}",
    delta=f"{long_score - short_score:.2f}"
)

col22.metric(
    "Short Score",
    f"{short_score:.2f}",
    delta=f"{short_score - long_score:.2f}"
)    

# SMART Signal (immer anzeigen)
if smart_type == "LONG":
    st.success(f"🚀 SMART LONG | Score: {long_score:.2f} | Δ {delta:.2f}")
elif smart_type == "SHORT":
    st.error(f"🔻 SMART SHORT | Score: {short_score:.2f} | Δ {delta:.2f}")
else:
    st.info("⚖️ NO CLEAR SIGNAL")

# A+ Signal (nur wenn vorhanden)
if signal:
    if signal["type"] == "LONG":
        st.success(
            f"🚀 A+ LONG\n"
            f"Entry: {signal['price']:.2f}\n"
            f"SL: {signal['sl']:.2f} | TP: {signal['tp']:.2f}\n"
            f"RR: {signal['rr']:.2f}"
        )
    else:
        st.error(
            f"🔻 A+ SHORT\n"
            f"Entry: {signal['price']:.2f}\n"
            f"SL: {signal['sl']:.2f} | TP: {signal['tp']:.2f}\n"
            f"RR: {signal['rr']:.2f}"
        )
else:
    st.info("⚖️ NO A+ SETUP")
        
