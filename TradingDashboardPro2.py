import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.graph_objects as go
from streamlit_autorefresh import st_autorefresh

st.set_page_config(layout="wide")
st.title("Trading Dashboard PRO")

# Auto Refresh
st_autorefresh(interval=30000, key="datarefresh")

# -----------------------
# SIDEBAR
# -----------------------

st.sidebar.header("Market")

symbol = st.sidebar.text_input("Ticker", value="BTC-USD").upper()

period = st.sidebar.selectbox(
    "Period",
    ["5d","1mo","3mo","6mo","1y"]
)

interval = st.sidebar.selectbox(
    "Timeframe",
    ["15m","1h","4h","1d"]
)

# -----------------------
# CHART OPTIONS
# -----------------------

st.sidebar.header("Chart")

show_candles = st.sidebar.checkbox("Candlestick",True)
show_volume = st.sidebar.checkbox("Volume",True)

show_ema20 = st.sidebar.checkbox("EMA20",True)
show_ema50 = st.sidebar.checkbox("EMA50",True)
show_ema200 = st.sidebar.checkbox("EMA200",True)

show_sma = st.sidebar.checkbox("SMA50",False)

show_support = st.sidebar.checkbox("Support",True)
show_resistance = st.sidebar.checkbox("Resistance",True)

show_sweeps = st.sidebar.checkbox("Liquidity Sweeps",False)
show_orderblocks = st.sidebar.checkbox("Orderblocks",False)

show_vwap = st.sidebar.checkbox("VWAP", True)
show_vwap_bands = st.sidebar.checkbox("VWAP Bands", True)

# -----------------------
# INDICATORS
# -----------------------

st.sidebar.header("Indicators")

show_rsi = st.sidebar.checkbox("RSI",True)
show_macd = st.sidebar.checkbox("MACD",True)

# -----------------------
# ANALYSIS
# -----------------------

st.sidebar.header("Analysis")

show_trend = st.sidebar.checkbox("Trend Strength",True)
show_signals = st.sidebar.checkbox("Trade Signals",True)

# -----------------------
# SCANNER
# -----------------------

st.sidebar.header("Scanner")

show_scanner = st.sidebar.checkbox("Momentum Scanner",True)
show_mtf = st.sidebar.checkbox("Multi Timeframe",False)

# -----------------------
# DATA
# -----------------------

@st.cache_data(ttl=30)
def load_data(symbol,period,interval):

    df = yf.download(symbol,period=period,interval=interval, progress=False)

    if isinstance(df.columns,pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    df = df.dropna()
    return df

df = load_data(symbol,period,interval)

if len(df) == 0:
    st.stop()

# Performance Limit
max_bars = 500
if len(df) > max_bars:
    df = df.tail(max_bars)

# -----------------------
# INDICATORS
# -----------------------

df["EMA20"] = df["Close"].ewm(span=20).mean()
df["EMA50"] = df["Close"].ewm(span=50).mean()
df["EMA200"] = df["Close"].ewm(span=200).mean()

df["SMA50"] = df["Close"].rolling(50).mean()

# RSI
delta = df["Close"].diff()
gain = delta.clip(lower=0)
loss = -delta.clip(upper=0)

avg_gain = gain.rolling(14).mean()
avg_loss = loss.rolling(14).mean()

rs = avg_gain/avg_loss
df["RSI"] = 100-(100/(1+rs))

# MACD
ema12 = df["Close"].ewm(span=12).mean()
ema26 = df["Close"].ewm(span=26).mean()

df["MACD"] = ema12 - ema26
df["MACD_signal"] = df["MACD"].ewm(span=9).mean()
df["MACD_hist"] = df["MACD"] - df["MACD_signal"]

# VWAP
df["VWAP"] = (df["Close"] * df["Volume"]).cumsum() / df["Volume"].cumsum()

# VWAP Bands
vwap_std = (df["Close"] - df["VWAP"]).rolling(20).std()

df["VWAP_upper1"] = df["VWAP"] + vwap_std
df["VWAP_lower1"] = df["VWAP"] - vwap_std

df["VWAP_upper2"] = df["VWAP"] + vwap_std*2
df["VWAP_lower2"] = df["VWAP"] - vwap_std*2

# -----------------------
# SUPPORT RESISTANCE
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

supports,resistances = detect_levels(df)

# -----------------------
# ALERTS
# -----------------------

def check_alerts(df):

    price = df["Close"].iloc[-1]
    upper = df["VWAP_upper2"].iloc[-1]
    lower = df["VWAP_lower2"].iloc[-1]

    alerts = []

    if price > upper:
        alerts.append("Price ABOVE VWAP +2")

    if price < lower:
        alerts.append("Price BELOW VWAP -2")

    return alerts

alerts = check_alerts(df)

# -----------------------
# PRICE
# -----------------------

current = df["Close"].iloc[-1]
prev = df["Close"].iloc[-2]

change = current-prev
change_percent = (change/prev)*100

vwap_last = df["VWAP"].iloc[-1]
rsi_last = df["RSI"].iloc[-1]

col1,col2,col3 = st.columns(3)

with col1:
    st.metric("Price",f"{current:.2f}",f"{change:.2f} ({change_percent:.2f}%)")

with col2:
    st.metric("VWAP",f"{vwap_last:.2f}")

with col3:
    st.metric("RSI",f"{rsi_last:.2f}")

# -----------------------
# CHART
# -----------------------

fig = go.Figure()

if show_candles:

    fig.add_trace(go.Candlestick(
        x=df.index,
        open=df["Open"],
        high=df["High"],
        low=df["Low"],
        close=df["Close"],
        name="Price"
    ))

if show_ema20:
    fig.add_trace(go.Scattergl(x=df.index,y=df["EMA20"],name="EMA20"))

if show_ema50:
    fig.add_trace(go.Scattergl(x=df.index,y=df["EMA50"],name="EMA50"))

if show_ema200:
    fig.add_trace(go.Scattergl(x=df.index,y=df["EMA200"],name="EMA200"))

if show_sma:
    fig.add_trace(go.Scattergl(x=df.index,y=df["SMA50"],name="SMA50"))

if show_vwap:
    fig.add_trace(go.Scattergl(
        x=df.index,
        y=df["VWAP"],
        name="VWAP",
        line=dict(color="orange")
    ))

if show_vwap_bands:

    fig.add_trace(go.Scattergl(x=df.index,y=df["VWAP_upper1"],name="VWAP +1",line=dict(dash="dot")))
    fig.add_trace(go.Scattergl(x=df.index,y=df["VWAP_lower1"],name="VWAP -1",line=dict(dash="dot")))

    fig.add_trace(go.Scattergl(x=df.index,y=df["VWAP_upper2"],name="VWAP +2",line=dict(dash="dot")))
    fig.add_trace(go.Scattergl(x=df.index,y=df["VWAP_lower2"],name="VWAP -2",line=dict(dash="dot")))

if show_support:
    for s in supports[-5:]:
        fig.add_hline(y=s,line_dash="dot",line_color="green")

if show_resistance:
    for r in resistances[-5:]:
        fig.add_hline(y=r,line_dash="dot",line_color="red")

fig.update_layout(
    height=900,
    template="plotly_dark",
    hovermode="x unified",
    xaxis_rangeslider_visible=False
)

st.plotly_chart(fig,use_container_width=True)

# -----------------------
# ALERT DISPLAY
# -----------------------

if alerts:

    st.subheader("Alerts")

    for a in alerts:
        st.warning(a)

# -----------------------
# SCANNER
# -----------------------

@st.cache_data(ttl=300)
def load_scanner(asset):
    return yf.download(asset,period="1mo",interval="1d")

if show_scanner:

    st.subheader("Momentum Scanner")

    assets=["BTC-USD","ETH-USD","SOL-USD","SPY","AAPL","NVDA","NFLX"]

    data=[]

    for a in assets:

        d = load_scanner(a)

        if len(d)<20:
            continue

        close=d["Close"].to_numpy().flatten()

        momentum=((close[-1]-close[-10])/close[-10])*100

        data.append((a,round(momentum,2)))

    scanner=pd.DataFrame(data,columns=["Asset","Momentum %"])

    st.dataframe(scanner)