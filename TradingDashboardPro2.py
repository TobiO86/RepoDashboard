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
    
@st.cache_data(ttl=180)
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
            df["date"] = df.index.date

            df["VWAP"] = (
                (df["Close"] * df["Volume"]).groupby(df["date"]).cumsum() /
                df["Volume"].groupby(df["date"]).cumsum()
            )
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
show_rth_only_vwap = st.sidebar.checkbox("VWAP nur RTH", True)
color_sessions = st.sidebar.checkbox("Sessions farbig", True)

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
    
symbol = st.session_state.symbol   

# -----------------------
# AUTO PERIOD / INTERVAL FIX
# -----------------------
def auto_period_interval(period, interval):
    """
    Stellt sicher, dass period und interval zusammenpassen.
    Fügt period='1d' hinzu.
    """
    valid_map = {
        "1m": ["1d", "5d"],
        "5m": ["1d", "5d"],
        "15m": ["5d", "1mo"],
        "1h": ["1mo", "3mo"],
        "4h": ["3mo", "6mo"],
        "1d": ["1y", "max"]
    }

    # Falls period nicht kompatibel mit interval
    if interval in valid_map:
        if period not in valid_map[interval]:
            # fallback = erstes gültiges period
            period = valid_map[interval][0]

    # Optional: falls interval nicht passt zu period, anpassen
    for key, periods in valid_map.items():
        if period in periods:
            if interval not in valid_map[key]:
                interval = key  # erstes gültiges Intervall
            break

    return period, interval

show_volume = st.sidebar.checkbox("Volume", True)
show_rsi = st.sidebar.checkbox("RSI", True)
show_macd = st.sidebar.checkbox("MACD", True)

period = st.sidebar.selectbox(
    "Period", 
    ["1d", "5d", "1mo", "3mo", "6mo", "1y"],  # 1d hinzugefügt
    key="period_select"
)

interval = st.sidebar.selectbox(
    "Timeframe", 
    ["1m","5m","15m","1h","4h","1d"],
    key="interval_select"
)

# Nutzung:
period, interval = auto_period_interval(period, interval)
st.sidebar.caption(f"Aktive Kombi: {interval} / {period}")

# -----------------------
# DATA
# -----------------------
@st.cache_data(ttl=5)
def load_fast_price(symbol):
    df = yf.download(symbol, period="1d", interval="1m", progress=False)

    if df.empty or "Close" not in df:
        return pd.DataFrame()

    df = df.dropna()

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

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

@st.cache_data(ttl=10)
def load_futures_prices():
    futures = {
        "ES": "ES=F",   # S&P500
        "NQ": "NQ=F",   # Nasdaq
        "YM": "YM=F",   # Dow
        "CL": "CL=F",   # Öl
        "NG": "NG=F",   # Gas
        "GC": "GC=F",   # Gold
        "SI": "SI=F",   # Silber
        "HG": "HG=F",   # Kupfer
        "ZN": "ZN=F",   # 10 Jahres Anleihen
        "ZB": "ZB=F",   # 30 Jahres Anleihen
        "DX": "DX=F"    # Dollar
    }

    prices = {}

    for name, ticker in futures.items():
        try:
            df = yf.download(
                ticker,
                period="1d",
                interval="1m",
                progress=False,
                prepost=True  # <-- Pre- und After-Hours aktiv
            )
            if df.empty:
                prices[name] = np.nan
                continue

            # Letzter Close, inkl. Pre/After-Hours
            prices[name] = float(df["Close"].iloc[-1])

        except:
            prices[name] = np.nan

    return prices

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
    return df.dropna()

et = pytz.timezone("US/Eastern")

def mark_premarket(df):
    df['Session'] = 'RTH'
    for idx in df.index:
        local_time = idx.tz_convert(et).time()
        if time(4,0) <= local_time < time(9,30):
            df.at[idx, 'Session'] = 'PREMARKET'
        elif time(16,0) <= local_time < time(20,0):
            df.at[idx, 'Session'] = 'AFTERHOURS'
        else:
            df.at[idx, 'Session'] = 'RTH'
    return df

@st.cache_data(ttl=60)
def load_multi_exchange(symbol, period, interval):
    # Mapping US -> EU (erweiterbar)
    eu_map = {
        "TSLA": "TSLA.DE",
        "NVDA": "NVDA.DE",
        "AAPL": "AAPL.DE",
        "MSFT": "MSF.DE",
        "AMZN": "AMZ.DE"
    }

    symbol_eu = eu_map.get(symbol)

    # US Daten (bestehend)
    df_us = yf.download(
        symbol,
        period=period,
        interval=interval,
        progress=False,
        prepost=True
    )

    if isinstance(df_us.columns, pd.MultiIndex):
        df_us.columns = df_us.columns.get_level_values(0)

    df_us = df_us.dropna()

    # Falls kein EU Symbol → return normal
    if not symbol_eu:
        df_us["EU_Close"] = np.nan
        df_us["Spread"] = np.nan
        return df_us, None

    # EU Daten
    df_eu = yf.download(
        symbol_eu,
        period=period,
        interval=interval,
        progress=False,
        prepost=True
    )

    if isinstance(df_eu.columns, pd.MultiIndex):
        df_eu.columns = df_eu.columns.get_level_values(0)

    df_eu = df_eu.dropna()

    # Timezone Alignment
    try:
        df_us.index = df_us.index.tz_convert("US/Eastern")
        df_eu.index = df_eu.index.tz_convert("US/Eastern")
    except:
        pass

    # Align
    df_us["EU_Close"] = df_eu["Close"].reindex(df_us.index, method="ffill")

    # Spread
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
futures = load_futures_prices()
df = normalize_df(df)
df = df.loc[:, ~df.columns.duplicated()]

# 2️⃣ Leeren DataFrame abfangen
if df.empty:
    st.warning("Keine Daten verfügbar")
    st.stop()

# 3️⃣ Premarket markieren
df = mark_premarket(df)
df["EU_Close"] = df["EU_Close"].fillna(method="ffill")
df["Spread"] = df["Spread"].fillna(0)

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

def compute_vwap(df, rth_only=True):
    df = df.copy()

    # Typischer Preis
    tp = (df["High"] + df["Low"] + df["Close"]) / 3

    # Session-Tag erstellen (immer)
    df["session_date"] = df["Session"] + "_" + df.index.date.astype(str)

    # Nur RTH berücksichtigen?
    if rth_only:
        df["vol_rth"] = np.where(df["Session"] == "RTH", df["Volume"], 0)
        df["pv_rth"] = tp * df["vol_rth"]
        df["cum_vol"] = df.groupby(df.index.date)["vol_rth"].cumsum()
        df["cum_pv"] = df.groupby(df.index.date)["pv_rth"].cumsum()
    else:
        # Premarket + RTH + Afterhours getrennt
        df["cum_vol"] = df.groupby("session_date")["Volume"].cumsum()
        df["cum_pv"] = (tp * df["Volume"]).groupby(df["session_date"]).cumsum()

    vwap = df["cum_pv"] / df["cum_vol"]
    return vwap

df["VWAP"] = compute_vwap(df, rth_only=show_rth_only_vwap)

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

    if curr["Close"] > curr["VWAP"]:
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
    if prev["sweep_low"] and curr["Close"] > curr["VWAP"]:
        score_long += weights["confirmation"]

    # Sell the news Reversal
    if df["SellNewsLong"].iloc[i]:
        score_long += weights["sellnews"]

        df.at[df.index[i], "LongScore"] = score_long

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

    if curr["Close"] < curr["VWAP"]:
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
    if prev["sweep_high"] and curr["Close"] < curr["VWAP"]:
        score_short += weights["confirmation"]

    # Sell the news Reversal
    if df["SellNewsShort"].iloc[i]:
        score_short += weights["sellnews"]

    # Optional: runden
    df.at[df.index[i], "ShortScore"] = round(score_short, 2)

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

# Letzter RTH-Close für korrektes Delta
df_rth = df_fast[mark_premarket(df_fast)["Session"] == "RTH"]
last_rth_close = safe_last(df_rth["Close"], default=prev)

# Aktueller Preis inkl. Pre/Post
current_price = safe_last(df_fast["Close"], default=prev)

# Prozentuale Änderung relativ zum letzten RTH-Close
delta_price = current_price - last_rth_close
delta_percent = (delta_price / last_rth_close) * 100

# VWAP inkl. Pre/Post
typical_price = (df_fast["High"] + df_fast["Low"] + df_fast["Close"]) / 3
vwap_last = (typical_price * df_fast["Volume"]).cumsum() / df_fast["Volume"].cumsum()
vwap_last = safe_last(vwap_last, default=current_price)

# Optional: EU-Preis falls vorhanden
eu_price, eu_symbol = load_global_prices(symbol)
eu_display = f" | EU: ${eu_price:.2f}" if not np.isnan(eu_price) else ""

vwap_last = (df_fast["Close"] * df_fast["Volume"]).cumsum() / df_fast["Volume"].cumsum()
vwap_last = vwap_last.iloc[-1]

rsi_last = df["RSI"].iloc[-1]

# -----------------------
# AUTO REFRESH (NUR METRICS)
# -----------------------

if get_market_session() == "RTH":
    st_autorefresh(interval=5000, key="price_refresh")

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

col1, col2, col3, col4 = st.columns(4)

# Metric anzeigen
col1.metric(
    label=display_name,
    value=f"${current_price:.2f}",
    delta=f"{delta_price:+.2f} ({delta_percent:+.2f}%) | VWAP: ${vwap_last:.2f}{eu_display}"
)




col2.metric("VWAP", f"${vwap_last:.2f}")

rsi_last = df["RSI"].iloc[-1]
col3.metric("RSI", f"{rsi_last:.2f}")

# EU PRICE
if not np.isnan(eu_price):
    col4.metric(
        f"{eu_symbol}",
        f"${eu_price:.2f}"
    )
else:
    col4.metric("EU", "-")

# ROW 2 (Futures separat)
col5, col6, col7 = st.columns(3)

# FUTURES
col5.metric("ES (S&P)",f"{futures.get('ES', np.nan):.0f}" if not np.isnan(futures.get('ES', np.nan)) else "-")
col6.metric("NQ (Nasdaq)",f"{futures.get('NQ', np.nan):.0f}" if not np.isnan(futures.get('NQ', np.nan)) else "-")
col7.metric("YM (Dow)",f"{futures.get('YM', np.nan):.0f}" if not np.isnan(futures.get('YM', np.nan)) else "-")

# ROW 3 (Commodities / weitere Futures)
col8, col9, col10, col11 = st.columns(4)

col8.metric("CL (Oil)",f"{futures.get('CL', np.nan):.2f}" if not np.isnan(futures.get('CL', np.nan)) else "-")
col9.metric("NG (Gas)",f"{futures.get('NG', np.nan):.2f}" if not np.isnan(futures.get('NG', np.nan)) else "-")
col10.metric("GC (Gold)",f"{futures.get('GC', np.nan):.2f}" if not np.isnan(futures.get('GC', np.nan)) else "-")
col11.metric("SI (Silver)",f"{futures.get('SI', np.nan):.2f}" if not np.isnan(futures.get('SI', np.nan)) else "-")

# ROW 4 (Macro Futures)
col12, col13, col14, col15 = st.columns(4)

col12.metric("HG (Copper)",f"{futures.get('HG', np.nan):.2f}" if not np.isnan(futures.get('HG', np.nan)) else "-")
col13.metric("ZN (10Y)",f"{futures.get('ZN', np.nan):.2f}" if not np.isnan(futures.get('ZN', np.nan)) else "-")
col14.metric("ZB (30Y)", f"{futures.get('ZB', np.nan):.2f}" if not np.isnan(futures.get('ZB', np.nan)) else "-")
col15.metric("DX (Dollar)", f"{futures.get('DX', np.nan):.2f}" if not np.isnan(futures.get('DX', np.nan)) else "-")

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


rows = 5  # Price, Volume, Score, Indicator, Timeline
row_heights = [0.5, 0.15, 0.15, 0.1, 0.1]
titles = ["Price", "Volume", "Score", "Indicator", "Timeline"]

# Timeline row ist die letzte
timeline_row = rows

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

volume_row = current_row if show_volume else None
if show_volume: current_row += 1

rsi_row = current_row if show_rsi else None
if show_rsi: current_row += 1

macd_row = current_row if show_macd else None
if show_macd: current_row += 1

score_row = current_row if show_score else None
if show_score: current_row += 1

# -----------------------
# PRICE
# -----------------------

rth_df = df[df["Session"] == "RTH"]
pre_df = df[df["Session"] == "PREMARKET"]
after_df = df[df["Session"] == "AFTERHOURS"]

# Premarket-Candles (optional dünner / transparenter)
pre_df = df[df["Session"]=="PREMARKET"]
if not pre_df.empty:
    if color_sessions:
        # Premarket
        fig.add_trace(go.Candlestick(
            x=pre_df.index,
            open=pre_df["Open"],
            high=pre_df["High"],
            low=pre_df["Low"],
            close=pre_df["Close"],
            name="Premarket",
            increasing_line_color='lightblue',
            decreasing_line_color='lightblue',
            opacity=0.4
        ), row=price_row, col=1)

        # Afterhours
        fig.add_trace(go.Candlestick(
            x=after_df.index,
            open=after_df["Open"],
            high=after_df["High"],
            low=after_df["Low"],
            close=after_df["Close"],
            name="Afterhours",
            increasing_line_color='orange',
            decreasing_line_color='orange',
            opacity=0.4
        ), row=price_row, col=1)

    # RTH (Hauptchart)
    fig.add_trace(go.Candlestick(
        x=rth_df.index,
        open=rth_df["Open"],
        high=rth_df["High"],
        low=rth_df["Low"],
        close=rth_df["Close"],
        name="RTH"
    ), row=price_row, col=1)
 
if symbol_eu:
    fig.add_trace(go.Scatter(
        x=df.index,
        y=df["EU_Close"],
        name=f"{symbol_eu} (EU)",
        line=dict(dash="dot", width=2)
    ), row=price_row, col=1)

if show_rth_only_vwap:
    fig.add_trace(go.Scatter(
        x=rth_df.index,
        y=rth_df["VWAP"],
        name="VWAP (RTH)",
        line=dict(width=3, color="yellow")
    ), row=price_row, col=1)
else:
    # Separate VWAPs für jede Session
    for session_name, session_df in {
        "PRE": pre_df,
        "RTH": rth_df,
        "AH": after_df
    }.items():
        if not session_df.empty:
            fig.add_trace(go.Scatter(
                x=session_df.index,
                y=session_df["VWAP"],
                name=f"VWAP {session_name}",
                line=dict(width=2, dash="dot")
            ), row=price_row, col=1)


fig.add_trace(go.Scatter(x=df.index,y=df["EMA20"],name="EMA20"),row=price_row,col=1)
fig.add_trace(go.Scatter(x=df.index,y=df["EMA50"],name="EMA50"),row=price_row,col=1)
fig.add_trace(go.Scatter(x=df.index,y=df["VWAP"],name="VWAP"),row=price_row,col=1)

fig.add_trace(go.Scatter(x=df.index,y=df["VWAP_upper2"],name="VWAP +2"),row=price_row,col=1)
fig.add_trace(go.Scatter(x=df.index,y=df["VWAP_lower2"],name="VWAP -2"),row=price_row,col=1)

session_colors = {
    "PREMARKET": "lightblue",
    "RTH": "white",
    "AFTERHOURS": "lightcoral"
}
# Timeline über Candles setzen
fig.add_trace(
    go.Scatter(
        x=df.index,
        y=[0]*len(df),  # dummy y-Werte
        mode='markers',
        marker=dict(
            color=[session_colors[s] for s in df['Session']],
            size=6
        ),
        showlegend=False,
        hoverinfo="x+text",
        text=df['Session']
    ),
    row=timeline_row, col=1
)

fig.update_yaxes(visible=False, row=timeline_row, col=1)
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
    
if "Spread" in df.columns:
    fig.add_trace(go.Scatter(
        x=df.index,
        y=df["Spread"],
        name="Spread (US-EU)",
        line=dict(width=1)
    ), row=score_row, col=1)
    
    fig.add_annotation(
        x=df.index[-1],
        y=df["Spread"].iloc[-1],
        text=f"Spread {df['Spread'].iloc[-1]:.2f}",
        showarrow=False,
        xanchor="left",
        row=score_row,
        col=1,
        yshift=40,
        font=dict(size=12)
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
    uirevision="constant",
    
    legend=dict(
        font=dict(color="#f3f4f6", size=12),
        bgcolor="#111827",
        bordercolor="#1f2937",
        borderwidth=1,
        orientation="v",
        xanchor="left",
        x=1.02,        # rechts vom Plot
        yanchor="top",
        y=1,
        traceorder="normal"
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