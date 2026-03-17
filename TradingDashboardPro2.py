import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from streamlit_autorefresh import st_autorefresh

st.set_page_config(layout="wide")
st.title("Trading Dashboard PRO")

st_autorefresh(interval=30000, key="datarefresh")

# -----------------------
# SIDEBAR
# -----------------------

symbol = st.sidebar.text_input("Ticker", value="BTC-USD").upper()
period = st.sidebar.selectbox("Period", ["5d","1mo","3mo","6mo","1y"])
interval = st.sidebar.selectbox("Timeframe", ["15m","1h","4h","1d"])

# -----------------------
# OPTIONS
# -----------------------

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
# INDICATORS
# -----------------------

# EMA
df["EMA20"] = df["Close"].ewm(span=20).mean()
df["EMA50"] = df["Close"].ewm(span=50).mean()

# RSI (EMA based)
window = 14
delta = df["Close"].diff()
gain = delta.where(delta > 0, 0)
loss = -delta.where(delta < 0, 0)

avg_gain = gain.ewm(alpha=1/window, min_periods=window).mean()
avg_loss = loss.ewm(alpha=1/window, min_periods=window).mean()

rs = avg_gain / avg_loss.replace(0,1e-10)
df["RSI"] = 100 - (100 / (1 + rs))

# MACD
ema12 = df["Close"].ewm(span=12).mean()
ema26 = df["Close"].ewm(span=26).mean()

df["MACD"] = ema12 - ema26
df["MACD_signal"] = df["MACD"].ewm(span=9).mean()
df["MACD_hist"] = df["MACD"] - df["MACD_signal"]

# VWAP (SESSION)
df["Date"] = df.index.date
df["VWAP"] = (
    (df["Close"] * df["Volume"]).groupby(df["Date"]).cumsum()
    /
    df["Volume"].groupby(df["Date"]).cumsum()
)

# VWAP Bands
typical_price = (df["High"] + df["Low"] + df["Close"]) / 3
vwap_dev = (typical_price - df["VWAP"]).rolling(20).std()

df["VWAP_upper2"] = df["VWAP"] + 2*vwap_dev
df["VWAP_lower2"] = df["VWAP"] - 2*vwap_dev

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

with col1:
    delta_color = "normal" if change >= 0 else "inverse"
    st.metric("Price", f"{current:.2f}", f"{change:.2f} ({change_percent:.2f}%)", delta_color=delta_color)   

with col2:
    st.metric("VWAP", f"{vwap_last:.2f}")

with col3:
    st.metric("RSI", f"{rsi_last:.2f}")
    
 

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
# TRADE SIGNAL
# -----------------------

df["Signal"] = 0

df.loc[
    (df["Close"] > df["VWAP"]) &
    (df["RSI"] > 50) &
    (df["EMA20"] > df["EMA50"]),
    "Signal"
] = 1

df.loc[
    (df["Close"] < df["VWAP"]) &
    (df["RSI"] < 50) &
    (df["EMA20"] < df["EMA50"]),
    "Signal"
] = -1

# -----------------------
# SUBPLOTS
# -----------------------

rows = 1
if show_volume: rows += 1
if show_rsi: rows += 1
if show_macd: rows += 1

fig = make_subplots(
    rows=rows,
    cols=1,
    shared_xaxes=True,
    vertical_spacing=0.02,
    row_heights=[0.6] + [0.13]*(rows-1)
)

current_row = 1
price_row = current_row
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

fig.add_trace(go.Scattergl(x=df.index,y=df["EMA20"],name="EMA20"),row=price_row,col=1)
fig.add_trace(go.Scattergl(x=df.index,y=df["EMA50"],name="EMA50"),row=price_row,col=1)
fig.add_trace(go.Scattergl(x=df.index,y=df["VWAP"],name="VWAP"),row=price_row,col=1)

fig.add_trace(go.Scattergl(x=df.index,y=df["VWAP_upper2"],name="VWAP +2"),row=price_row,col=1)
fig.add_trace(go.Scattergl(x=df.index,y=df["VWAP_lower2"],name="VWAP -2"),row=price_row,col=1)

# Support / Resistance
for s in supports[-5:]:
    fig.add_hline(y=s,line_dash="dot",line_color="green",row=price_row,col=1)

for r in resistances[-5:]:
    fig.add_hline(y=r,line_dash="dot",line_color="red",row=price_row,col=1)

# -----------------------
# VOLUME
# -----------------------

if show_volume:
    fig.add_trace(go.Bar(x=df.index,y=df["Volume"],name="Volume"),
                  row=current_row,col=1)
    current_row += 1

# -----------------------
# RSI
# -----------------------

if show_rsi:
    fig.add_trace(go.Scattergl(x=df.index,y=df["RSI"],name="RSI"),
                  row=current_row,col=1)

    fig.add_hline(y=70,row=current_row,col=1,line_dash="dot")
    fig.add_hline(y=30,row=current_row,col=1,line_dash="dot")

    current_row += 1

# -----------------------
# MACD
# -----------------------

if show_macd:
    fig.add_trace(go.Scattergl(x=df.index,y=df["MACD"],name="MACD"),
                  row=current_row,col=1)

    fig.add_trace(go.Scattergl(x=df.index,y=df["MACD_signal"],name="Signal"),
                  row=current_row,col=1)

    fig.add_trace(go.Bar(x=df.index,y=df["MACD_hist"],name="Hist"),
                  row=current_row,col=1)

# -----------------------
# LAYOUT
# -----------------------

fig.update_layout(
    height=1000,
    template="plotly_dark",
    hovermode="x unified",
    xaxis_rangeslider_visible=False
)

st.plotly_chart(fig,use_container_width=True)

# -----------------------
# SIGNAL OUTPUT
# -----------------------

last_signal = df["Signal"].iloc[-1]

if last_signal == 1:
    st.success("LONG SIGNAL")
elif last_signal == -1:
    st.error("SHORT SIGNAL")
else:
    st.info("NO CLEAR SIGNAL")

# -----------------------
# SCANNER (FAST)
# -----------------------

@st.cache_data(ttl=300)
def load_scanner_batch(assets):
    return yf.download(
        assets,
        period="1mo",
        interval="1d",
        group_by="ticker",
        threads=True
    )

assets=["BTC-USD","ETH-USD","SOL-USD","SPY","AAPL","NVDA","NFLX"]

data_raw = load_scanner_batch(assets)

scanner_data=[]

for a in assets:
    try:
        d = data_raw[a].dropna()
        close = d["Close"].values
        momentum=((close[-1]-close[-10])/close[-10])*100
        scanner_data.append((a,round(momentum,2)))
    except:
        pass

scanner = pd.DataFrame(scanner_data,columns=["Asset","Momentum %"])

st.subheader("Momentum Scanner")
st.dataframe(scanner)