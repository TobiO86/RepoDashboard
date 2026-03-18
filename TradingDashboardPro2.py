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

st_autorefresh(interval=5000, key="datarefresh")

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

@st.cache_data(ttl=5)
def load_data(symbol,period,interval):
    df = yf.download(symbol,period=period,interval=interval, progress=False)
    if isinstance(df.columns,pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df.dropna()

df = load_data(symbol,period,interval)
if len(df) == 0:
    st.stop()

df = df.tail(300)

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
    close = df["Close"]

    # Falls MultiIndex / DataFrame → auf Series reduzieren
    if isinstance(close, pd.DataFrame):
        close = close.iloc[:, 0]

    ema20 = close.ewm(span=20).mean()
    ema50 = close.ewm(span=50).mean()

    return "bull" if float(ema20.iloc[-1]) > float(ema50.iloc[-1]) else "bear"


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

df["VWAP"] = (df["Close"] * df["Volume"]).cumsum() / df["Volume"].cumsum()

typical_price = (df["High"] + df["Low"] + df["Close"]) / 3
vwap_dev = (typical_price - df["VWAP"]).rolling(20).std()

df["VWAP_upper2"] = df["VWAP"] + 2*vwap_dev
df["VWAP_lower2"] = df["VWAP"] - 2*vwap_dev

df = df.replace([np.inf, -np.inf], np.nan)

df = df.dropna(subset=[
    "Close","EMA20","EMA50","VWAP",
    "RSI","MACD","MACD_signal"
])
# -----------------------
# BOLLINGER BANDS
# -----------------------

bb_period = 20
bb_std = 2

df["BB_MID"] = df["Close"].rolling(bb_period).mean()
df["BB_STD"] = df["Close"].rolling(bb_period).std()

df["BB_UPPER"] = df["BB_MID"] + bb_std * df["BB_STD"]
df["BB_LOWER"] = df["BB_MID"] - bb_std * df["BB_STD"]

# Bollinger Squeeze
df["BB_WIDTH"] = df["BB_UPPER"] - df["BB_LOWER"]
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

df["LongScore"] = 0
df["ShortScore"] = 0

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

    # LONG nur wenn echter Reclaim
    if prev["sweep_low"] and curr["Close"] > prev["Low"]:
        score_long += 1

    # 🔥 Liquidity Boost
    if prev["sweep_low"] and curr["Close"] > curr["VWAP"]:
        score_long += 2

    df.at[df.index[i], "LongScore"] = score_long

    score_short = 0
    if prev["sweep_high"]: score_short += 1
    if curr["Close"] < curr["VWAP"]: score_short += 1
    if curr["vol_spike"]: score_short += 1
    if bias_5m == "bear": score_short += 1
    if bias_15m == "bear": score_short += 1
    if curr["delta"] < vol_avg.iloc[i]: score_short += 1

    # SHORT nur wenn echter Rejection
    if prev["sweep_high"] and curr["Close"] < prev["High"]:
        score_short += 1
        
    # 🔥 Liquidity Boost
    if prev["sweep_high"] and curr["Close"] < curr["VWAP"]:
        score_short += 2

    df.at[df.index[i], "ShortScore"] = score_short

# -----------------------
# HIGH PROBABILITY FILTER
# -----------------------

HIGH_PROB_MODE = True
SCORE_THRESHOLD = 5

df["LongSignal"] = False
df["ShortSignal"] = False

for i in range(len(df)):

    if HIGH_PROB_MODE:
        if df["LongScore"].iloc[i] >= SCORE_THRESHOLD:
            df.at[df.index[i], "LongSignal"] = True

        if df["ShortScore"].iloc[i] >= SCORE_THRESHOLD:
            df.at[df.index[i], "ShortSignal"] = True

    else:
        if df["LongScore"].iloc[i] >= 4:
            df.at[df.index[i], "LongSignal"] = True

        if df["ShortScore"].iloc[i] >= 4:
            df.at[df.index[i], "ShortSignal"] = True

# -----------------------
# SL / TP
# -----------------------

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

def clean_levels(levels,threshold=0.002):
    filtered=[]
    for l in sorted(levels):
        if not any(abs(l-f)/f < threshold for f in filtered):
            filtered.append(l)
    return filtered

supports,resistances = detect_levels(df)
if len(supports) == 0:
    supports = [df["Low"].min()]

if len(resistances) == 0:
    resistances = [df["High"].max()]
    
supports = clean_levels(supports)
resistances = clean_levels(resistances)

current_price = df["Close"].iloc[-1]

supports = sorted(supports, key=lambda x: abs(x - current_price))[:5]
resistances = sorted(resistances, key=lambda x: abs(x - current_price))[:5]

df.replace([np.inf, -np.inf], np.nan, inplace=True)

df = df.replace([np.inf, -np.inf], np.nan)

df = df.dropna(subset=[
    "Close","EMA20","EMA50","VWAP",
    "RSI","MACD","MACD_signal",
    "BB_UPPER","BB_LOWER","BB_MID"
])
# -----------------------
# SUBPLOTS (FIXED UI)
# -----------------------

show_score = True


rows = 1
titles = ["Price"]


if show_volume:
    rows += 1
    titles.append("Volume")

if show_rsi:
    rows += 1
    titles.append("RSI")

if show_macd:
    rows += 1
    titles.append("MACD")

if show_score:
    rows += 1
    titles.append("Score")

# 👉 WICHTIG: größere Hauptchart-Gewichtung
row_heights = [0.6]

remaining = rows - 1
if remaining > 0:
    small_height = 0.4 / remaining
    row_heights += [small_height] * remaining

fig = make_subplots(
    rows=rows,
    cols=1,
    shared_xaxes=True,
    vertical_spacing=0.03,
    row_heights=row_heights,
    subplot_titles=titles
)

current_row = 1
price_row = current_row
current_row += 1

volume_row = None
rsi_row = None
macd_row = None
score_row = None

if show_volume:
    volume_row = current_row
    current_row += 1

if show_rsi:
    rsi_row = current_row
    current_row += 1

if show_macd:
    macd_row = current_row
    current_row += 1

if show_score:
    score_row = current_row
    current_row += 1
# -----------------------
# PRICE
# -----------------------

fig.add_trace(go.Candlestick(
    x=df.index,
    open=df["Open"],
    high=df["High"],
    low=df["Low"],
    close=df["Close"],
    name="Price"
), row=price_row, col=1)

fig.add_trace(go.Scatter(x=df.index,y=df["EMA20"],name="EMA20"),row=price_row,col=1)
fig.add_trace(go.Scatter(x=df.index,y=df["EMA50"],name="EMA50"),row=price_row,col=1)
fig.add_trace(go.Scatter(x=df.index,y=df["VWAP"],name="VWAP"),row=price_row,col=1)

fig.add_trace(go.Scatter(x=df.index,y=df["VWAP_upper2"],name="VWAP +2"),row=price_row,col=1)
fig.add_trace(go.Scatter(x=df.index,y=df["VWAP_lower2"],name="VWAP -2"),row=price_row,col=1)

# -----------------------
# SUPPORT / RESISTANCE LINES
# -----------------------

for s in supports:
    fig.add_hline(
        y=s,
        line_dash="dot",
        line_color="green",
        opacity=0.4,
        row=price_row,
        col=1
    )

for r in resistances:
    fig.add_hline(
        y=r,
        line_dash="dot",
        line_color="red",
        opacity=0.4,
        row=price_row,
        col=1
    )
    
# -----------------------
# BOLLINGER BANDS PLOT
# -----------------------

fig.add_trace(go.Scatter(
    x=df.index,
    y=df["BB_UPPER"],
    name="BB Upper",
    connectgaps=False,
    line=dict(width=1, dash="dot")
), row=price_row, col=1)

fig.add_trace(go.Scatter(
    x=df.index,
    y=df["BB_LOWER"],
    name="BB Lower",
    connectgaps=False,
    line=dict(width=1, dash="dot")
), row=price_row, col=1)

fig.add_trace(go.Scatter(
    x=df.index,
    y=df["BB_MID"],
    name="BB Mid",
    connectgaps=False,
    line=dict(width=1)
), row=price_row, col=1)    

# SIGNALS
longs = df[df["LongSignal"]]
shorts = df[df["ShortSignal"]]

fig.add_trace(go.Scatter(
    x=longs.index, y=longs["Close"],
    mode="markers",
    marker=dict(symbol="triangle-up", size=12),
    name="LONG",
    connectgaps=False,
), row=price_row, col=1)

fig.add_trace(go.Scatter(
    x=shorts.index, y=shorts["Close"],
    mode="markers",
    marker=dict(symbol="triangle-down", size=12),
    name="SHORT",
    connectgaps=False,
), row=price_row, col=1)


# -----------------------
# VOLUME
# -----------------------

if show_volume:
    fig.add_trace(go.Bar(
        x=df.index,
        y=df["Volume"],
        name="Volume"
    ), row=volume_row, col=1)

# -----------------------
# RSI
# -----------------------

if show_rsi:
    fig.add_trace(go.Scatter(
        x=df.index,
        y=df["RSI"],
        name="RSI"
    ), row=rsi_row, col=1)

    fig.add_hline(y=70, line_dash="dot", row=rsi_row, col=1)
    fig.add_hline(y=30, line_dash="dot", row=rsi_row, col=1)

# -----------------------
# MACD
# -----------------------

if show_macd:
    fig.add_trace(go.Scatter(
        x=df.index,
        y=df["MACD"],
        name="MACD"
    ), row=macd_row, col=1)

    fig.add_trace(go.Scatter(
        x=df.index,
        y=df["MACD_signal"],
        name="Signal"
    ), row=macd_row, col=1)

    fig.add_trace(go.Bar(
        x=df.index,
        y=df["MACD_hist"],
        name="Histogram"
    ), row=macd_row, col=1)

    
if show_score:
    fig.add_trace(go.Scatter(
        x=df.index,
        y=df["LongScore"],
        name="Long Score",
        line=dict(width=1, dash="dot")
    ), row=score_row, col=1)

    fig.add_trace(go.Scatter(
        x=df.index,
        y=df["ShortScore"],
        name="Short Score",
        line=dict(width=1, dash="dot")
    ), row=score_row, col=1)

    fig.add_hline(y=5, line_dash="dash", row=score_row, col=1)

    fig.update_yaxes(range=[0,8], row=score_row, col=1)    
    
# -----------------------
# LAYOUT (WICHTIG)
# -----------------------

fig.update_layout(
    height=1100,  # 👈 größer!
    template="plotly_dark",
    hovermode="x unified",
    xaxis_rangeslider_visible=False,
    uirevision=f"{symbol}_{interval}"
)

st.plotly_chart(
    fig,
    use_container_width=True,
    key=f"chart_{symbol}_{interval}"
)

# -----------------------
# ALERT / SIGNAL OUTPUT
# -----------------------

last_long = df["LongSignal"].iloc[-1]
last_short = df["ShortSignal"].iloc[-1]

if last_long:
    st.success("🚀 SMART LONG (Score-based Setup)")
elif last_short:
    st.error("🔻 SMART SHORT (Score-based Setup)")
else:
    st.info("NO HIGH PROBABILITY SETUP")
    
# -----------------------
# TELEGRAM ALERTS
# -----------------------

def send_telegram(msg):
    TOKEN = "DEIN_TELEGRAM_BOT_TOKEN"
    CHAT_ID = "DEINE_CHAT_ID"
    
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    
    try:
        requests.post(url, data={
            "chat_id": CHAT_ID,
            "text": msg
        })
    except Exception as e:
        print("Telegram Error:", e)

# -----------------------
# ANTI-SPAM LOGIK
# -----------------------

if "last_signal" not in st.session_state:
    st.session_state.last_signal = None

current_signal = None
current_price = df["Close"].iloc[-1]

if df["LongSignal"].iloc[-1]:
    current_signal = f"🚀 LONG @ {current_price:.2f}"

elif df["ShortSignal"].iloc[-1]:
    current_signal = f"🔻 SHORT @ {current_price:.2f}"

# Nur senden wenn neues Signal
if current_signal and current_signal != st.session_state.last_signal:
    
    sl = df["SL"].iloc[-1]
    tp = df["TP"].iloc[-1]

    sl_text = f"{sl:.2f}" if not np.isnan(sl) else "-"
    tp_text = f"{tp:.2f}" if not np.isnan(tp) else "-"

    score = df["LongScore"].iloc[-1] if df["LongSignal"].iloc[-1] else df["ShortScore"].iloc[-1]

    message = f"""
    {symbol} SIGNAL

    {current_signal}
    Score: {score}/8

    VWAP: {df['VWAP'].iloc[-1]:.2f}
    RSI: {df['RSI'].iloc[-1]:.2f}

    SL: {sl_text}
    TP: {tp_text}
    """ 
    send_telegram(message)
    st.session_state.last_signal = current_signal