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

@st.cache_data(ttl=180)
def scan_market(limit=100):
    symbols = filter_symbols_by_session(get_sp500_symbols(), SESSION)[:limit]
    results = []

    data_all = {}
    chunks = np.array_split(symbols, 3)

    # --- Download ---
    for chunk in chunks:
        try:
            d = yf.download(tickers=" ".join(chunk.tolist()), period="2d", interval="5m", group_by="ticker", threads=True, progress=False)
            if isinstance(d.columns, pd.MultiIndex):
                for ticker in chunk:
                    df = d.get(ticker)
                    if df is not None and not df.empty:
                        data_all[ticker] = df.dropna()
            else:
                data_all[chunk[0]] = d.dropna()
        except:
            continue

    # --- Scan ---
    for s in symbols:
        df = data_all.get(s)
        if df is None or len(df) < 10:
            continue

        try:
            df["VWAP_RTH"] = (df["Close"] * df["Volume"]).groupby(df.index.date).cumsum() / df["Volume"].groupby(df.index.date).cumsum()
            ema20 = df["Close"].ewm(span=20).mean()
            ema50 = df["Close"].ewm(span=50).mean()
            ema200 = df["Close"].ewm(span=200).mean()

            price = df["Close"].iloc[-1]

            # --- Liquidity ---
            avg_vol = df["Volume"].rolling(20).mean().iloc[-1]
            dollar_vol = price * avg_vol

            if dollar_vol < 10_000_000:
                continue

            df["ATR"] = compute_atr(df)
            
            if df["ATR"].isna().all():
                continue
            atr = df["ATR"].iloc[-1]
            atr_pct = atr / price

            if atr_pct < 0.005:
                continue

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
            
            
            # --- Relative Volume ---
            rel_vol = df["Volume"].iloc[-1] / avg_vol

            if rel_vol < 1.3:
                continue
            
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

            long_score = sum([sweep_low, price>vwap, vol_spike, trend_bull, rsi>55])
            short_score = sum([sweep_high, price<vwap, vol_spike, trend_bear, rsi<45])
            delta_score = long_score - short_score

            setup = None
            score = max(long_score, short_score)

            # --- Lockerere Bedingungen ---
            if long_score >= 2:
                setup = "LONG"
            elif short_score >= 2:
                setup = "SHORT"

            if setup:
                results.append({
                    "symbol": s,
                    "price": round(price,2),
                    "score": score,
                    "delta": delta_score,
                    "setup": setup,
                    "volume": int(df["Volume"].iloc[-1])
                })

        except:
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
    st.session_state.period_select = valid_map["5m"][0]

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
df = mark_premarket(df)
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
    df["vol_rth"] = np.where(df["Session"] == "RTH", df["Volume"], 0)
    df["pv_rth"] = tp * df["vol_rth"]

    df["cum_vol_rth"] = df.groupby(df.index.date)["vol_rth"].cumsum()
    df["cum_pv_rth"] = df.groupby(df.index.date)["pv_rth"].cumsum()

    df["VWAP_RTH"] = df["cum_pv_rth"] / df["cum_vol_rth"]

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

df["Vol_Current"] = df["Volume"]
df["Vol_Avg"] = df["Volume"].rolling(20).mean()

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
       
    if (
        score_long  >= 6 and
        df["MarketRegime"].iloc[i] == "TREND"
    ):
        df.at[df.index[i], "LongSignal"] = True

    elif (
        score_short  >= 6 and
        df["MarketRegime"].iloc[i] == "TREND"
    ):
        df.at[df.index[i], "ShortSignal"] = True

    # RANGE MODE
    elif (
        score_long  >= 5 and
        df["MarketRegime"].iloc[i] == "RANGE"
    ):
        df.at[df.index[i], "LongSignal"] = True

    elif (
        score_short >= 5 and
        df["MarketRegime"].iloc[i] == "RANGE"
    ):
        df.at[df.index[i], "ShortSignal"] = True 
    
    
     
# -----------------------
# HIGH PROBABILITY FILTER
# -----------------------

HIGH_PROB_MODE = True
SCORE_THRESHOLD = 5

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
        entry = df["Close"].iloc[i]
        atr = df["ATR"].iloc[i]
        df.at[df.index[i], "SL"] = entry - atr * 1.5
        df.at[df.index[i], "TP"] = entry + atr * 2.5

    if df["ShortSignal"].iloc[i]:
        entry = df["Close"].iloc[i]
        atr = df["ATR"].iloc[i]
        df.at[df.index[i], "SL"] = entry + atr * 1.5
        df.at[df.index[i], "TP"] = entry - atr * 2.5

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

# 2️⃣ Aktueller Preis (Premarket / Afterhours / letzte Kerze)
current_price = df_fast["Close"].iloc[-1]

# 3️⃣ Delta vom Last Price zum aktuellen Preis
delta_price = last_rth_price -current_price
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

volume_value = round(df["Vol_Current"].iloc[-1], 2)
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

# Variante 1: direkt auf df
df = df.bfill().ffill()

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
show_volume = True
show_rsi = True
show_macd = True

current_row = 1

price_row = current_row
current_row += 1

# 🔥 Timeline IMMER Row 2
timeline_row = current_row
current_row += 1

volume_row = None
if show_volume:
    volume_row = current_row
    current_row += 1

score_row = None
if show_score:
    score_row = current_row
    current_row += 1

rsi_row = None
if show_rsi:
    rsi_row = current_row
    current_row += 1

macd_row = None
if show_macd:
    macd_row = current_row
    current_row += 1

rows = current_row - 1

titles = ["Price", "Timeline"]

if show_volume:
    titles.append("Volume")

if show_score:
    titles.append("Score")

if show_rsi:
    titles.append("RSI")

if show_macd:
    titles.append("MACD")
    
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

fig = make_subplots(
    rows=rows,
    cols=1,
    shared_xaxes=True,
    vertical_spacing=0.03,
    row_heights=row_heights,
    subplot_titles=titles
)

price_row = 1
timeline_row = 2
volume_row = 3 if show_volume else None
score_row = 4 if show_score else None
rsi_row = 5 if show_rsi else None
macd_row = 6 if show_macd else None

# -----------------------
# PRICE
# -----------------------

# --- Sessions ---
# Subsets
rth_df = df[df["Session"] == "RTH"]
pre_df = df[df["Session"] == "PREMARKET"]
ah_df  = df[df["Session"] == "AFTERHOURS"]

print(price_row)
print(rth_df["VWAP_RTH"])
print(pre_df["VWAP_PRE"])
print(ah_df["VWAP_AH"])

# RTH Candles – Standardfarben
fig.add_trace(go.Candlestick(
    x=rth_df.index,
    open=rth_df["Open"],
    high=rth_df["High"],
    low=rth_df["Low"],
    close=rth_df["Close"],
    name="RTH",
    increasing_line_color='green',
    decreasing_line_color='red'
))

# PREMARKET Candles – leicht transparent
fig.add_trace(go.Candlestick(
    x=pre_df.index,
    open=pre_df["Open"],
    high=pre_df["High"],
    low=pre_df["Low"],
    close=pre_df["Close"],
    name="PRE",
    increasing_line_color='lightgreen',
    decreasing_line_color='lightcoral',
    opacity=0.5
))

# AFTERHOURS Candles – leicht transparent
fig.add_trace(go.Candlestick(
    x=ah_df.index,
    open=ah_df["Open"],
    high=ah_df["High"],
    low=ah_df["Low"],
    close=ah_df["Close"],
    name="AH",
    increasing_line_color='lightblue',
    decreasing_line_color='lightsalmon',
    opacity=0.5
))

fig.add_trace(go.Candlestick(
    x=df.index, open=df["Open"], high=df["High"], low=df["Low"], close=df["Close"], name="Price"
))

if not rth_df.empty and "VWAP_RTH" in rth_df.columns and rth_df["VWAP_RTH"].notna().any():
    fig.add_trace(
        go.Scatter(
            x=rth_df.index,
            y=rth_df["VWAP_RTH"],
            name="VWAP_RTH",
            line=dict(width=3, color="yellow")
        ),
        row=price_row, col=1
    )

if not pre_df.empty and "VWAP_PRE" in pre_df.columns and pre_df["VWAP_PRE"].notna().any():
    fig.add_trace(
        go.Scatter(
            x=pre_df.index,
            y=pre_df["VWAP_PRE"],
            name="VWAP_PRE",
            line=dict(width=3, color="orange")
        ),
        row=price_row, col=1
    )

if not ah_df.empty and "VWAP_AH" in ah_df.columns and ah_df["VWAP_AH"].notna().any():
    fig.add_trace(
        go.Scatter(
            x=ah_df.index,
            y=ah_df["VWAP_AH"],
            name="VWAP_AH",
            line=dict(width=3, color="purple")
        ),
        row=price_row, col=1
    ) 

fig.add_trace(go.Scatter(x=df.index,y=df["EMA20"],name="EMA20"),row=price_row,col=1)
fig.add_trace(go.Scatter(x=df.index,y=df["EMA50"],name="EMA50"),row=price_row,col=1)

fig.add_trace(go.Scatter(x=df.index,y=df["VWAP_upper2"],name="VWAP +2"),row=price_row,col=1)
fig.add_trace(go.Scatter(x=df.index,y=df["VWAP_lower2"],name="VWAP -2"),row=price_row,col=1)

fig.add_trace(go.Scatter(x=df.index,y=df["KC_UPPER"],name="KC Upper"),row=price_row,col=1)
fig.add_trace(go.Scatter(x=df.index, y=df["KC_MID"],  line=dict(color="blue", width=1),  name="KC Mid"))
fig.add_trace(go.Scatter(x=df.index,y=df["KC_LOWER"],name="KC Lower"),row=price_row,col=1)
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


if show_volume and volume_row is not None:
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

if show_rsi and rsi_row is not None:
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

if show_macd and macd_row is not None:
    fig.add_trace(
        go.Scatter(
            x=df.index,
            y=df["MACD"],
            name="MACD"
        ),
        row=macd_row,
        col=1
    )

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
  
if show_score and score_row is not None:
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

    VWAP: {df['VWAP_RTH'].iloc[-1]:.2f}
    RSI: {df['RSI'].iloc[-1]:.2f}

    SL: {sl_text}
    TP: {tp_text}
    """ 
    send_telegram(message)
    st.session_state.last_signal = current_signal