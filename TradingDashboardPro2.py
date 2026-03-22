import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import numpy as np
import requests
import pytz
from datetime import datetime, time

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
        # FUTURES (24/5)
        # -----------------------
        "ES=F",   # S&P 500 Futures
        "NQ=F",   # Nasdaq Futures
        "YM=F",   # Dow Futures
        "RTY=F",  # Russell
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
        "BTC-USD","ETH-USD","SOL-USD","XRP_USD","ADA_USD"

    ]

def filter_symbols_by_session(symbols):
    session = get_market_session()

    if session == "RTH":
        symbols = symbols[:100]   # volle Power nur im Markt
    else:
        symbols = symbols[:30]    # off-hours = weniger nötig

    if session == "WEEKEND":
        return [s for s in symbols if "=F" in s or "USD" in s]

    elif session in ["PREMARKET", "AFTERHOURS"]:
        # optional: weniger Noise
        return symbols  # oder nur High Beta / Futures

    return symbols
    
@st.cache_data(ttl=60)
def scan_market(limit=100):
    symbols = filter_symbols_by_session(get_sp500_symbols())[:limit]
    results = []

    chunks = np.array_split(symbols, 3)
    data_all = {}

    # --- Download ---
    for chunk in chunks:
        chunk = list(chunk)
        if not chunk:
            continue
        try:
            d = yf.download(
                tickers=chunk,
                period="2d",
                interval="5m",
                group_by="ticker",
                threads=False,
                progress=False
            )
            if isinstance(d.columns, pd.MultiIndex):
                for ticker in chunk:
                    df = d.get(ticker)
                    if df is not None and not df.empty:
                        data_all[ticker] = df.dropna()
            else:
                df = d
                if not df.empty:
                    data_all[chunk[0]] = df.dropna()
        except Exception as e:
            print(f"Download error for chunk {chunk}: {e}")
            continue

    # --- Scan ---
    for s in symbols:
        df = data_all.get(s)
        if df is None or len(df) < 50:
            continue

        try:
            # LIGHT INDICATORS
            df["VWAP"] = (df["Close"] * df["Volume"]).cumsum() / df["Volume"].cumsum()
            df["Volume_MA"] = df["Volume"].rolling(20).mean()

            ema20 = df["Close"].ewm(span=20).mean()
            ema50 = df["Close"].ewm(span=50).mean()

            price = df["Close"].iloc[-1]
            vwap = df["VWAP"].iloc[-1]

            delta = df["Close"].diff()
            gain = delta.clip(lower=0)
            loss = -delta.clip(upper=0)
            avg_gain = gain.ewm(alpha=1/14).mean()
            avg_loss = loss.ewm(alpha=1/14).mean()
            rs = avg_gain / avg_loss.replace(0, 1e-10)
            rsi = 100 - (100 / (1 + rs))
            rsi = rsi.iloc[-1]

            volume = df["Volume"].iloc[-1]
            volume_ma = df["Volume_MA"].iloc[-1]
            vol_spike = volume > 1.5 * volume_ma

            # LIQUIDITY
            high_max = df["High"].rolling(20).max()
            low_min = df["Low"].rolling(20).min()
            sweep_high = df["High"].iloc[-2] > high_max.iloc[-3]
            sweep_low = df["Low"].iloc[-2] < low_min.iloc[-3]

            # TREND
            trend_bull = ema20.iloc[-1] > ema50.iloc[-1]
            trend_bear = ema20.iloc[-1] < ema50.iloc[-1]

            # SIGNAL
            long_score = sum([
                sweep_low, price > vwap, vol_spike, trend_bull, rsi > 60,
                df["Close"].iloc[-1] > df["Close"].iloc[-3],
                sweep_low and price > vwap  # boost
            ])
            short_score = sum([
                sweep_high, price < vwap, vol_spike, trend_bear, rsi < 40,
                sweep_high and price < vwap  # boost
            ])
            score_delta = long_score - short_score

            setup = None
            final_score = 0

            if long_score >= 5 and score_delta > 1:
                setup, final_score = "STRONG LONG", long_score
            elif short_score >= 5 and score_delta < -1:
                setup, final_score = "STRONG SHORT", short_score
            elif long_score >= 4 and score_delta > 1:
                setup, final_score = "LONG", long_score
            elif short_score >= 4 and score_delta < -1:
                setup, final_score = "SHORT", short_score
            elif long_score >= 3 and score_delta > 0:
                setup, final_score = "EARLY LONG (Unsicher)", long_score
            elif short_score >= 3 and score_delta < 0:
                setup, final_score = "EARLY SHORT (Unsicher)", short_score

            if setup:
                results.append({
                    "symbol": s,
                    "price": round(price, 2),
                    "score": final_score,
                    "delta": score_delta,
                    "setup": setup,
                    "volume": int(volume)
                })

        except Exception as e:
            print(f"Scanner error {s}: {e}")
            continue

    df_res = pd.DataFrame(results)
    if df_res.empty:
        return [], []

    df_res = df_res.sort_values("score", ascending=False)
    return df_res.head(10).to_dict("records"), df_res.tail(10).to_dict("records")


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
        ticker = s["symbol"]

        label = f"{ticker} | {s['setup']} | ⭐{s['score']} Δ{s['delta']}"

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

session = get_market_session()

if live_mode:
    if session == "WEEKEND":
        st.caption("Weekend Mode: nur Crypto + Futures")
    elif session != "RTH":
        st.caption(f"Off-hours: {session}")

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

period = st.sidebar.selectbox("Period", ["5d","1mo","3mo","6mo","1y"],key="period_select")
interval = st.sidebar.selectbox("Timeframe", ["1m","5m","15m","1h","4h","1d"],key="interval_select")
    
symbol = st.session_state.symbol   


# -----------------------
# AUTO PERIOD FIX (BEST PRACTICE)
# -----------------------

def auto_period(interval, period):
    valid_map = {
        "1m": ["1d"],
        "5m": ["5d"],
        "15m": ["1mo"],
        "1h": ["3mo"],
        "4h": ["6mo"],
        "1d": ["1y"]
    }

    if interval in valid_map and period not in valid_map[interval]:
        return valid_map[interval][0]  # fallback

    return period

period = auto_period(interval, period)

st.sidebar.caption(f"Aktive Kombi: {interval} / {period}")

show_volume = st.sidebar.checkbox("Volume", True)
show_rsi = st.sidebar.checkbox("RSI", True)
show_macd = st.sidebar.checkbox("MACD", True)


# -----------------------
# DATA
# -----------------------

@st.cache_data(ttl=5)
def load_data(symbol, period, interval, _version=2):
    df = yf.download(symbol, period=period, interval=interval, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df.dropna()



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

df = load_data(symbol, period, interval)

df = normalize_df(df)
df = df.loc[:, ~df.columns.duplicated()]

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

df["date"] = df.index.date

df["VWAP"] = (
    (df["Close"] * df["Volume"]).groupby(df["date"]).cumsum() /
    df["Volume"].groupby(df["date"]).cumsum()
)

typical_price = (df["High"] + df["Low"] + df["Close"]) / 3
vwap_dev = (typical_price - df["VWAP"]).rolling(20).std()

df["VWAP_upper2"] = df["VWAP"] + 2*vwap_dev
df["VWAP_lower2"] = df["VWAP"] - 2*vwap_dev



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
    
    # Optional: NaN vermeiden bei VWAP und BB
df["VWAP"].fillna(method="ffill", inplace=True)
df["VWAP_upper2"].fillna(method="ffill", inplace=True)
df["VWAP_lower2"].fillna(method="ffill", inplace=True)

df["BB_UPPER"].fillna(method="ffill", inplace=True)
df["BB_LOWER"].fillna(method="ffill", inplace=True)
df["BB_MID"].fillna(method="ffill", inplace=True)

# Bollinger Squeeze
df["BB_WIDTH"] = df["BB_UPPER"] - df["BB_LOWER"]
# -----------------------
# DELTA
# -----------------------

df["delta"] = np.where(df["Close"] > df["Open"], df["Volume"], -df["Volume"])
df["cum_delta"] = df["delta"].cumsum()

# -----------------------
# SELL THE NEWS DETECTOR
# -----------------------

lookback = 20
df["high_max"] = df["High"].rolling(lookback).max()
df["low_min"] = df["Low"].rolling(lookback).min()

df["sweep_high"] = df["High"] > df["high_max"].shift(1)
df["sweep_low"] = df["Low"] < df["low_min"].shift(1)

df["vol_mean"] = df["Volume"].rolling(20).mean()
df["vol_spike"] = df["Volume"] > df["vol_mean"] * 1.5

df["SellNewsShort"] = False
df["SellNewsLong"] = False

start = max(2, len(df) - 100)

for i in range(start, len(df)):
    prev = df.iloc[i-1]
    curr = df.iloc[i]

    # SHORT: Fake Breakout nach oben → Reversal
    if (
        prev["sweep_high"] and
        prev["Close"] > prev["VWAP"] and   # vorher stark
        curr["Close"] < curr["VWAP"] and   # jetzt schwach
        curr["Close"] < prev["Close"]      # Momentum kippt
    ):
        df.at[df.index[i], "SellNewsShort"] = True

    # LONG: Fake Breakdown → Reversal
    if (
        prev["sweep_low"] and
        prev["Close"] < prev["VWAP"] and
        curr["Close"] > curr["VWAP"] and
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

for i in range(start, len(df)):
    prev = df.iloc[i-1]
    curr = df.iloc[i]

    score_long = 0
    if prev["sweep_low"]: score_long += 1
    if curr["Close"] > curr["VWAP"]: score_long += 1
    if curr["vol_spike"]: score_long += 1
    if bias_5m == "bull": score_long += 1
    if bias_15m == "bull": score_long += 1
    if curr["delta"] > -vol_avg.iloc[i]: score_long += 1
    if df["SellNewsLong"].iloc[i]:
        score_long += 2
    if curr["delta"] > vol_avg.iloc[i] * 0.3:
        score_long += 1    
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
    if df["SellNewsShort"].iloc[i]:
        score_short += 2


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

df["ScoreDelta"] = df["LongScore"] - df["ShortScore"]

start = max(2, len(df) - 100)

for i in range(start, len(df)):

    if HIGH_PROB_MODE:
        if (
            df["LongScore"].iloc[i] >= SCORE_THRESHOLD and
            df["ScoreDelta"].iloc[i] > 1
        ):
            df.at[df.index[i], "LongSignal"] = True

        if (
            df["ShortScore"].iloc[i] >= SCORE_THRESHOLD and
            df["ScoreDelta"].iloc[i] < -1
        ):
            df.at[df.index[i], "ShortSignal"] = True

    else:
        if df["LongScore"].iloc[i] >= 4:
            df.at[df.index[i], "LongSignal"] = True

        if df["ShortScore"].iloc[i] >= 4:
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

col1.metric(
    display_name,
    f"{current:.2f}",
    f"{change:.2f} ({change_percent:.2f}%)"
)

col2.metric("VWAP", f"{vwap_last:.2f}")
col3.metric("RSI", f"{rsi_last:.2f}")


session = get_market_session()
st.caption(f"Session: {session}")

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

df = df.replace([np.inf, -np.inf], np.nan)

df = df.fillna(method="bfill").fillna(method="ffill")

if len(df) < 50:
    st.warning("Zu wenig Daten")
    st.stop()

last_time = df.index[-1]
st.caption(f"Last update: {last_time}")

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
# Hauptchart = 0.5, Rest gleichmäßig
if rows == 1:
    row_heights = [1.0]
else:
    main_height = 0.5
    remaining_height = 1 - main_height
    small_height = remaining_height / (rows - 1)
    row_heights = [main_height] + [small_height] * (rows - 1)

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
price_range = df["High"].max() - df["Low"].min()

filtered_supports = [s for s in supports if abs(s - current_price) < price_range * 0.3]
filtered_resistances = [r for r in resistances if abs(r - current_price) < price_range * 0.3]

if len(filtered_supports) == 0:
    filtered_supports = supports[:2]

if len(filtered_resistances) == 0:
    filtered_resistances = resistances[:2]
    
for s in filtered_supports:
    fig.add_hline(
        y=s,
        line_dash="dot",
        line_color="green",
        line_width=1.5,
        opacity=0.4,
        row=price_row,
        col=1
    )
    fig.add_annotation(
    x=df.index[-1],
    y=s,
    text=f"S {s:.0f}",
    showarrow=False,
    font=dict(size=14),
    xanchor="left"
)

for r in filtered_resistances:
    fig.add_hline(
        y=r,
        line_dash="dot",
        line_color="red",
        line_width=1.5,
        opacity=0.4,
        row=price_row,
        col=1
    )
    fig.add_annotation(
        x=df.index[-1],
        y=r,
        text=f"R {r:.0f}",
        showarrow=False,
        font=dict(size=14),
        xanchor="left"
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
    
    fig.add_annotation(
        x=df.index[-1],
        y=df["Volume"].iloc[-1],
        text=f"Vol {df['Volume'].iloc[-1]:.0f}",
        showarrow=False,
        xanchor="left",
        row=volume_row,
        col=1,
        font=dict(size=14)
    )

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
    
    fig.add_annotation(
        x=df.index[-1],
        y=df["RSI"].iloc[-1],
        text=f"RSI {df['RSI'].iloc[-1]:.1f}",
        showarrow=False,
        xanchor="left",
        row=rsi_row,
        col=1,
        yshift=20,
        font=dict(size=14)
    )

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
    
    fig.add_annotation(
        x=df.index[-1],
        y=df["MACD"].iloc[-1],
        text=f"MACD {df['MACD'].iloc[-1]:.2f}",
        showarrow=False,
        xanchor="left",
        row=macd_row,
        col=1,
        yshift=10,
        font=dict(size=14)
    )

    fig.add_annotation(
        x=df.index[-1],
        y=df["MACD_signal"].iloc[-1],
        text=f"Signal {df['MACD_signal'].iloc[-1]:.2f}",
        showarrow=False,
        xanchor="left",
        row=macd_row,
        col=1,
        yshift=30,
        font=dict(size=14)
    )    
    
# -----------------------
# SCORE
# -----------------------
    
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
    
    fig.add_annotation(
        x=df.index[-1],
        y=df["LongScore"].iloc[-1],
        text=f"L {df['LongScore'].iloc[-1]:.0f}",
        showarrow=False,
        xanchor="left",
        row=score_row,
        col=1,
        font=dict(size=14)
    )

    fig.add_annotation(
        x=df.index[-1],
        y=df["ShortScore"].iloc[-1],
        text=f"S {df['ShortScore'].iloc[-1]:.0f}",
        showarrow=False,
        xanchor="left",
        row=score_row,
        col=1,
        yshift=20,
        font=dict(size=14)
    ) 
    
    fig.add_trace(go.Scatter(
        x=df.index,
        y=df["ScoreDelta"],
        name="Delta"
        ), row=score_row, col=1
    )
    
# -----------------------
# LAYOUT (WICHTIG)
# -----------------------

height = 400 + (rows * 250)

fig.update_layout(
    template="plotly_dark",

    paper_bgcolor="#0e1117",
    plot_bgcolor="#0e1117",

    font=dict(color="#e6e6e6"),

    xaxis=dict(
        showgrid=False,
        color="#e6e6e6",
        zeroline=False
    ),
    yaxis=dict(
        showgrid=True,
        gridcolor="#1f2937",
        color="#e6e6e6",
        zeroline=False
    ),

    height=height,
    hovermode="x unified",
    xaxis_rangeslider_visible=False,
    uirevision="constant"
    
    legend=dict(
    font=dict(
        color="#f3f4f6",   # heller Text
        size=12
    ),
    bgcolor="#111827",       # dunkler Hintergrund für die Legende
    bordercolor="#1f2937",
    borderwidth=1,
    orientation="h",        # optional: horizontal
    yanchor="bottom",
    y=1.02,
    xanchor="right",
    x=1
)
)

# 🔥 WICHTIG: ALLE SUBPLOTS überschreiben
for i in range(1, rows+1):
    fig.update_xaxes(
        showgrid=False,
        color="#e6e6e6",
        row=i, col=1
    )
    fig.update_yaxes(
        showgrid=True,
        gridcolor="#1f2937",
        color="#e6e6e6",
        row=i, col=1
    )

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

st.plotly_chart(
    fig,
    use_container_width=True
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