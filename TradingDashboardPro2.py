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
import sqlite3
from concurrent.futures import ThreadPoolExecutor

st.set_page_config(layout="wide")

st_autorefresh(interval=15000, key="refresh")  # 60s

def get_conn():
    os.makedirs("data", exist_ok=True)
    return sqlite3.connect("data/alerts.db", check_same_thread=False)

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

    # Triggered Tabelle
    c.execute("""
        CREATE TABLE IF NOT EXISTS triggered (
            ticker TEXT PRIMARY KEY,
            above INTEGER,
            below INTEGER
        )
    """)

    # Paper Trading Settings
    c.execute("""
        CREATE TABLE IF NOT EXISTS paper_settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    # Offene Positionen
    c.execute("""
        CREATE TABLE IF NOT EXISTS paper_positions (
            ticker TEXT PRIMARY KEY,
            direction TEXT,
            qty REAL,
            entry_price REAL,
            entry_time TEXT,
            sl REAL,
            tp REAL,
            status TEXT DEFAULT 'OPEN'
        )
    """)

    # Geschlossene und offene Trades Historie
    c.execute("""
        CREATE TABLE IF NOT EXISTS paper_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT,
            direction TEXT,
            qty REAL,
            entry_price REAL,
            entry_time TEXT,
            exit_price REAL,
            exit_time TEXT,
            sl REAL,
            tp REAL,
            pnl REAL,
            pnl_pct REAL,
            status TEXT,
            exit_reason TEXT
        )
    """)

    conn.commit()
    conn.close()


# Beim Start einmal ausführen
init_db()

# =========================================================
# 🔹 PAPER TRADING DB HELPERS
# =========================================================

def paper_get_setting(key, default=None):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT value FROM paper_settings WHERE key = ?", (key,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else default


def paper_set_setting(key, value):
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "INSERT OR REPLACE INTO paper_settings (key, value) VALUES (?, ?)",
        (key, str(value))
    )
    conn.commit()
    conn.close()


def paper_init_defaults():
    if paper_get_setting("cash") is None:
        paper_set_setting("cash", 10000)
    if paper_get_setting("start_cash") is None:
        paper_set_setting("start_cash", 10000)
    if paper_get_setting("last_signal_key") is None:
        paper_set_setting("last_signal_key", "")


def paper_reset_account(start_cash=10000):
    conn = get_conn()
    c = conn.cursor()
    c.execute("DELETE FROM paper_positions")
    c.execute("DELETE FROM paper_trades")
    c.execute("DELETE FROM paper_settings")
    conn.commit()
    conn.close()
    paper_set_setting("cash", start_cash)
    paper_set_setting("start_cash", start_cash)
    paper_set_setting("last_signal_key", "")


def paper_get_cash():
    return float(paper_get_setting("cash", 10000) or 10000)


def paper_set_cash(value):
    paper_set_setting("cash", round(float(value), 2))


def paper_get_open_position(ticker):
    conn = get_conn()
    query = pd.read_sql_query(
        "SELECT * FROM paper_positions WHERE ticker = ? AND status = 'OPEN'",
        conn,
        params=(ticker,)
    )
    conn.close()
    if query.empty:
        return None
    return query.iloc[0].to_dict()


def paper_get_open_positions():
    conn = get_conn()
    df_pos = pd.read_sql_query(
        "SELECT * FROM paper_positions WHERE status = 'OPEN' ORDER BY entry_time DESC",
        conn
    )
    conn.close()
    return df_pos


def paper_get_trade_history(limit=50):
    conn = get_conn()
    df_trades = pd.read_sql_query(
        "SELECT * FROM paper_trades ORDER BY id DESC LIMIT ?",
        conn,
        params=(limit,)
    )
    conn.close()
    return df_trades


def paper_open_position(ticker, direction, qty, entry_price, sl, tp, entry_time):
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        """
        INSERT OR REPLACE INTO paper_positions
        (ticker, direction, qty, entry_price, entry_time, sl, tp, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'OPEN')
        """,
        (ticker, direction, qty, entry_price, entry_time, sl, tp)
    )
    c.execute(
        """
        INSERT INTO paper_trades
        (ticker, direction, qty, entry_price, entry_time, sl, tp, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'OPEN')
        """,
        (ticker, direction, qty, entry_price, entry_time, sl, tp)
    )
    conn.commit()
    conn.close()


def paper_close_position(ticker, exit_price, exit_time, exit_reason):
    pos = paper_get_open_position(ticker)
    if not pos:
        return None

    qty = float(pos["qty"])
    entry_price = float(pos["entry_price"])
    direction = pos["direction"]

    if direction == "LONG":
        pnl = (exit_price - entry_price) * qty
        cash_delta = exit_price * qty
    else:
        pnl = (entry_price - exit_price) * qty
        cash_delta = entry_price * qty + pnl

    pnl_pct = ((exit_price - entry_price) / entry_price * 100) if direction == "LONG" else ((entry_price - exit_price) / entry_price * 100)

    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "UPDATE paper_positions SET status = 'CLOSED' WHERE ticker = ?",
        (ticker,)
    )
    c.execute(
        """
        UPDATE paper_trades
        SET exit_price = ?, exit_time = ?, pnl = ?, pnl_pct = ?, status = 'CLOSED', exit_reason = ?
        WHERE id = (
            SELECT id FROM paper_trades
            WHERE ticker = ? AND status = 'OPEN'
            ORDER BY id DESC LIMIT 1
        )
        """,
        (exit_price, exit_time, pnl, pnl_pct, exit_reason, ticker)
    )
    conn.commit()
    conn.close()

    paper_set_cash(paper_get_cash() + cash_delta)
    return {
        "ticker": ticker,
        "direction": direction,
        "pnl": pnl,
        "pnl_pct": pnl_pct,
        "exit_reason": exit_reason,
        "exit_price": exit_price
    }


def paper_account_snapshot(live_prices=None):
    live_prices = live_prices or {}
    cash = paper_get_cash()
    start_cash = float(paper_get_setting("start_cash", 10000) or 10000)
    positions = paper_get_open_positions()

    market_value = 0.0
    unrealized = 0.0

    if not positions.empty:
        for _, row in positions.iterrows():
            ticker = row["ticker"]
            direction = row["direction"]
            qty = float(row["qty"])
            entry = float(row["entry_price"])
            live = float(live_prices.get(ticker, entry))

            if direction == "LONG":
                market_value += live * qty
                unrealized += (live - entry) * qty
            else:
                unrealized += (entry - live) * qty
                market_value += entry * qty + (entry - live) * qty

    equity = cash + market_value
    total_pnl = equity - start_cash
    total_pnl_pct = (total_pnl / start_cash * 100) if start_cash else 0

    return {
        "cash": cash,
        "market_value": market_value,
        "equity": equity,
        "unrealized": unrealized,
        "total_pnl": total_pnl,
        "total_pnl_pct": total_pnl_pct
    }


def paper_process_symbol(symbol, signal, current_price, now_str, auto_enabled=True, notify=False):
    pos = paper_get_open_position(symbol)
    closed_info = None
    opened_info = None

    if pos:
        direction = pos["direction"]
        sl = float(pos["sl"])
        tp = float(pos["tp"])

        if direction == "LONG":
            if current_price <= sl:
                closed_info = paper_close_position(symbol, current_price, now_str, "SL")
            elif current_price >= tp:
                closed_info = paper_close_position(symbol, current_price, now_str, "TP")
            elif signal and signal.get("type") == "SHORT":
                closed_info = paper_close_position(symbol, current_price, now_str, "REVERSE")

        elif direction == "SHORT":
            if current_price >= sl:
                closed_info = paper_close_position(symbol, current_price, now_str, "SL")
            elif current_price <= tp:
                closed_info = paper_close_position(symbol, current_price, now_str, "TP")
            elif signal and signal.get("type") == "LONG":
                closed_info = paper_close_position(symbol, current_price, now_str, "REVERSE")

    pos = paper_get_open_position(symbol)

    if auto_enabled and signal and not pos:
        signal_key = f"{symbol}_{signal['type']}_{df.index[-1]}"
        last_signal_key = paper_get_setting("last_signal_key", "")

        if signal_key != last_signal_key:
            cash = paper_get_cash()
            risk_fraction = float(paper_get_setting("risk_fraction", 0.25) or 0.25)
            order_value = cash * risk_fraction
            qty = max(order_value / current_price, 0)

    if qty > 0:
        # -----------------------
        # SICHERHEITSSCHUTZ SL/TP
        # -----------------------
        sig_type = signal["type"]
        sig_sl = float(signal["sl"])
        sig_tp = float(signal["tp"])
        sig_entry = float(current_price)

        valid_signal = True

        if sig_type == "LONG":
            if not (sig_sl < sig_entry < sig_tp):
                valid_signal = False
                st.warning(
                    f"Paper-Trade blockiert: LONG ungültig "
                    f"(SL={sig_sl:.2f}, Entry={sig_entry:.2f}, TP={sig_tp:.2f})"
                )

        elif sig_type == "SHORT":
            if not (sig_tp < sig_entry < sig_sl):
                valid_signal = False
                st.warning(
                    f"Paper-Trade blockiert: SHORT ungültig "
                    f"(TP={sig_tp:.2f}, Entry={sig_entry:.2f}, SL={sig_sl:.2f})"
                )

        if valid_signal:
            if sig_type == "LONG":
                paper_set_cash(cash - (qty * current_price))

            paper_open_position(
                ticker=symbol,
                direction=sig_type,
                qty=qty,
                entry_price=current_price,
                sl=sig_sl,
                tp=sig_tp,
                entry_time=now_str
            )

            paper_set_setting("last_signal_key", signal_key)

            opened_info = {
                "ticker": symbol,
                "direction": sig_type,
                "qty": qty,
                "entry_price": current_price,
                "sl": sig_sl,
                "tp": sig_tp
            }

            if notify:
                send_telegram(
                    f"🧪 PAPER {sig_type} {symbol}\n"
                    f"Entry: {current_price:.2f}\n"
                    f"SL: {sig_sl:.2f}\n"
                    f"TP: {sig_tp:.2f}\n"
                    f"Qty: {qty:.4f}"
            )

    return opened_info, closed_info



def paper_manual_open(symbol, direction, current_price, sl, tp, now_str, qty=None, risk_fraction=None, notify=False):
    pos = paper_get_open_position(symbol)
    if pos:
        return None, f"{symbol}: Bereits offene Position vorhanden"

    cash = paper_get_cash()

    if qty is None:
        if risk_fraction is None:
            risk_fraction = float(paper_get_setting("risk_fraction", 0.25) or 0.25)
        order_value = cash * float(risk_fraction)
        qty = max(order_value / current_price, 0)

    qty = float(qty)
    if qty <= 0:
        return None, f"{symbol}: Qty muss größer als 0 sein"

    if direction == "LONG":
        required_cash = qty * current_price
        if required_cash > cash:
            return None, f"{symbol}: Nicht genug Cash für LONG"
        paper_set_cash(cash - required_cash)

    paper_open_position(
        ticker=symbol,
        direction=direction,
        qty=qty,
        entry_price=current_price,
        sl=float(sl),
        tp=float(tp),
        entry_time=now_str
    )

    opened_info = {
        "ticker": symbol,
        "direction": direction,
        "qty": qty,
        "entry_price": current_price,
        "sl": float(sl),
        "tp": float(tp)
    }

    if notify:
        send_telegram(
            f"🧪 PAPER MANUAL {direction} {symbol}\n"
            f"Entry: {current_price:.2f}\n"
            f"SL: {float(sl):.2f}\n"
            f"TP: {float(tp):.2f}\n"
            f"Qty: {qty:.4f}"
        )

    return opened_info, None

paper_init_defaults()

def get_market_session():
    et = pytz.timezone("America/New_York")
    now = datetime.now(et)

    weekday = now.weekday()
    current_time = now.time()

    if weekday >= 5:
        return "WEEKEND"

    if time(4, 0) <= current_time < time(9, 30):
        return "PREMARKET"
    elif time(9, 30) <= current_time < time(16, 0):
        return "RTH"
    elif time(16, 0) <= current_time < time(20, 0):
        return "AFTERHOURS"
    else:
        return "CLOSED"

SESSION = get_market_session()
# =========================================================
# 🔹 DEFAULT INPUTS
# =========================================================

symbol = "BTC-USD"          # oder dein Default
period = "5d"            # z.B. 1d, 5d, 1mo
interval = "5m"          # 1m, 5m, 15m

# -----------------------
# DATA
# -----------------------
@st.cache_data(ttl=15)
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

@st.cache_data(ttl=15)
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

# =========================================================
# 🔹 TELEGRAM + ALERTS
# =========================================================

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


def check_alerts():
    alerts = load_alerts_sql()
    triggered = load_triggered_sql()

    if not alerts:
        return

    for ticker, levels in alerts.items():
        try:
            data = yf.download(ticker, period="5d", interval="5m", progress=False)
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

        # ABOVE
        if levels["above"] > 0:
            if price >= levels["above"] and not triggered[ticker]["above"]:
                send_telegram(f"🚀 {ticker} über {levels['above']} → {price:.2f}")
                triggered[ticker]["above"] = True

            if price < levels["above"]:
                triggered[ticker]["above"] = False

        # BELOW
        if levels["below"] > 0:
            if price <= levels["below"] and not triggered[ticker]["below"]:
                send_telegram(f"📉 {ticker} unter {levels['below']} → {price:.2f}")
                triggered[ticker]["below"] = True

            if price > levels["below"]:
                triggered[ticker]["below"] = False

    save_triggered_sql(triggered)
    
@st.cache_data(ttl=60)
def run_alerts():
    check_alerts()

run_alerts()
    
# =========================================================
# 🔹 SIDEBAR INPUTS
# =========================================================


if "symbol" not in st.session_state:
    st.session_state.symbol = "BTC-USD"

symbol_input = st.sidebar.text_input(
    "Ticker",
    value=st.session_state.symbol,
    key="ticker_input"
).upper()

if symbol_input != st.session_state.symbol:
    st.session_state.symbol = symbol_input

symbol = st.session_state.symbol

valid_map = {
    "1m": ["1d", "5d"],
    "5m": ["1d", "5d"],
    "15m": ["5d", "1mo"],
    "1h": ["1mo", "3mo"],
    "4h": ["3mo", "6mo"],
    "1d": ["1y", "max"]
}

if "interval_select" not in st.session_state:
    st.session_state.interval_select = "5m"

if "period_select" not in st.session_state:
    st.session_state.period_select = valid_map["5m"][1]

interval = st.sidebar.selectbox(
    "Timeframe",
    list(valid_map.keys()),
    key="interval_select"
)

valid_periods = valid_map[interval]

if st.session_state.period_select not in valid_periods:
    st.session_state.period_select = valid_periods[0]

period = st.sidebar.selectbox(
    "Period",
    valid_periods,
    index=valid_periods.index(st.session_state.period_select),
    key="period_select"
)

st.sidebar.caption(f"Aktive Kombi: {interval} / {period}")

if st.sidebar.button("🔄 Refresh Data"):
    st.cache_data.clear()
    st.rerun()

if st.sidebar.button("🧪 Test LONG Signal"):
    send_telegram("🚀 TEST LONG SIGNAL funktioniert!")
    
# =========================================================
# 🔹 ALERT SIDEBAR
# =========================================================

st.sidebar.header("📊 Preisalarme")

PriceAlerttickers = []

for i in range(2):
    ticker_input = st.sidebar.text_input(f"Ticker {i+1}", key=f"alert_ticker_{i}")
    label = ticker_input if ticker_input else f"Ticker {i+1}"

    price_above = st.sidebar.number_input(f"{label} ≥ Preis", key=f"above_{i}", value=0.0)
    price_below = st.sidebar.number_input(f"{label} ≤ Preis", key=f"below_{i}", value=0.0)

    if ticker_input:
        PriceAlerttickers.append({
            "ticker": ticker_input.upper(),
            "above": price_above,
            "below": price_below
        })

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

st.sidebar.markdown("---")
st.sidebar.subheader("🔔 Gespeicherte Preisalarme")

saved_alerts = load_alerts_sql()

if not saved_alerts:
    st.sidebar.caption("Keine gespeicherten Alarme")
else:
    for ticker, levels in saved_alerts.items():
        above = levels.get("above", 0)
        below = levels.get("below", 0)

        text_parts = []

        if above and above > 0:
            text_parts.append(f"≥ {above}")
        if below and below > 0:
            text_parts.append(f"≤ {below}")

        alert_text = " | ".join(text_parts) if text_parts else "keine Levels"

        st.sidebar.write(f"**{ticker}**")
        st.sidebar.caption(alert_text)

# =========================================================
# 🔹 PAPER TRADING SIDEBAR
# =========================================================

st.sidebar.markdown("---")
st.sidebar.subheader("🧪 Paper Trading")

current_start_cash = float(paper_get_setting("start_cash", 10000) or 10000)
current_risk_fraction = float(paper_get_setting("risk_fraction", 0.25) or 0.25)
current_auto_mode = (paper_get_setting("auto_mode", "0") == "1")
current_paper_telegram = (paper_get_setting("paper_telegram", "0") == "1")

paper_start_cash_input = st.sidebar.number_input(
    "Startkapital",
    min_value=1000.0,
    value=current_start_cash,
    step=1000.0,
    key="paper_start_cash_input"
)

paper_risk_fraction_input = st.sidebar.slider(
    "Einsatz pro Trade (% vom Cash)",
    min_value=5,
    max_value=100,
    value=int(current_risk_fraction * 100),
    step=5,
    key="paper_risk_fraction_input"
)

paper_auto_mode = st.sidebar.checkbox(
    "Auto-Trade bei A+ Signal",
    value=current_auto_mode,
    key="paper_auto_mode"
)

paper_telegram = st.sidebar.checkbox(
    "Telegram für Paper-Trades",
    value=current_paper_telegram,
    key="paper_telegram"
)

if st.sidebar.button("💾 Paper Settings speichern"):
    paper_set_setting("start_cash", paper_start_cash_input)
    if paper_get_setting("cash") is None:
        paper_set_setting("cash", paper_start_cash_input)
    paper_set_setting("risk_fraction", paper_risk_fraction_input / 100)
    paper_set_setting("auto_mode", "1" if paper_auto_mode else "0")
    paper_set_setting("paper_telegram", "1" if paper_telegram else "0")
    st.sidebar.success("Paper Settings gespeichert")

if st.sidebar.button("🗑️ Paper Konto reset"):
    paper_reset_account(paper_start_cash_input)
    st.sidebar.success("Paper Konto zurückgesetzt")
    st.rerun()
# =========================================================
# 🔹 MARKET SCANNER
# =========================================================


@st.cache_data(ttl=86400)
def get_sp500_symbols():
    return [
        "AAPL","MSFT","NVDA","AMZN","META","GOOGL","TSLA",
        "AVGO","TSM","AMD","NFLX","INTC","ADBE","CRM","LITE",
        "COIN","PLTR","RIVN","SOFI","SNAP","ROKU",
        "UPST","AFRM","DKNG","SHOP","SQ","PYPL",
        "SMCI","ARM","MU","ASML","LRCX","KLAC","MRVL",
        "JPM","GS","BAC","MS","SCHW",
        "XOM","CVX","OXY","SLB","HAL","ENR.DE",
        "LLY","UNH","JNJ","MRNA","BNTX",
        "CAT","BA","GE","DE","NOC",
        "SPY","QQQ","IWM","DIA","XLF","XLK","XLE",
        "^GSPC","^NDX","^DJI",
        "VIXY","UVXY",
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

@st.cache_data(ttl=300)
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
    except Exception:
        return data_all

    if isinstance(d.columns, pd.MultiIndex):
        for ticker in symbols:
            if ticker in d.columns.get_level_values(0):
                df_t = d[ticker].copy()
                if isinstance(df_t.columns, pd.MultiIndex):
                    df_t.columns = df_t.columns.get_level_values(0)
                df_t = df_t.dropna(subset=["Close"])
                if not df_t.empty:
                    data_all[ticker] = df_t
    else:
        d = d.dropna(subset=["Close"]) if "Close" in d.columns else d.dropna()
        if not d.empty and len(symbols) > 0:
            data_all[symbols[0]] = d

    return data_all

def mark_premarket(df):
    if not isinstance(df, pd.DataFrame):
        return df

    if not isinstance(df.index, pd.DatetimeIndex):
        return df

    try:
        # 🔹 Zeitzone sicherstellen
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")

        df.index = df.index.tz_convert("America/New_York")

        times = df.index.time

        df["Session"] = np.where(
            (times >= time(4, 0)) & (times < time(9, 30)),
            "PREMARKET",
            np.where(
                (times >= time(16, 0)) & (times < time(20, 0)),
                "AFTERHOURS",
                "RTH"
            )
        )

    except Exception as e:
        print("Session Error:", e)

    return df
# ATR
def compute_atr(df):
    tr = np.maximum(
        df["High"] - df["Low"],
        np.maximum(
            abs(df["High"] - df["Close"].shift(1)),
            abs(df["Low"] - df["Close"].shift(1))
        )
    )
    return tr.ewm(span=14, adjust=False).mean()
def process_symbol(s, data_all):
    df_s = data_all.get(s)

    if df_s is None or len(df_s) < 10:
        return None

    df_s = df_s.copy()

    if not isinstance(df_s.index, pd.DatetimeIndex):
        df_s.index = pd.to_datetime(df_s.index, errors="coerce")

    df_s = df_s.dropna()
    if df_s.empty:
        return None

    df_s = mark_premarket(df_s)

    if "Session" not in df_s.columns:
        df_s["Session"] = "RTH"

    # Core indicators
    df_s["ATR"] = compute_atr(df_s)

    up_move = df_s["High"].diff()
    down_move = -df_s["Low"].diff()

    df_s["+DM"] = np.where((up_move > down_move) & (up_move > 0), up_move, 0)
    df_s["-DM"] = np.where((down_move > up_move) & (down_move > 0), down_move, 0)

    plus_dm = df_s["+DM"].ewm(span=14, adjust=False).mean()
    minus_dm = df_s["-DM"].ewm(span=14, adjust=False).mean()

    df_s["+DI"] = 100 * (plus_dm / df_s["ATR"])
    df_s["-DI"] = 100 * (minus_dm / df_s["ATR"])

    dx = (np.abs(df_s["+DI"] - df_s["-DI"]) / (df_s["+DI"] + df_s["-DI"])) * 100
    df_s["ADX"] = dx.ewm(span=14, adjust=False).mean().fillna(0)

    # VWAP
    tp = (df_s["High"] + df_s["Low"] + df_s["Close"]) / 3
    df_s["Vol_RTH"] = np.where(df_s["Session"] == "RTH", df_s["Volume"], np.nan)
    df_s["Vol_Avg_RTH"] = pd.Series(df_s["Vol_RTH"], index=df_s.index).rolling(20, min_periods=5).mean()

    vol_rth = np.where(df_s["Session"] == "RTH", df_s["Volume"], 0)
    pv_rth = tp * vol_rth
    df_s["VWAP_RTH"] = (
        pd.Series(pv_rth, index=df_s.index).groupby(df_s.index.date).cumsum() /
        pd.Series(vol_rth, index=df_s.index).groupby(df_s.index.date).cumsum().replace(0, np.nan)
    )

    df_s["VWAP_PRE"] = df_s["VWAP_RTH"]
    df_s["VWAP_AH"] = df_s["VWAP_RTH"]

    price = df_s["Close"].iloc[-1]
    avg_vol = df_s["Vol_Avg_RTH"].iloc[-1]

    if pd.isna(avg_vol) or avg_vol == 0:
        return None

    dollar_vol = price * avg_vol

    score = 0
    if dollar_vol > 5_000_000:
        score += 1
    if dollar_vol > 20_000_000:
        score += 1

    atr = df_s["ATR"].iloc[-1]
    atr_pct = atr / price if price != 0 else np.nan

    if pd.notna(atr_pct) and atr_pct > 0.003:
        score += 1
    if pd.notna(atr_pct) and atr_pct > 0.01:
        score += 1

    rel_vol = df_s["Volume"].iloc[-1] / avg_vol
    if rel_vol > 1.2:
        score += 1
    if rel_vol > 1.5:
        score += 1

    ema20 = df_s["Close"].ewm(span=20).mean()
    ema50 = df_s["Close"].ewm(span=50).mean()

    rsi = 100 - (100 / (1 + (
        df_s["Close"].diff().clip(lower=0).ewm(alpha=1/14).mean() /
        (-df_s["Close"].diff().clip(upper=0).ewm(alpha=1/14).mean().replace(0, 1e-10))
    ))).iloc[-1]

    vwap = df_s["VWAP_RTH"].iloc[-1]

    long_score = 0
    short_score = 0

    if price > vwap:
        long_score += 1
    else:
        short_score += 1

    if ema20.iloc[-1] > ema50.iloc[-1]:
        long_score += 1
    else:
        short_score += 1

    if rsi > 55:
        long_score += 1
    elif rsi < 45:
        short_score += 1

    total_score = max(long_score, short_score) + score
    if total_score < 7:
        return None

    setup = "LONG" if long_score > short_score else "SHORT"

    # Scanner-SLTP-Version
    price_i = df_s["Close"].iloc[-1]
    atr_i = df_s["ATR"].iloc[-1]
    vwap_i = df_s["VWAP_RTH"].iloc[-1]

    if pd.isna(price_i) or pd.isna(atr_i) or pd.isna(vwap_i):
        return None

    min_risk = atr_i * 0.5
    max_risk = atr_i * 3

    if setup == "LONG":
        swing_low = df_s["Low"].iloc[max(0, len(df_s)-11):len(df_s)-1].min()
        if pd.isna(swing_low):
            swing_low = price_i - atr_i
        sl = min(swing_low, vwap_i - atr_i * 0.5)
        if sl >= price_i:
            sl = price_i - min_risk
        risk = max(min_risk, min(price_i - sl, max_risk))
        tp = price_i + risk * 2
    else:
        swing_high = df_s["High"].iloc[max(0, len(df_s)-11):len(df_s)-1].max()
        if pd.isna(swing_high):
            swing_high = price_i + atr_i
        sl = max(swing_high, vwap_i + atr_i * 0.5)
        if sl <= price_i:
            sl = price_i + min_risk
        risk = max(min_risk, min(sl - price_i, max_risk))
        tp = price_i - risk * 2

    if pd.isna(sl) or pd.isna(tp) or price_i == sl:
        return None

    # Risk/Reward
    #👉 A+ Setup: RR ≥ 2.0 – 3.0 + Score >= 8
    #👍 Normales Setup: 👉 RR ≥ 1.5 Score 5-7
    rr = abs(tp - price_i) / abs(price_i - sl)
    if total_score >= 8:
        min_rr = 2.0
    elif total_score >= 6:
        min_rr = 1.5
    else:
        return None

    signal_ok = rr >= min_rr

    return {
        "symbol": s,
        "setup": setup,
        "price": price_i,
        "sl": sl,
        "tp": tp,
        "rr": rr,
        "score": total_score,
        "delta": long_score - short_score,
        "signal_ok": signal_ok,
        "timestamp": df_s.index[-1]
    }

def scan_market_core(symbols, data_all):
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda s: process_symbol(s, data_all), symbols))

    return [r for r in results if r is not None]

@st.cache_data(ttl=300, max_entries=1)
def scan_market(limit=50):
    symbols = filter_symbols_by_session(get_sp500_symbols(), SESSION)[:limit]
    data_all = download_data(symbols)
    results = scan_market_core(symbols, data_all)

    gainers = [r for r in results if r["setup"] == "LONG"]
    losers = [r for r in results if r["setup"] == "SHORT"]

    gainers = sorted(gainers, key=lambda x: x["score"], reverse=True)
    losers = sorted(losers, key=lambda x: x["score"], reverse=True)

    for r in results:
        if not r["signal_ok"]:
            continue

        signal_id = f"{r['symbol']}_{r['setup']}_{r['timestamp']}"

        if "sent_signals" not in st.session_state:
            st.session_state.sent_signals = set()

        if signal_id not in st.session_state.sent_signals:
            send_telegram(
                f"🚨 {r['symbol']} {r['setup']}\n\n"
                f"Entry: {r['price']:.2f}\n"
                f"SL: {r['sl']:.2f}\n"
                f"TP: {r['tp']:.2f}\n"
                f"RR: {r['rr']:.2f}\n\n"
                f"Score: {r['score']} | Δ {r['delta']}"
            )
            st.session_state.sent_signals.add(signal_id)

    def pad(x):
        return x[:10] + [{}] * max(0, 10 - len(x))

    return pad(gainers), pad(losers)


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

# =========================================================
# 🔹 SCANNER SIDEBAR UI
# =========================================================

st.sidebar.markdown("---")
st.sidebar.subheader("🔥 Scanner PRO MAX")

limit = st.sidebar.slider(
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
        st.sidebar.caption("Weekend Mode: nur Crypto + Futures")
    elif SESSION != "RTH":
        st.sidebar.caption(f"Off-hours: {SESSION}")

st.sidebar.write("Scanner UI geladen")

gainers, losers = [], []

try:
    gainers, losers = scan_market(limit)
    st.sidebar.write("scan_market fertig")
except Exception as e:
    st.sidebar.error(f"Scanner Fehler: {e}")

render_list("Top Momentum ↑", gainers)
render_list("Top Breakdown ↓", losers)

if st.sidebar.button("♻️ Full Reset"):
    st.cache_data.clear()
    st.session_state.clear()
    st.rerun()
st.sidebar.markdown("---")
  
@st.cache_data(ttl=120)
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

        df_us.index = df_us.index.tz_convert("America/New_York")
    except Exception as e:
        print("TZ Error:", e)

    # -----------------------
    # MERGE
    # -----------------------
    df_us["EU_Close"] = df_eu["Close"].reindex(df_us.index, method="ffill")
    df_us["Spread"] = df_us["Close"] - df_us["EU_Close"]

    return df_us, symbol_eu


# =========================================================
# 🔹 1. DATA LOADING & CLEANING
# =========================================================

df, symbol_eu = load_multi_exchange(symbol, period, interval)

def normalize_df(df):
    # MultiIndex entfernen
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    # Sicherstellen: alle OHLCV sind Series
    for col in ["Open", "High", "Low", "Close", "Volume"]:
        if col in df.columns and isinstance(df[col], pd.DataFrame):
            df[col] = df[col].squeeze()

    return df

df = normalize_df(df)
df = df.loc[:, ~df.columns.duplicated()]

if df.empty:
    st.warning("Keine Daten verfügbar")
    st.stop()

def add_session_column(df):
    df = df.copy()

    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index, errors="coerce")

    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")

    df.index = df.index.tz_convert("America/New_York")

    times = df.index.time

    df["Session"] = np.where(
        (times >= time(4, 0)) & (times < time(9, 30)),
        "PREMARKET",
        np.where(
            (times >= time(16, 0)) & (times < time(20, 0)),
            "AFTERHOURS",
            "RTH"
        )
    )

    return df

df = add_session_column(df)
# =========================================================
# 🔹 2. BASIC CLEANUP
# =========================================================

# Volume clean
df["Volume"] = df["Volume"].replace(0, np.nan)
df["Volume"] = df["Volume"].ffill()

# EU Close absichern
if "EU_Close" not in df.columns:
    df["EU_Close"] = np.nan

if df["EU_Close"].notna().any():
    df["EU_Close"] = df["EU_Close"].ffill()

# Spread
df["Spread"] = df["Close"] - df["EU_Close"].fillna(df["Close"])

df = df.tail(300)

# =========================================================
# 🔹 3. MTF (Multi Timeframe)
# =========================================================

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

    if isinstance(close, pd.DataFrame):
        close = close.iloc[:, 0]

    ema20 = close.ewm(span=20).mean()
    ema50 = close.ewm(span=50).mean()

    if ema50.dropna().empty:
        return "neutral"

    return "bull" if ema20.iloc[-1] > ema50.iloc[-1] else "bear"

bias_5m = mtf_bias(df_5m)
bias_15m = mtf_bias(df_15m)

# =========================================================
# 🔹 4. INDICATORS (CLEAN VERSION)
# =========================================================

# EMA (nur einmal!)
df["EMA9"]   = df["Close"].ewm(span=9, adjust=False).mean()
df["EMA20"]  = df["Close"].ewm(span=20, adjust=False).mean()
df["EMA50"]  = df["Close"].ewm(span=50, adjust=False).mean()
df["EMA200"] = df["Close"].ewm(span=200, adjust=False).mean()



df["ATR"] = compute_atr(df)
df["ATR_pct"] = df["ATR"] / df["Close"]

# RSI
delta = df["Close"].diff()
gain = delta.clip(lower=0)
loss = -delta.clip(upper=0)

avg_gain = gain.ewm(alpha=1/14).mean()
avg_loss = loss.ewm(alpha=1/14).mean()

rs = avg_gain / avg_loss.replace(0, 1e-10)
df["RSI"] = 100 - (100 / (1 + rs))

# MACD
ema12 = df["Close"].ewm(span=12).mean()
ema26 = df["Close"].ewm(span=26).mean()

df["MACD"] = ema12 - ema26
df["MACD_signal"] = df["MACD"].ewm(span=9).mean()
df["MACD_hist"] = df["MACD"] - df["MACD_signal"]

# =========================================================
# 🔹 5. VOLUME LOGIC (FIXED)
# =========================================================

df["Vol_RTH"] = np.where(df["Session"] == "RTH", df["Volume"], np.nan)
df["Vol_Avg_RTH"] = df["Vol_RTH"].rolling(20, min_periods=5).mean()

df["Vol_Current"] = df["Volume"]
df["Vol_Avg"] = df["Volume"].rolling(20, min_periods=5).mean()

# =========================================================
# 🔹 6. VWAP SUITE (CLEAN)
# =========================================================

def compute_vwap_suite(df):
    df = df.copy()

    # saubere Basis
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index, errors="coerce")

    tp = (df["High"] + df["Low"] + df["Close"]) / 3

    df["VWAP_RTH"] = np.nan
    df["VWAP_PRE"] = np.nan
    df["VWAP_AH"] = np.nan

    # pro Tag und pro Session separat rechnen
    for _, day_df in df.groupby(df.index.date):

        for session_name, target_col in [
            ("RTH", "VWAP_RTH"),
            ("PREMARKET", "VWAP_PRE"),
            ("AFTERHOURS", "VWAP_AH"),
        ]:
            session_df = day_df[day_df["Session"] == session_name]

            if session_df.empty:
                continue

            session_tp = tp.loc[session_df.index]
            session_vol = session_df["Volume"].fillna(0)

            cum_pv = (session_tp * session_vol).cumsum()
            cum_vol = session_vol.cumsum().replace(0, np.nan)

            df.loc[session_df.index, target_col] = cum_pv / cum_vol

    return df

df = compute_vwap_suite(df)

# =========================================================
# 🔹 7. VWAP BANDS
# =========================================================

typical_price = (df["High"] + df["Low"] + df["Close"]) / 3
vwap_dev = (typical_price - df["VWAP_RTH"]).rolling(20).std()

df["VWAP_upper2"] = df["VWAP_RTH"] + 2 * vwap_dev
df["VWAP_lower2"] = df["VWAP_RTH"] - 2 * vwap_dev

# =========================================================
# 🔹 8. DMI / ADX (FIXED BUG)
# =========================================================

up_move = df["High"].diff()
down_move = -df["Low"].diff()

df["+DM"] = np.where((up_move > down_move) & (up_move > 0), up_move, 0)
df["-DM"] = np.where((down_move > up_move) & (down_move > 0), down_move, 0)

atr = df["ATR"]

df["+DI"] = 100 * (pd.Series(df["+DM"]).ewm(span=14).mean() / atr)
df["-DI"] = 100 * (pd.Series(df["-DM"]).ewm(span=14).mean() / atr)

dx = (abs(df["+DI"] - df["-DI"]) / (df["+DI"] + df["-DI"])) * 100
df["ADX"] = dx.ewm(span=14).mean().fillna(0)

# Trend Flags
df["Trend_Strong"] = df["ADX"] > 25
df["Trend_Long"] = df["+DI"] > df["-DI"]
df["Trend_Short"] = df["-DI"] > df["+DI"]  # 🔥 FIXED

# =========================================================
# 🔹 9. KELTNER CHANNEL
# =========================================================

kc_mult = 1.5
df["KC_MID"] = df["EMA20"]
df["KC_UPPER"] = df["KC_MID"] + kc_mult * df["ATR"]
df["KC_LOWER"] = df["KC_MID"] - kc_mult * df["ATR"]

df["KC_Above"] = df["Close"] > df["KC_UPPER"]
df["KC_Below"] = df["Close"] < df["KC_LOWER"]

# =========================================================
# 🔹 10. BOLLINGER BANDS
# =========================================================

df["BB_MID"] = df["Close"].rolling(20).mean()
df["BB_STD"] = df["Close"].rolling(20).std()

df["BB_UPPER"] = df["BB_MID"] + 2 * df["BB_STD"]
df["BB_LOWER"] = df["BB_MID"] - 2 * df["BB_STD"]

df["BB_WIDTH"] = df["BB_UPPER"] - df["BB_LOWER"]

# =========================================================
# 🔹 11. DELTA
# =========================================================

df["delta"] = np.where(df["Close"] > df["Open"], df["Volume"], -df["Volume"])
df["cum_delta"] = df["delta"].cumsum()

# =========================================================
# 🔹 12. MARKET REGIME
# =========================================================

def detect_market_regime(df):
    if "VWAP_RTH" not in df.columns:
        return "UNKNOWN"

    price = df["Close"]
    vwap = df["VWAP_RTH"]

    crosses = ((price > vwap) != (price.shift(1) > vwap.shift(1))).rolling(20).sum()

    ema20 = df["EMA20"]
    ema50 = df["EMA50"]
    trend_strength = abs(ema20 - ema50) / price

    if crosses.iloc[-1] > 3 and trend_strength.iloc[-1] < 0.002:
        return "RANGE"
    else:
        return "TREND"

df["MarketRegime"] = detect_market_regime(df)

# =========================================================
# 🔹 13. SELL THE NEWS DETECTOR
# =========================================================

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

    # SHORT
    if (
        prev["sweep_high"] and
        prev["Close"] > prev["VWAP_RTH"] and
        curr["Close"] < curr["VWAP_RTH"] and
        curr["Close"] < prev["Close"]
    ):
        df.at[df.index[i], "SellNewsShort"] = True

    # LONG
    if (
        prev["sweep_low"] and
        prev["Close"] < prev["VWAP_RTH"] and
        curr["Close"] > curr["VWAP_RTH"] and
        curr["Close"] > prev["Close"]
    ):
        df.at[df.index[i], "SellNewsLong"] = True


# =========================================================
# 🔹 14. SMART MONEY BASE FEATURES
# =========================================================

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

# Init Scores
df["LongScore"] = 0.0
df["ShortScore"] = 0.0

# =========================================================
# 🔹 15. SCORING ENGINE (CLEAN + FIXED)
# =========================================================

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

start = max(2, len(df) - 100)

for i in range(start, len(df)):
    prev = df.iloc[i-1]
    curr = df.iloc[i]

    vwap = curr.get("VWAP_RTH", np.nan)

    score_long = 0
    score_short = 0

    # -----------------------
    # LONG SCORE
    # -----------------------

    if prev["sweep_low"]:
        score_long += weights["sweep"]

    if curr["Close"] > vwap:
        score_long += weights["vwap"]

    if curr["vol_spike"]:
        score_long += weights["volume"]

    if bias_5m == "bull":
        score_long += weights["trend"]

    if bias_15m == "bull":
        score_long += weights["mtf"]

    if curr["delta"] > 0:
        score_long += weights["delta"]

    if prev["sweep_low"] and curr["Close"] > vwap:
        score_long += weights["confirmation"]

    if curr["SellNewsLong"]:
        score_long += weights["sellnews"]

    # -----------------------
    # SHORT SCORE
    # -----------------------

    if prev["sweep_high"]:
        score_short += weights["sweep"]

    if curr["Close"] < vwap:
        score_short += weights["vwap"]

    if curr["vol_spike"]:
        score_short += weights["volume"]

    if bias_5m == "bear":
        score_short += weights["trend"]

    if bias_15m == "bear":
        score_short += weights["mtf"]

    if curr["delta"] < 0:
        score_short += weights["delta"]

    if prev["sweep_high"] and curr["Close"] < vwap:
        score_short += weights["confirmation"]

    if curr["SellNewsShort"]:
        score_short += weights["sellnews"]

    # -----------------------
    # MARKET REGIME
    # -----------------------

    regime = df["MarketRegime"].iloc[i]

    if regime == "RANGE":
        if curr["VWAP_Extreme_High"]:
            score_short += 2
        if curr["VWAP_Extreme_Low"]:
            score_long += 2

    if regime == "TREND":
        if curr["Close"] > vwap:
            score_long += 1
        if curr["Close"] < vwap:
            score_short += 1

    # -----------------------
    # EXTRA BOOSTS
    # -----------------------

    if curr["ATR_pct"] > 0.005:
        if curr["Close"] > vwap:
            score_long += 1
        else:
            score_short += 1

    if curr["KC_Above"]:
        score_long += 1

    if curr["KC_Below"]:
        score_short += 1

    if curr["Trend_Strong"] and curr["Trend_Long"]:
        score_long += 2

    if curr["Trend_Strong"] and curr["Trend_Short"]:
        score_short += 2

    if curr["VWAP_Reclaim_Long"]:
        score_long += 2

    if curr["VWAP_Reclaim_Short"]:
        score_short += 2

    if curr["HH"]:
        score_long += 1

    if curr["LL"]:
        score_short += 1

    # LIMIT
    score_long = min(score_long, 10)
    score_short = min(score_short, 10)

    df.at[df.index[i], "LongScore"] = score_long
    df.at[df.index[i], "ShortScore"] = score_short


# =========================================================
# 🔹 16. SIGNAL GENERATION (STABLE)
# =========================================================

MIN_SCORE = 5
DELTA_THRESHOLD = 2

df["ScoreDelta"] = df["LongScore"] - df["ShortScore"]

df["LongSignal"] = False
df["ShortSignal"] = False

start = max(2, len(df) - 100)

for i in range(start, len(df)):

    long_score = df["LongScore"].iloc[i]
    short_score = df["ShortScore"].iloc[i]
    delta = long_score - short_score

    if i > start:
        if df["LongSignal"].iloc[i-1] and long_score >= short_score:
            df.at[df.index[i], "LongSignal"] = True
            continue

        if df["ShortSignal"].iloc[i-1] and short_score >= long_score:
            df.at[df.index[i], "ShortSignal"] = True
            continue

    if max(long_score, short_score) < MIN_SCORE:
        continue

    if long_score > short_score and delta >= DELTA_THRESHOLD:
        df.at[df.index[i], "LongSignal"] = True

    elif short_score > long_score and delta <= -DELTA_THRESHOLD:
        df.at[df.index[i], "ShortSignal"] = True
 
 
df["Date"] = df.index.date
df["Daily_High"] = df.groupby("Date")["High"].transform("max")
df["Daily_Low"] = df.groupby("Date")["Low"].transform("min")      
        
def get_dynamic_rr(df, i):
    long_score = df["LongScore"].iloc[i]
    short_score = df["ShortScore"].iloc[i]
    score = max(long_score, short_score)

    regime = df["MarketRegime"].iloc[i] if "MarketRegime" in df.columns else "TREND"
    trend_strong = bool(df["Trend_Strong"].iloc[i]) if "Trend_Strong" in df.columns else False
    trend_long = bool(df["Trend_Long"].iloc[i]) if "Trend_Long" in df.columns else False
    trend_short = bool(df["Trend_Short"].iloc[i]) if "Trend_Short" in df.columns else False
    atr_pct = df["ATR_pct"].iloc[i] if "ATR_pct" in df.columns else np.nan

    # Basis-RR nach Setup-Qualität
    if score >= 9:
        rr = 2.3
    elif score >= 7:
        rr = 1.8
    else:
        rr = 1.4

    # Trend-Bonus nur in Signalrichtung
    if df["LongSignal"].iloc[i] and trend_strong and trend_long:
        rr += 0.2

    if df["ShortSignal"].iloc[i] and trend_strong and trend_short:
        rr += 0.2

    # Volatilität leicht berücksichtigen
    if pd.notna(atr_pct):
        if atr_pct > 0.01:
            rr += 0.2
        elif atr_pct < 0.003:
            rr -= 0.2

    # In Range lieber konservativer
    if regime == "RANGE":
        rr -= 0.3

    # Harte Grenzen
    rr = max(1.2, min(rr, 2.5))
    return rr


def get_min_rr_for_entry(df, i):
    score = max(df["LongScore"].iloc[i], df["ShortScore"].iloc[i])
    regime = df["MarketRegime"].iloc[i] if "MarketRegime" in df.columns else "TREND"

    if regime == "RANGE":
        return 1.2
    elif score >= 9:
        return 1.8
    elif score >= 7:
        return 1.5
    else:
        return 1.3


def safe_prev_stop(prev_sl, new_sl, is_long=True):
    if np.isnan(prev_sl):
        return new_sl
    return max(prev_sl, new_sl) if is_long else min(prev_sl, new_sl)
        
def calculate_sl_tp(df, i):
    price = df["Close"].iloc[i]
    atr = df["ATR"].iloc[i]
    vwap = df["VWAP_RTH"].iloc[i]

    if np.isnan(price) or np.isnan(atr) or np.isnan(vwap):
        return np.nan, np.nan

    rr_target = get_dynamic_rr(df, i)

    min_risk = atr * 0.5
    max_risk = atr * 3

    # ---------- LONG ----------
    if df["LongSignal"].iloc[i]:
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
        return sl, tp

    # ---------- SHORT ----------
    elif df["ShortSignal"].iloc[i]:
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
        return sl, tp

    return np.nan, np.nan


# -----------------------
# APPLY SL/TP + TRAILING
# -----------------------

df["SL"] = np.nan
df["TP"] = np.nan

for i in range(1, len(df)):

    price = df["Close"].iloc[i]
    atr = df["ATR"].iloc[i]

    new_long = df["LongSignal"].iloc[i] and not df["LongSignal"].iloc[i-1]
    new_short = df["ShortSignal"].iloc[i] and not df["ShortSignal"].iloc[i-1]

    # 🟢 Neue Trades: SL/TP frisch setzen
    if new_long or new_short:
        sl, tp = calculate_sl_tp(df, i)
        if not np.isnan(sl) and not np.isnan(tp):
            df.at[df.index[i], "SL"] = sl
            df.at[df.index[i], "TP"] = tp
        continue   # <- WICHTIG: verhindert Überschreiben durch altes Trailing

    # 🔁 Trailing Stop LONG
    if df["LongSignal"].iloc[i-1]:
        prev_sl = df["SL"].iloc[i-1]
        prev_tp = df["TP"].iloc[i-1]

        new_sl = price - atr * 1.2
        df.at[df.index[i], "SL"] = safe_prev_stop(prev_sl, new_sl, is_long=True)
        df.at[df.index[i], "TP"] = prev_tp

    # 🔁 Trailing Stop SHORT
    elif df["ShortSignal"].iloc[i-1]:
        prev_sl = df["SL"].iloc[i-1]
        prev_tp = df["TP"].iloc[i-1]

        new_sl = price + atr * 1.2
        df.at[df.index[i], "SL"] = safe_prev_stop(prev_sl, new_sl, is_long=False)
        df.at[df.index[i], "TP"] = prev_tp

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

def get_min_rr_for_entry(df, i):
    score = max(df["LongScore"].iloc[i], df["ShortScore"].iloc[i])
    regime = df["MarketRegime"].iloc[i]

    if regime == "RANGE":
        return 1.2
    if score >= 9:
        return 1.8
    if score >= 7:
        return 1.5
    return 1.3

MIN_SCORE = 5
DELTA_THRESHOLD = 2
VOLUME_FACTOR = 1.2

def get_entry_signal(df, i, bias):
    price = df["Close"].iloc[i]
    vwap = df["VWAP_RTH"].iloc[i]

    long_score = df["LongScore"].iloc[i]
    short_score = df["ShortScore"].iloc[i]
    delta = long_score - short_score

    volume = df["Volume"].iloc[i]
    avg_volume = df["Volume"].rolling(20).mean().iloc[i]

    orb_high = df["ORB_High"].iloc[i]
    orb_low = df["ORB_Low"].iloc[i]

    sl = df["SL"].iloc[i]
    tp = df["TP"].iloc[i]

    if np.isnan(sl) or np.isnan(tp):
        return None

    rr = abs((tp - price) / (price - sl)) if price != sl else 0
    min_rr = get_min_rr_for_entry(df, i)

    # 🚀 LONG
    if (
        bias == "LONG" and
        long_score >= MIN_SCORE and
        delta >= DELTA_THRESHOLD and
        price > vwap and
        price > orb_high and
        volume > avg_volume * VOLUME_FACTOR and
        rr >= min_rr
    ):
        return {
            "type": "LONG",
            "price": price,
            "sl": sl,
            "tp": tp,
            "rr": rr,
            "delta": delta
        }

    # 🔻 SHORT
    if (
        bias == "SHORT" and
        short_score >= MIN_SCORE and
        delta <= -DELTA_THRESHOLD and
        price < vwap and
        price < orb_low and
        volume > avg_volume * VOLUME_FACTOR and
        rr >= min_rr
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

i = len(df) - 1

smart_type, long_score, short_score, delta = get_smart_signal(df, i)
signal = get_entry_signal(df, i, smart_type)    

def last_valid(series):
    try:
        return float(series.dropna().iloc[-1])
    except:
        return 0.0

# 🔴 FAST PRICE (1m inkl. Pre/Post)
df_fast = load_fast_price(symbol)

if df_fast.empty or "Close" not in df_fast.columns:
    st.warning("Keine Live-Daten verfügbar")
    st.stop()

current_price = last_valid(df_fast["Close"])

# 🔥 RTH aus FAST DATA holen (korrekt!)
df_fast_rth = df_fast.copy()

# Zeitzone sicherstellen
if df_fast_rth.index.tz is None:
    df_fast_rth.index = df_fast_rth.index.tz_localize("UTC")

df_fast_rth.index = df_fast_rth.index.tz_convert("America/New_York")

times = df_fast_rth.index.time

rth_mask = (times >= time(9,30)) & (times < time(16,0))
df_fast_rth = df_fast_rth[rth_mask]

if not df_fast_rth.empty:
    last_rth_price = df_fast_rth["Close"].iloc[-1]
else:
    last_rth_price = current_price
    
delta_price = current_price - last_rth_price
delta_percent = (delta_price / last_rth_price) * 100 if last_rth_price != 0 else 0

# =========================================================
# 🔹 PAPER TRADING RUNTIME
# =========================================================

paper_opened = None
paper_closed = None
paper_auto_enabled = (paper_get_setting("auto_mode", "0") == "1")
paper_notify = (paper_get_setting("paper_telegram", "0") == "1")
now_str = datetime.now(pytz.timezone("America/New_York")).strftime("%Y-%m-%d %H:%M:%S %Z")

paper_opened, paper_closed = paper_process_symbol(
    symbol=symbol,
    signal=signal,
    current_price=float(current_price),
    now_str=now_str,
    auto_enabled=paper_auto_enabled,
    notify=paper_notify
)

# 🌍 EU Preis
eu_price, eu_symbol = load_global_prices(symbol)
eu_display = f" | EU: ${eu_price:.2f}" if not np.isnan(eu_price) else ""

# VWAP Live
vwap_last = (df_fast["Close"] * df_fast["Volume"]).cumsum() / df_fast["Volume"].cumsum()
vwap_last = vwap_last.iloc[-1] 

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

col1.metric(
    label="Current Price",
    value=f"${current_price:.2f}",
    delta=f"{delta_price:+.2f} ({delta_percent:+.2f}%)"
)

col2.metric(
    label=f"{display_name} (RTH){eu_display}",    
    value=f"${last_rth_price:.2f}"
)

col3.metric("VWAP", f"${vwap_last:.2f}")       

col5, col6, col7 = st.columns(3)

col5.metric("MACD", round(df["MACD"].iloc[-1], 2))
col6.metric("Daily High", round(df["Daily_High"].iloc[-1], 2))
col7.metric("Daily Low", round(df["Daily_Low"].iloc[-1], 2))

col8, col9, col10 = st.columns(3)

col8.metric("RSI", f"{df['RSI'].iloc[-1]:.2f}")
col9.metric("Volume", float(df["Volume"].iloc[-1]))
col10.metric("Vol Avg", round(df["Vol_Avg"].iloc[-1], 2))

col11, col12, col13 = st.columns(3)

col11.metric("EMA9", round(df["EMA9"].iloc[-1], 2))
col12.metric("EMA20", round(df["EMA20"].iloc[-1], 2))
col13.metric("EMA50", round(df["EMA50"].iloc[-1], 2))

col14, col15, col16 = st.columns(3)

col14.metric("EMA200", round(df["EMA200"].iloc[-1], 2))
col15.metric("ATR", round(df["ATR"].iloc[-1], 2))
col16.metric("ADX", round(df["ADX"].iloc[-1], 2))

# =========================================================
# 🔹 PAPER TRADING DASHBOARD
# =========================================================

paper_snapshot = paper_account_snapshot({symbol: float(current_price)})
open_pos = paper_get_open_position(symbol)

pc1, pc2, pc3, pc4 = st.columns(4)
pc1.metric("Paper Cash", f"${paper_snapshot['cash']:.2f}")
pc2.metric("Paper Equity", f"${paper_snapshot['equity']:.2f}", delta=f"{paper_snapshot['total_pnl']:+.2f}")
pc3.metric("Open PnL", f"${paper_snapshot['unrealized']:.2f}")
pc4.metric("Gesamt PnL %", f"{paper_snapshot['total_pnl_pct']:+.2f}%")

if paper_opened:
    st.success(
        f"🧪 PAPER {paper_opened['direction']} eröffnet | {paper_opened['ticker']} | "
        f"Entry {paper_opened['entry_price']:.2f} | SL {paper_opened['sl']:.2f} | TP {paper_opened['tp']:.2f}"
    )

if paper_closed:
    pnl_color = "✅" if paper_closed["pnl"] >= 0 else "❌"
    st.info(
        f"{pnl_color} PAPER {paper_closed['direction']} geschlossen | {paper_closed['ticker']} | "
        f"Exit {paper_closed['exit_price']:.2f} | PnL {paper_closed['pnl']:+.2f} ({paper_closed['pnl_pct']:+.2f}%) | {paper_closed['exit_reason']}"
    )

if open_pos:
    st.caption(
        f"Offene Position {open_pos['direction']} | Qty {float(open_pos['qty']):.4f} | "
        f"Entry {float(open_pos['entry_price']):.2f} | SL {float(open_pos['sl']):.2f} | TP {float(open_pos['tp']):.2f}"
    )

# Trend Score
score = 0
if df["Close"].iloc[-1] > df["EMA200"].iloc[-1]:
    score += 1
if df["EMA50"].iloc[-1] > df["EMA200"].iloc[-1]:
    score += 1
if df["ADX"].iloc[-1] > 25:
    score += 1

trend = "Bullish" if score >= 2 else "Bearish"

st.markdown(
    f"<span style='color:{'green' if trend=='Bullish' else 'red'}; font-size:24px'>Trend: {trend} ({score}/3)</span>",
    unsafe_allow_html=True
)

fig = make_subplots(
    rows=5, cols=1,
    shared_xaxes=True,
    row_heights=[0.55,0.15,0.12,0.08,0.1]
)

# Candles
fig.add_trace(go.Candlestick(
    x=df.index,
    open=df["Open"],
    high=df["High"],
    low=df["Low"],
    close=df["Close"]
), row=1, col=1)

# VWAP
fig.add_trace(go.Scatter(x=df.index, y=df["VWAP_RTH"], name="VWAP"), row=1, col=1)

# EMA
for ema in ["EMA20","EMA50","EMA200"]:
    fig.add_trace(go.Scatter(x=df.index, y=df[ema], name=ema), row=1, col=1)

# Signals
fig.add_trace(go.Scatter(
    x=df[df["LongSignal"]].index,
    y=df[df["LongSignal"]]["Close"],
    mode="markers",
    marker=dict(color="green", size=10),
    name="LONG"
), row=1, col=1)

fig.add_trace(go.Scatter(
    x=df[df["ShortSignal"]].index,
    y=df[df["ShortSignal"]]["Close"],
    mode="markers",
    marker=dict(color="red", size=10),
    name="SHORT"
), row=1, col=1)

if df["SL"].notna().any():
    fig.add_hline(y=df["SL"].dropna().iloc[-1], line_dash="dot", line_color="red")

if df["TP"].notna().any():
    fig.add_hline(y=df["TP"].dropna().iloc[-1], line_dash="dot", line_color="green")   
    
# =========================================================
# 🔹 SUPPORT / RESISTANCE
# =========================================================

def detect_levels_pro(df, window=10, tolerance=0.002):
    supports = []
    resistances = []

    lows = df["Low"].to_numpy()
    highs = df["High"].to_numpy()

    for i in range(window, len(df) - window):
        if lows[i] == min(lows[i-window:i+window]):
            supports.append((lows[i], i))
        if highs[i] == max(highs[i-window:i+window]):
            resistances.append((highs[i], i))

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

    def score_level(level):
        age = current_idx - level["last_touch"]
        recency = np.exp(-age / 50)
        strength = level["touches"]
        return strength * recency

    for l in sup_cluster:
        l["score"] = score_level(l)

    for l in res_cluster:
        l["score"] = score_level(l)

    flipped_supports = []
    flipped_resistances = []

    for r in res_cluster:
        if current_price > r["price"]:
            flipped_supports.append(r)

    for s in sup_cluster:
        if current_price < s["price"]:
            flipped_resistances.append(s)

    final_supports = sup_cluster + flipped_supports
    final_resistances = res_cluster + flipped_resistances

    def sort_levels(levels):
        return sorted(
            levels,
            key=lambda x: (-x["score"], abs(x["price"] - current_price))
        )

    final_supports = sort_levels(final_supports)[:5]
    final_resistances = sort_levels(final_resistances)[:5]

    return final_supports, final_resistances


sup_levels, res_levels = detect_levels_pro(df)
supports = [l["price"] for l in sup_levels]
resistances = [l["price"] for l in res_levels]


# =========================================================
# 🔹 SUBPLOTS
# =========================================================

rows_map = {
    "Price": 1,
    "Volume": 2,
    "Score": 3,
    "RSI": 4,
    "MACD": 5
}

rows = 5
titles = ["Price", "Volume", "Score", "RSI", "MACD"]
row_heights = [0.55, 0.15, 0.12, 0.08, 0.10]

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


# =========================================================
# 🔹 PRICE CHART – SESSIONS
# =========================================================

for sess, col in [
    ("RTH", df[df["Session"] == "RTH"]),
    ("PRE", df[df["Session"] == "PREMARKET"]),
    ("AH",  df[df["Session"] == "AFTERHOURS"])
]:
    if col.empty:
        continue

    fig.add_trace(
        go.Candlestick(
            x=col.index,
            open=col["Open"],
            high=col["High"],
            low=col["Low"],
            close=col["Close"],
            name=sess,
            increasing_line_color="green" if sess == "RTH" else "lightgreen",
            decreasing_line_color="red" if sess == "RTH" else "lightcoral",
            opacity=1 if sess == "RTH" else 0.5
        ),
        row=price_row, col=1
    )


# =========================================================
# 🔹 VWAP PER SESSION
# =========================================================

session_vwap_map = [
    ("RTH", "VWAP_RTH", "yellow"),
    ("PREMARKET", "VWAP_PRE", "orange"),
    ("AFTERHOURS", "VWAP_AH", "purple"),
]

for session_name, vwap_col, color in session_vwap_map:
    col = df[df["Session"] == session_name]
    if not col.empty and vwap_col in col.columns:
        fig.add_trace(
            go.Scatter(
                x=col.index,
                y=col[vwap_col],
                line=dict(color=color, width=3),
                name=vwap_col
            ),
            row=price_row, col=1
        )


# =========================================================
# 🔹 EMA LINES
# =========================================================

for ema in ["EMA20", "EMA50", "EMA200"]:
    if ema in df.columns:
        fig.add_trace(
            go.Scatter(
                x=df.index,
                y=df[ema],
                name=ema
            ),
            row=price_row, col=1
        )


# =========================================================
# 🔹 BOLLINGER BANDS
# =========================================================

for bb in ["BB_UPPER", "BB_MID", "BB_LOWER"]:
    if bb in df.columns:
        dash = "dot" if bb != "BB_MID" else None
        fig.add_trace(
            go.Scatter(
                x=df.index,
                y=df[bb],
                name=bb,
                line=dict(width=1, dash=dash)
            ),
            row=price_row, col=1
        )


# =========================================================
# 🔹 VWAP BANDS + KELTNER
# =========================================================

for kc in ["VWAP_upper2", "VWAP_lower2", "KC_UPPER", "KC_MID", "KC_LOWER"]:
    if kc in df.columns:
        line_kwargs = dict(line=dict(width=1))
        if kc == "KC_MID":
            line_kwargs["line"]["color"] = "blue"

        fig.add_trace(
            go.Scatter(
                x=df.index,
                y=df[kc],
                name=kc,
                **line_kwargs
            ),
            row=price_row, col=1
        )


# =========================================================
# 🔹 LONG / SHORT SIGNALS
# =========================================================

if "LongSignal" in df.columns:
    long_df = df[df["LongSignal"]]
    if not long_df.empty:
        fig.add_trace(
            go.Scatter(
                x=long_df.index,
                y=long_df["Close"],
                mode="markers+text",
                marker=dict(symbol="triangle-up", size=12, color="lime"),
                textposition="top center",
                hovertemplate="Price: %{y}<extra></extra>",
                name="LONG"
            ),
            row=price_row, col=1
        )

if "ShortSignal" in df.columns:
    short_df = df[df["ShortSignal"]]
    if not short_df.empty:
        fig.add_trace(
            go.Scatter(
                x=short_df.index,
                y=short_df["Close"],
                mode="markers+text",
                marker=dict(symbol="triangle-down", size=12, color="red"),
                textposition="bottom center",
                hovertemplate="Price: %{y}<extra></extra>",
                name="SHORT"
            ),
            row=price_row, col=1
        )


# =========================================================
# 🔹 SUPPORT / RESISTANCE LINES
# =========================================================

for s in supports:
    fig.add_hline(
        y=s,
        line_dash="dash",
        line_color="green",
        row=price_row, col=1,
        annotation_text=f"S {s:.2f}",
        annotation_position="bottom left"
    )

for r in resistances:
    fig.add_hline(
        y=r,
        line_dash="dash",
        line_color="red",
        row=price_row, col=1,
        annotation_text=f"R {r:.2f}",
        annotation_position="top left"
    )


# =========================================================
# 🔹 SL / TP LINES
# =========================================================

last_sl = df["SL"].dropna().iloc[-1] if "SL" in df.columns and df["SL"].notna().any() else None
last_tp = df["TP"].dropna().iloc[-1] if "TP" in df.columns and df["TP"].notna().any() else None

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


# =========================================================
# 🔹 VOLUME PANEL
# =========================================================

if "Volume" in df.columns:
    fig.add_trace(
        go.Bar(
            x=df.index,
            y=df["Volume"],
            name="Volume"
        ),
        row=volume_row, col=1
    )

    last_vol = df["Volume"].iloc[-1]
    fig.add_annotation(
        x=df.index[-1],
        y=last_vol,
        text=f"Vol {last_vol:.0f}",
        showarrow=False,
        xanchor="left",
        row=volume_row, col=1
    )


# =========================================================
# 🔹 SCORE PANEL
# =========================================================

if "LongScore" in df.columns and "ShortScore" in df.columns:
    fig.add_trace(
        go.Scatter(
            x=df.index,
            y=df["LongScore"],
            name="Long Score",
            line=dict(width=1, dash="dot")
        ),
        row=score_row, col=1
    )

    fig.add_trace(
        go.Scatter(
            x=df.index,
            y=df["ShortScore"],
            name="Short Score",
            line=dict(width=1, dash="dot")
        ),
        row=score_row, col=1
    )

    fig.add_hline(y=5, line_dash="dash", row=score_row, col=1)
    fig.update_yaxes(range=[0, 8], row=score_row, col=1)

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


# =========================================================
# 🔹 RSI PANEL
# =========================================================

if "RSI" in df.columns:
    fig.add_trace(
        go.Scatter(
            x=df.index,
            y=df["RSI"],
            name="RSI"
        ),
        row=rsi_row, col=1
    )

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


# =========================================================
# 🔹 MACD PANEL
# =========================================================

if all(col in df.columns for col in ["MACD", "MACD_signal", "MACD_hist"]):
    fig.add_trace(
        go.Scatter(
            x=df.index,
            y=df["MACD"],
            name="MACD"
        ),
        row=macd_row, col=1
    )

    fig.add_trace(
        go.Scatter(
            x=df.index,
            y=df["MACD_signal"],
            name="Signal"
        ),
        row=macd_row, col=1
    )

    fig.add_trace(
        go.Bar(
            x=df.index,
            y=df["MACD_hist"],
            name="Histogram"
        ),
        row=macd_row, col=1
    )

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


# =========================================================
# 🔹 LAYOUT
# =========================================================

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

for i in range(1, rows + 1):
    if i == price_row:
        fig.update_xaxes(
            showticklabels=True,
            showspikes=True,
            rangebreaks=[dict(bounds=["sat", "mon"])],
            spikemode="across",
            row=i, col=1
        )
        fig.update_yaxes(showspikes=True)
    else:
        fig.update_xaxes(
            showticklabels=False,
            showspikes=True,
            rangebreaks=[dict(bounds=["sat", "mon"])],
            spikemode="across",
            row=i, col=1
        )
        fig.update_yaxes(showspikes=True)
        
# =========================================================
# 🔹 DARK MODE / CSS
# =========================================================

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
    background-color: #1f2937 !important;
    color: #f3f4f6 !important;
}

section[data-testid="stSidebar"] div[data-baseweb="select"] span {
    color: #f3f4f6 !important;
}

</style>
""", unsafe_allow_html=True)


# =========================================================
# 🔹 PLOT OUTPUT
# =========================================================

st.plotly_chart(
    fig,
    use_container_width=True,
    config={
        "scrollZoom": True,
        "displaylogo": False,
        "modeBarButtonsToRemove": [
            "zoom2d", "lasso2d", "select2d"
        ]
    }
)


# =========================================================
# 🔹 FINAL ALERT / SIGNAL OUTPUT
# =========================================================

last_long = df["LongSignal"].iloc[-1] if "LongSignal" in df.columns else False
last_short = df["ShortSignal"].iloc[-1] if "ShortSignal" in df.columns else False

MIN_SCORE = 5
DELTA_THRESHOLD = 2
VOLUME_FACTOR = 1.2

last_idx = len(df) - 1

long_score = df["LongScore"].iloc[last_idx]
short_score = df["ShortScore"].iloc[last_idx]
delta = long_score - short_score

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

if signal_idx is None:
    signal_idx = len(df) - 1
    signal_type = "NEUTRAL"


# =========================================================
# 🔹 SCORE METRICS
# =========================================================

col20, col21, col22 = st.columns(3)

col20.metric(
    "Signal Strength",
    f"{confidence:.2f}"
)

col21.metric(
    "Long Score",
    f"{df['LongScore'].iloc[signal_idx]:.2f}",
    delta=f"{df['LongScore'].iloc[signal_idx] - df['ShortScore'].iloc[signal_idx]:.2f}"
)

col22.metric(
    "Short Score",
    f"{df['ShortScore'].iloc[signal_idx]:.2f}",
    delta=f"{df['ShortScore'].iloc[signal_idx] - df['LongScore'].iloc[signal_idx]:.2f}"
)


# =========================================================
# 🔹 SMART SIGNAL OUTPUT
# =========================================================

if smart_type == "LONG":
    st.success(
        f"🚀 SMART LONG | Score: {long_score:.2f} | Δ {delta:.2f}"
    )
elif smart_type == "SHORT":
    st.error(
        f"🔻 SMART SHORT | Score: {short_score:.2f} | Δ {delta:.2f}"
    )
else:
    st.info("⚖️ NO CLEAR SIGNAL")


# =========================================================
# 🔹 A+ SIGNAL OUTPUT
# =========================================================

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

# =========================================================
# 🔹 MANUAL PAPER ORDER BLOCK (IMMER SICHTBAR)
# =========================================================

st.markdown("### 🎛️ Manueller Paper-Orderblock")

manual_pos = paper_get_open_position(symbol)
default_manual_type = signal["type"] if signal else "LONG"
default_manual_sl = float(signal["sl"]) if signal else (float(df["SL"].dropna().iloc[-1]) if df["SL"].notna().any() else float(current_price * 0.98))
default_manual_tp = float(signal["tp"]) if signal else (float(df["TP"].dropna().iloc[-1]) if df["TP"].notna().any() else float(current_price * 1.02))
default_qty = round(max((paper_get_cash() * float(paper_get_setting("risk_fraction", 0.25) or 0.25)) / float(current_price), 0), 6)

mcol1, mcol2, mcol3, mcol4 = st.columns(4)
manual_direction = mcol1.selectbox(
    "Richtung",
    ["LONG", "SHORT"],
    index=0 if default_manual_type == "LONG" else 1,
    key=f"manual_direction_{symbol}"
)
manual_qty = mcol2.number_input(
    "Qty",
    min_value=0.0,
    value=float(default_qty),
    step=max(float(default_qty) / 10, 0.001) if default_qty > 0 else 1.0,
    format="%.6f",
    key=f"manual_qty_{symbol}"
)
manual_sl = mcol3.number_input(
    "SL",
    value=float(default_manual_sl),
    step=max(abs(float(current_price)) * 0.001, 0.01),
    format="%.4f",
    key=f"manual_sl_{symbol}"
)
manual_tp = mcol4.number_input(
    "TP",
    value=float(default_manual_tp),
    step=max(abs(float(current_price)) * 0.001, 0.01),
    format="%.4f",
    key=f"manual_tp_{symbol}"
)

bcol1, bcol2, bcol3 = st.columns(3)

if signal:
    if bcol1.button("🟢 A+ Signal handeln", key=f"paper_a_plus_trade_{symbol}"):
        opened_info, error_msg = paper_manual_open(
            symbol=symbol,
            direction=signal["type"],
            current_price=float(current_price),
            sl=float(signal["sl"]),
            tp=float(signal["tp"]),
            now_str=now_str,
            qty=float(default_qty),
            notify=paper_notify
        )
        if error_msg:
            st.warning(error_msg)
        elif opened_info:
            st.success(f"Paper {opened_info['direction']} eröffnet: {opened_info['ticker']} | Qty {opened_info['qty']:.4f}")
            st.rerun()
else:
    bcol1.button("🟢 A+ Signal handeln", key=f"paper_a_plus_trade_{symbol}_disabled", disabled=True)

if bcol2.button("🟡 Manuellen Trade ausführen", key=f"paper_manual_trade_{symbol}"):
    opened_info, error_msg = paper_manual_open(
        symbol=symbol,
        direction=manual_direction,
        current_price=float(current_price),
        sl=float(manual_sl),
        tp=float(manual_tp),
        now_str=now_str,
        qty=float(manual_qty),
        notify=paper_notify
    )
    if error_msg:
        st.warning(error_msg)
    elif opened_info:
        st.success(f"Manueller Paper {opened_info['direction']} Trade eröffnet: {opened_info['ticker']} | Qty {opened_info['qty']:.4f}")
        st.rerun()

close_disabled = manual_pos is None
if bcol3.button("🔴 Offene Position schließen", key=f"paper_manual_close_{symbol}", disabled=close_disabled):
    closed = paper_close_position(symbol, float(current_price), now_str, "MANUAL")
    if closed:
        if paper_notify:
            send_telegram(
                f"🧪 PAPER MANUAL CLOSE {symbol}\n"
                f"Exit: {float(current_price):.2f}\n"
                f"PnL: {closed['pnl']:.2f} ({closed['pnl_pct']:.2f}%)"
            )
        st.success(f"Position geschlossen: {symbol} | PnL {closed['pnl']:.2f}")
        st.rerun()

if manual_pos:
    st.caption(
        f"Offene Position in {symbol}: {manual_pos['direction']} | Qty {float(manual_pos['qty']):.4f} | Entry {float(manual_pos['entry_price']):.2f}"
    )
else:
    st.caption(f"Keine offene Position in {symbol}")

# =========================================================
# 🔹 PAPER POSITIONS / TRADES OUTPUT
# =========================================================

paper_positions_df = paper_get_open_positions()
paper_trades_df = paper_get_trade_history(20)

if not paper_positions_df.empty:
    st.markdown("### Offene Paper-Positionen")
    st.dataframe(paper_positions_df, use_container_width=True)

if not paper_trades_df.empty:
    st.markdown("### Letzte Paper-Trades")
    st.dataframe(paper_trades_df, use_container_width=True)
