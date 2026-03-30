# -----------------------
# IMPORTS & SETTINGS
# -----------------------
import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pytz
from datetime import datetime, time
from streamlit_autorefresh import st_autorefresh

st.set_page_config(layout="wide")
st_autorefresh(interval=30_000, key="datarefresh")

# -----------------------
# MARKET SESSION
# -----------------------
def get_market_session():
    et = pytz.timezone("US/Eastern")
    now = datetime.now(et)
    weekday = now.weekday()
    t = now.time()

    if weekday >= 5:
        return "WEEKEND"
    if time(4,0) <= t < time(9,30):
        return "PREMARKET"
    elif time(9,30) <= t < time(16,0):
        return "RTH"
    elif time(16,0) <= t < time(20,0):
        return "AFTERHOURS"
    return "CLOSED"

SESSION = get_market_session()

# -----------------------
# YFINANCE SAFE DOWNLOAD
# -----------------------
def yf_safe_download(symbols, period="1d", interval="1m", prepost=False):
    if isinstance(symbols, str):
        symbols = [symbols]
    try:
        df = yf.download(symbols, period=period, interval=interval, progress=False, prepost=prepost, threads=False)
        if df.empty:
            return {s: pd.DataFrame() for s in symbols}
        if isinstance(df.columns, pd.MultiIndex):
            result = {}
            for ticker in symbols:
                result[ticker] = df[ticker].dropna() if ticker in df else pd.DataFrame()
            return result
        return {symbols[0]: df.dropna()}
    except:
        return {s: pd.DataFrame() for s in symbols}

# -----------------------
# DATA LOADER & INDICATORS
# -----------------------
@st.cache_data(ttl=300)
def load_data(symbol, period="1d", interval="1m"):
    data_dict = yf_safe_download(symbol, period=period, interval=interval)
    df = data_dict.get(symbol, pd.DataFrame())
    if df.empty:
        return df
    df.reset_index(inplace=True)
    df['Datetime'] = pd.to_datetime(df['Datetime'])
    df.set_index('Datetime', inplace=True)
    df = mark_premarket(df)
    df = add_indicators(df)
    return df

def mark_premarket(df):
    et = pytz.timezone("US/Eastern")
    session = []
    for idx in df.index:
        t = idx.tz_convert(et).time()
        if time(4,0) <= t < time(9,30):
            session.append("PREMARKET")
        elif time(9,30) <= t < time(16,0):
            session.append("RTH")
        elif time(16,0) <= t < time(20,0):
            session.append("AFTERHOURS")
        else:
            session.append("CLOSED")
    df["Session"] = session
    return df

def add_indicators(df):
    delta = df["Close"].diff()
    up = delta.clip(lower=0)
    down = -1*delta.clip(upper=0)
    ma_up = up.rolling(14).mean()
    ma_down = down.rolling(14).mean()
    rs = ma_up / ma_down
    df["RSI"] = 100 - (100/(1+rs))

    ema12 = df["Close"].ewm(span=12, adjust=False).mean()
    ema26 = df["Close"].ewm(span=26, adjust=False).mean()
    df["MACD"] = ema12 - ema26
    df["MACD_Signal"] = df["MACD"].ewm(span=9, adjust=False).mean()

    df["BB_Mid"] = df["Close"].rolling(20).mean()
    df["BB_Upper"] = df["BB_Mid"] + 2*df["Close"].rolling(20).std()
    df["BB_Lower"] = df["BB_Mid"] - 2*df["Close"].rolling(20).std()

    df["VWAP_RTH"] = (df["Close"]*df.get("Volume",1)).cumsum() / df.get("Volume",1).cumsum()
    return df

# -----------------------
# SIGNAL LOGIC
# -----------------------
def track_signals(df, symbol):
    last = df.iloc[-1]
    signal = None
    if last["Close"] > last["VWAP_RTH"] and last["RSI"] < 70:
        signal = "LONG"
    elif last["Close"] < last["VWAP_RTH"] and last["RSI"] > 30:
        signal = "SHORT"

    key = f"last_signal_{symbol}"
    prev_signal = st.session_state.get(key)
    if signal != prev_signal:
        st.session_state[key] = signal
        return signal
    return None

def calculate_score(df):
    last = df.iloc[-1]
    score = 0
    score += 1 if last["Close"] > last["VWAP_RTH"] else -1
    score += 1 if last["RSI"] < 30 else (-1 if last["RSI"] > 70 else 0)
    score += 1 if last["MACD"] > last["MACD_Signal"] else -1
    return score

# -----------------------
# CHARTS
# -----------------------
def plot_price_chart(df, symbol):
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(go.Candlestick(x=df.index, open=df["Open"], high=df["High"], low=df["Low"], close=df["Close"], name=f"{symbol} Price"))
    fig.add_trace(go.Scatter(x=df.index, y=df["VWAP_RTH"], mode="lines", line=dict(color="orange", width=1.5), name="VWAP RTH"))
    if "BB_Upper" in df.columns and "BB_Lower" in df.columns:
        fig.add_trace(go.Scatter(x=df.index, y=df["BB_Upper"], line=dict(color="lightblue", width=1), name="BB Upper"))
        fig.add_trace(go.Scatter(x=df.index, y=df["BB_Lower"], line=dict(color="lightblue", width=1), name="BB Lower", fill='tonexty', fillcolor='rgba(173,216,230,0.2)'))
    if "RSI" in df.columns:
        fig.add_trace(go.Scatter(x=df.index, y=df["RSI"], line=dict(color="purple", width=1), name="RSI", yaxis="y2"), secondary_y=True)
        fig.update_yaxes(title_text="RSI", secondary_y=True, range=[0,100])
    fig.update_layout(title=f"{symbol} Chart mit Indikatoren", xaxis_title="Zeit", yaxis_title="Preis", legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1), height=600)
    return fig

def plot_score_chart(df, symbol):
    df_scores = pd.DataFrame(index=df.index)
    df_scores["Score"] = df.apply(lambda row: calculate_score(df.loc[:row.name]), axis=1)
    fig = go.Figure()
    fig.add_trace(go.Bar(x=df_scores.index, y=df_scores["Score"], marker_color=np.where(df_scores["Score"]>0, "green","red"), name="Score"))
    fig.update_layout(title=f"{symbol} Score Chart", xaxis_title="Zeit", yaxis_title="Score", height=300)
    return fig

# -----------------------
# ORDERS / ALERTS
# -----------------------
def execute_order(symbol, signal, qty=1):
    timestamp = pd.Timestamp.now(tz="US/Eastern")
    st.session_state.setdefault("orders", [])
    order = {"symbol": symbol, "signal": signal, "qty": qty, "time": timestamp}
    st.session_state["orders"].append(order)
    st.success(f"Order executed: {signal} {qty} {symbol} at {timestamp.strftime('%H:%M:%S')}")

def check_alerts(df, symbol):
    signal = track_signals(df, symbol)
    if signal and st.session_state.get(f"last_alert_{symbol}", "") != signal:
        st.session_state[f"last_alert_{symbol}"] = signal
        st.toast(f"{symbol} Signal: {signal}", icon="⚡")

# -----------------------
# DASHBOARD UI
# -----------------------
st.title("Trading Dashboard Pro")

symbols_input = st.text_input("Symbols (comma separated)", value="AAPL,MSFT,TSLA")
symbols = [s.strip().upper() for s in symbols_input.split(",")]

period = st.selectbox("Period", ["1d","5d","1mo","3mo"], index=0)
interval = st.selectbox("Interval", ["1m","5m","15m","1h"], index=1)

all_data = {sym: load_data(sym, period, interval) for sym in symbols}

for sym, df in all_data.items():
    st.header(f"{sym} ({df['Session'].iloc[-1]})")
    price_fig = plot_price_chart(df, sym)
    st.plotly_chart(price_fig, use_container_width=True)
    score_fig = plot_score_chart(df, sym)
    st.plotly_chart(score_fig, use_container_width=True)
    check_alerts(df, sym)
    col1, col2 = st.columns(2)
    if col1.button(f"LONG {sym}"):
        execute_order(sym, "LONG")
    if col2.button(f"SHORT {sym}"):
        execute_order(sym, "SHORT")
    last_signal = st.session_state.get(f"last_signal_{sym}", None)
    st.write(f"Last Signal: {last_signal if last_signal else 'None'}")