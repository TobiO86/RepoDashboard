import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.graph_objects as go

st.set_page_config(layout="wide")
st.title("Trading Dashboard PRO")

# -----------------------
# SIDEBAR
# -----------------------

st.sidebar.header("Market")

symbol = st.sidebar.selectbox(
    "Asset",
    ["BTC-USD","ETH-USD","SOL-USD","XRP-USD","AAPL","NVDA","SPY","QQQ","GC=F","MSTR","NFLX"]
)

period = st.sidebar.selectbox(
    "Period",
    ["1mo","3mo","6mo","1y","2y"]
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
# LOAD DATA
# -----------------------

@st.cache_data(ttl=300)
def load_data(symbol,period,interval):

    df = yf.download(symbol,period=period,interval=interval)

    if isinstance(df.columns,pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    df = df.dropna()

    return df


df = load_data(symbol,period,interval)

if len(df)==0:
    st.stop()

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

# -----------------------
# CURRENT INDICATOR VALUES
# -----------------------

ema20_last = df["EMA20"].iloc[-1]
ema50_last = df["EMA50"].iloc[-1]
ema200_last = df["EMA200"].iloc[-1]

rsi_last = df["RSI"].iloc[-1]

macd_last = df["MACD"].iloc[-1]
macd_signal_last = df["MACD_signal"].iloc[-1]
macd_hist_last = df["MACD_hist"].iloc[-1]

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


supports,resistances = detect_levels(df)

# -----------------------
# LIQUIDITY SWEEPS
# -----------------------

def detect_sweeps(df):

    sweeps=[]

    highs=df["High"].to_numpy()
    lows=df["Low"].to_numpy()
    closes=df["Close"].to_numpy()

    for i in range(2,len(df)):

        if highs[i] > highs[i-1] and closes[i] < highs[i-1]:
            sweeps.append(("bearish",i))

        if lows[i] < lows[i-1] and closes[i] > lows[i-1]:
            sweeps.append(("bullish",i))

    return sweeps


sweeps = detect_sweeps(df)

# -----------------------
# ORDERBLOCKS
# -----------------------

def detect_orderblocks(df):

    blocks=[]

    open_=df["Open"].to_numpy()
    close=df["Close"].to_numpy()
    high=df["High"].to_numpy()
    low=df["Low"].to_numpy()

    for i in range(1,len(df)):

        body=abs(close[i]-open_[i])
        prev_body=abs(close[i-1]-open_[i-1])

        if body>prev_body*2:

            if close[i]>open_[i]:
                blocks.append(("bullish",low[i-1]))

            if close[i]<open_[i]:
                blocks.append(("bearish",high[i-1]))

    return blocks


orderblocks = detect_orderblocks(df)

# -----------------------
# TREND SCORE
# -----------------------

def trend_strength(df):

    score=0

    closes=df["Close"].to_numpy()
    ema50=df["EMA50"].to_numpy()
    ema200=df["EMA200"].to_numpy()

    if closes[-1] > ema50[-1]:
        score += 1

    if ema50[-1] > ema200[-1]:
        score += 1

    momentum = ((closes[-1]-closes[-5])/closes[-5])*100

    if momentum > 5:
        score += 1

    if momentum < -5:
        score -= 1

    return score


score = trend_strength(df)

# -----------------------
# TRADE SIGNAL
# -----------------------

def trade_signal(df):

    price=df["Close"].iloc[-1]
    ema50=df["EMA50"].iloc[-1]
    rsi=df["RSI"].iloc[-1]

    if price > ema50 and rsi < 35:
        return "LONG SETUP"

    if price < ema50 and rsi > 65:
        return "SHORT SETUP"

    return "NO SIGNAL"


signal = trade_signal(df)

# -----------------------
# PRICE INFO
# -----------------------

current=df["Close"].iloc[-1]
prev=df["Close"].iloc[-2]

change=current-prev
change_percent=(change/prev)*100

st.metric("Price",f"{current:.2f}",f"{change:.2f} ({change_percent:.2f}%)")

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
    fig.add_trace(go.Scatter(x=df.index,y=df["EMA20"],name="EMA20"))

if show_ema50:
    fig.add_trace(go.Scatter(x=df.index,y=df["EMA50"],name="EMA50"))

if show_ema200:
    fig.add_trace(go.Scatter(x=df.index,y=df["EMA200"],name="EMA200"))

if show_sma:
    fig.add_trace(go.Scatter(x=df.index,y=df["SMA50"],name="SMA50"))

# Support
if show_support:
    for s in supports:
        fig.add_hline(y=s,line_dash="dot",line_color="green")

# Resistance
if show_resistance:
    for r in resistances:
        fig.add_hline(y=r,line_dash="dot",line_color="red")

# Liquidity Sweeps
if show_sweeps:

    for s in sweeps:

        idx=s[1]

        if s[0]=="bullish":

            fig.add_trace(go.Scatter(
                x=[df.index[idx]],
                y=[df["Low"].iloc[idx]],
                mode="markers",
                marker=dict(size=10,color="green"),
                name="Bull Sweep"
            ))

        if s[0]=="bearish":

            fig.add_trace(go.Scatter(
                x=[df.index[idx]],
                y=[df["High"].iloc[idx]],
                mode="markers",
                marker=dict(size=10,color="red"),
                name="Bear Sweep"
            ))

fig.update_layout(
    height=900,
    template="plotly_dark",
    hovermode="x unified",
    xaxis_rangeslider_visible=False
)

st.plotly_chart(fig,use_container_width=True)

# -----------------------
# SUPPORT / RESISTANCE TABLE
# -----------------------

st.subheader("Support & Resistance Levels")

levels=[]

for s in supports[-5:]:
    levels.append(("Support",round(s,2)))

for r in resistances[-5:]:
    levels.append(("Resistance",round(r,2)))

levels_df=pd.DataFrame(levels,columns=["Type","Price"])

st.dataframe(levels_df,use_container_width=True)

# -----------------------
# VOLUME
# -----------------------

if show_volume:

    st.subheader("Volume")

    vol_fig = go.Figure()

    vol_fig.add_trace(go.Bar(
        x=df.index,
        y=df["Volume"]
    ))

    vol_fig.update_layout(template="plotly_dark")

    st.plotly_chart(vol_fig,use_container_width=True)

# -----------------------
# RSI
# -----------------------

if show_rsi:

    st.subheader("RSI")

    rsi_fig = go.Figure()

    rsi_fig.add_trace(go.Scatter(x=df.index,y=df["RSI"]))

    rsi_fig.add_hline(y=70)
    rsi_fig.add_hline(y=30)

    rsi_fig.update_layout(template="plotly_dark")

    st.plotly_chart(rsi_fig,use_container_width=True)

# -----------------------
# MACD
# -----------------------

if show_macd:

    st.subheader("MACD")

    macd_fig = go.Figure()

    macd_fig.add_trace(go.Scatter(
        x=df.index,
        y=df["MACD"],
        name="MACD"
    ))

    macd_fig.add_trace(go.Scatter(
        x=df.index,
        y=df["MACD_signal"],
        name="Signal"
    ))

    macd_fig.add_trace(go.Bar(
        x=df.index,
        y=df["MACD_hist"],
        name="Histogram"
    ))

    macd_fig.update_layout(template="plotly_dark")

    st.plotly_chart(macd_fig,use_container_width=True)
    
    # -----------------------
# INDICATOR VALUES PANEL
# -----------------------

st.subheader("Indicator Values")

col1,col2,col3,col4 = st.columns(4)

with col1:
    st.metric("EMA20",f"{ema20_last:.2f}")
    st.metric("EMA50",f"{ema50_last:.2f}")
    st.metric("EMA200",f"{ema200_last:.2f}")

with col2:
    st.metric("RSI",f"{rsi_last:.2f}")

with col3:
    st.metric("MACD",f"{macd_last:.2f}")
    st.metric("Signal",f"{macd_signal_last:.2f}")

with col4:
    st.metric("MACD Histogram",f"{macd_hist_last:.2f}")

# -----------------------
# ANALYSIS
# -----------------------

if show_trend:

    st.subheader("Market Analysis")

    st.write("Trend Score:",score)
    st.write("Liquidity Sweeps:",len(sweeps))
    st.write("Orderblocks:",len(orderblocks))

# -----------------------
# SIGNAL
# -----------------------

if show_signals:

    st.subheader("Trade Signal")

    if signal=="LONG SETUP":
        st.success(signal)

    elif signal=="SHORT SETUP":
        st.error(signal)

    else:
        st.info(signal)

# -----------------------
# MOMENTUM SCANNER
# -----------------------

if show_scanner:

    st.subheader("Momentum Scanner")

    assets=["BTC-USD","ETH-USD","SOL-USD","SPY","AAPL","NVDA","NFLX"]

    data=[]

    for a in assets:

        d=yf.download(a,period="1mo",interval="1d")

        if len(d)<20:
            continue

        close=d["Close"].to_numpy().flatten()

        momentum=((close[-1]-close[-10])/close[-10])*100

        data.append((a,round(momentum,2)))

    scanner=pd.DataFrame(data,columns=["Asset","Momentum %"])

    st.dataframe(scanner)

# -----------------------
# MULTI TIMEFRAME
# -----------------------

if show_mtf:

    st.subheader("Multi Timeframe Analysis")

    timeframes=["1h","4h","1d"]

    results=[]

    for tf in timeframes:

        d=yf.download(symbol,period="1mo",interval=tf)

        if len(d) < 10:
            continue

        close=d["Close"].to_numpy().flatten()

        momentum=((close[-1]-close[-10])/close[-10])*100

        results.append((tf,round(momentum,2)))

    tf_df=pd.DataFrame(results,columns=["Timeframe","Momentum"])

    st.dataframe(tf_df)