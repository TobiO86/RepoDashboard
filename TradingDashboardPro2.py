import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from streamlit_autorefresh import st_autorefresh
import numpy as np
import requests

st.set_page_config(layout="wide")
st.title("Trading Dashboard PRO")

st_autorefresh(interval=30000, key="datarefresh")

# -----------------------
# SIDEBAR
# -----------------------

symbol = st.sidebar.text_input("Ticker", value="BTC-USD").upper()
period = st.sidebar.selectbox("Period", ["5d","1mo","3mo","6mo","1y"])
interval = st.sidebar.selectbox("Timeframe", ["15m","1h","4h","1d"])

show_volume = st.sidebar.checkbox("Volume", True)
show_rsi = st.sidebar.checkbox("RSI", True)
show_macd = st.sidebar.checkbox("MACD", True)

# -----------------------
# DATA
# -----------------------

@st.cache_data(ttl=30)
def load_data(symbol,period,interval):
    df = yf.download(symbol,period=period,interval=interval, progress=False)
    if isinstance(df.columns,pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df.dropna()

df = load_data(symbol,period,interval)
if len(df) == 0:
    st.stop()

df = df.tail(500)

# -----------------------
# MTF
# -----------------------

@st.cache_data(ttl=30)
def load_mtf(symbol):
    df_5m = yf.download(symbol, period="2d", interval="5m", progress=False)
    df_15m = yf.download(symbol, period="5d", interval="15m", progress=False)
    return df_5m.dropna(), df_15m.dropna()

df_5m, df_15m = load_mtf(symbol)

def mtf_bias(df):
    df["EMA20"] = df["Close"].ewm(span=20).mean()
    df["EMA50"] = df["Close"].ewm(span=50).mean()
    return "bull" if df["EMA20"].iloc[-1] > df["EMA50"].iloc[-1] else "bear"

bias_5m = mtf_bias(df_5m)
bias_15m = mtf_bias(df_15m)

# -----------------------
# INDICATORS
# -----------------------

df["EMA20"] = df["Close"].ewm(span=20).mean()
df["EMA50"] = df["Close"].ewm(span=50).mean()

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

df["Date"] = df.index.date
df["VWAP"] = (df["Close"]*df["Volume"]).groupby(df["Date"]).cumsum() / df["Volume"].groupby(df["Date"]).cumsum()

typical_price = (df["High"] + df["Low"] + df["Close"]) / 3
vwap_dev = (typical_price - df["VWAP"]).rolling(20).std()

df["VWAP_upper2"] = df["VWAP"] + 2*vwap_dev
df["VWAP_lower2"] = df["VWAP"] - 2*vwap_dev

# -----------------------
# DELTA
# -----------------------

df["delta"] = np.where(df["Close"] > df["Open"], df["Volume"], -df["Volume"])
df["cum_delta"] = df["delta"].cumsum()

# -----------------------
# SMART MONEY (FIXED)
# -----------------------

lookback = 20
df["high_max"] = df["High"].rolling(lookback).max()
df["low_min"] = df["Low"].rolling(lookback).min()

df["sweep_high"] = df["High"] > df["high_max"].shift(1)
df["sweep_low"] = df["Low"] < df["low_min"].shift(1)

df["vol_mean"] = df["Volume"].rolling(20).mean()
df["vol_spike"] = df["Volume"] > df["vol_mean"] * 1.5

df["LongSignal"] = False
df["ShortSignal"] = False

vol_avg = df["Volume"].rolling(20).mean()

for i in range(2, len(df)):
    prev = df.iloc[i-1]
    curr = df.iloc[i]

    score_long = 0
    if prev["sweep_low"]: score_long += 1
    if curr["Close"] > curr["VWAP"]: score_long += 1
    if curr["vol_spike"]: score_long += 1
    if bias_5m == "bull": score_long += 1
    if bias_15m == "bull": score_long += 1
    if curr["delta"] > -vol_avg.iloc[i]: score_long += 1

    if score_long >= 4:
        df.at[df.index[i], "LongSignal"] = True

    score_short = 0
    if prev["sweep_high"]: score_short += 1
    if curr["Close"] < curr["VWAP"]: score_short += 1
    if curr["vol_spike"]: score_short += 1
    if bias_5m == "bear": score_short += 1
    if bias_15m == "bear": score_short += 1
    if curr["delta"] < vol_avg.iloc[i]: score_short += 1

    if score_short >= 4:
        df.at[df.index[i], "ShortSignal"] = True

# -----------------------
# SL / TP
# -----------------------

df["SL"] = np.nan
df["TP"] = np.nan

for i in range(1, len(df)):
    if df["LongSignal"].iloc[i]:
        sl = df["Low"].iloc[i-1]
        entry = df["Close"].iloc[i]
        df.at[df.index[i], "SL"] = sl
        df.at[df.index[i], "TP"] = entry + (entry - sl) * 2

    if df["ShortSignal"].iloc[i]:
        sl = df["High"].iloc[i-1]
        entry = df["Close"].iloc[i]
        df.at[df.index[i], "SL"] = sl
        df.at[df.index[i], "TP"] = entry - (sl - entry) * 2

# -----------------------
# PRICE METRICS
# -----------------------

current = df["Close"].iloc[-1]
prev = df["Close"].iloc[-2]

change = current - prev
change_percent = (change / prev) * 100

vwap_last = df["VWAP"].iloc[-1]
rsi_last = df["RSI"].iloc[-1]

col1, col2, col3 = st.columns(3)

col1.metric("Price", f"{current:.2f}", f"{change:.2f} ({change_percent:.2f}%)")
col2.metric("VWAP", f"{vwap_last:.2f}")
col3.metric("RSI", f"{rsi_last:.2f}")

# -----------------------
# SUPPORT / RESISTANCE
# -----------------------

def detect_levels(df,window=10):
    supports=[]
    resistances=[]
    lows=df["Low"].to_numpy()
    highs=df["High"].to_numpy()

    for i in range(window,len(df)-window):
        if lows[i] == min(lows[i-window:i+window]):
            supports.append(lows[i])
        if highs[i] == max(highs[i-window:i+window]):
            resistances.append(highs[i])
    return supports,resistances

def clean_levels(levels,threshold=0.005):
    filtered=[]
    for l in sorted(levels):
        if not any(abs(l-f)/f < threshold for f in filtered):
            filtered.append(l)
    return filtered

supports,resistances = detect_levels(df)
supports = clean_levels(supports)
resistances = clean_levels(resistances)

# -----------------------
# SUBPLOTS
# -----------------------

rows = 1
if show_volume: rows += 1
if show_rsi: rows += 1
if show_macd: rows += 1

fig = make_subplots(rows=rows, cols=1, shared_xaxes=True)

current_row = 1
price_row = current_row
current_row += 1

# PRICE
fig.add_trace(go.Candlestick(
    x=df.index, open=df["Open"], high=df["High"],
    low=df["Low"], close=df["Close"]
), row=price_row, col=1)

fig.add_trace(go.Scatter(x=df.index,y=df["EMA20"],name="EMA20"),row=price_row,col=1)
fig.add_trace(go.Scatter(x=df.index,y=df["EMA50"],name="EMA50"),row=price_row,col=1)
fig.add_trace(go.Scatter(x=df.index,y=df["VWAP"],name="VWAP"),row=price_row,col=1)

fig.add_trace(go.Scatter(x=df.index,y=df["VWAP_upper2"],name="VWAP +2"),row=price_row,col=1)
fig.add_trace(go.Scatter(x=df.index,y=df["VWAP_lower2"],name="VWAP -2"),row=price_row,col=1)

# SIGNALS
longs = df[df["LongSignal"]]
shorts = df[df["ShortSignal"]]

fig.add_trace(go.Scatter(x=longs.index,y=longs["Close"],
                         mode="markers",marker=dict(symbol="triangle-up",size=10),
                         name="LONG"),row=price_row,col=1)

fig.add_trace(go.Scatter(x=shorts.index,y=shorts["Close"],
                         mode="markers",marker=dict(symbol="triangle-down",size=10),
                         name="SHORT"),row=price_row,col=1)

# S/R
for s in supports[-5:]:
    fig.add_hline(y=s,line_dash="dot",line_color="green",row=price_row,col=1)

for r in resistances[-5:]:
    fig.add_hline(y=r,line_dash="dot",line_color="red",row=price_row,col=1)

# VOLUME
if show_volume:
    fig.add_trace(go.Bar(x=df.index,y=df["Volume"]),row=current_row,col=1)
    current_row += 1

# RSI
if show_rsi:
    fig.add_trace(go.Scatter(x=df.index,y=df["RSI"]),row=current_row,col=1)
    current_row += 1

# MACD
if show_macd:
    fig.add_trace(go.Bar(x=df.index,y=df["MACD_hist"]),row=current_row,col=1)

fig.update_layout(height=1000, template="plotly_dark")

st.plotly_chart(fig, use_container_width=True)