import yfinance as yf
import pandas as pd
import numpy as np

from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.spinner import Spinner
from kivy.uix.button import Button
from kivy.uix.label import Label

import matplotlib.pyplot as plt
from kivy_garden.matplotlib.backend_kivyagg import FigureCanvasKivyAgg


# -----------------------
# INDICATORS
# -----------------------

def calculate_indicators(df):

    df["EMA20"] = df["Close"].ewm(span=20).mean()
    df["EMA50"] = df["Close"].ewm(span=50).mean()
    df["EMA200"] = df["Close"].ewm(span=200).mean()

    df["SMA50"] = df["Close"].rolling(50).mean()

    delta = df["Close"].diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.rolling(14).mean()
    avg_loss = loss.rolling(14).mean()

    rs = avg_gain / avg_loss

    df["RSI"] = 100 - (100/(1+rs))

    ema12 = df["Close"].ewm(span=12).mean()
    ema26 = df["Close"].ewm(span=26).mean()

    df["MACD"] = ema12 - ema26
    df["MACD_signal"] = df["MACD"].ewm(span=9).mean()
    df["MACD_hist"] = df["MACD"] - df["MACD_signal"]

    return df


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


# -----------------------
# SIGNAL
# -----------------------

def trade_signal(df):

    price=df["Close"].iloc[-1]
    ema50=df["EMA50"].iloc[-1]
    rsi=df["RSI"].iloc[-1]

    if price > ema50 and rsi < 35:
        return "LONG"

    if price < ema50 and rsi > 65:
        return "SHORT"

    return "NONE"


# -----------------------
# UI
# -----------------------

class TradingLayout(BoxLayout):

    def __init__(self, **kwargs):

        super().__init__(orientation="vertical", **kwargs)

        self.symbol="BTC-USD"

        self.spinner = Spinner(
            text="BTC-USD",
            values=("BTC-USD","ETH-USD","SOL-USD","AAPL","NVDA","SPY"),
            size_hint=(1,0.1)
        )

        self.spinner.bind(text=self.change_asset)

        self.add_widget(self.spinner)

        self.price_label = Label(
            text="Price...",
            font_size=30,
            size_hint=(1,0.1)
        )

        self.add_widget(self.price_label)

        btn = Button(
            text="Load Dashboard",
            size_hint=(1,0.1)
        )

        btn.bind(on_press=self.load_dashboard)

        self.add_widget(btn)

        self.chart_area = BoxLayout()

        self.add_widget(self.chart_area)

        self.signal_label = Label(
            text="Signal: ...",
            font_size=25,
            size_hint=(1,0.1)
        )

        self.add_widget(self.signal_label)


    def change_asset(self,spinner,text):

        self.symbol=text


    def load_dashboard(self,instance):

        df = yf.download(self.symbol,period="6mo",interval="1d")

        df = calculate_indicators(df)

        supports,resistances = detect_levels(df)

        signal = trade_signal(df)

        price = df["Close"].iloc[-1]

        self.price_label.text = f"{self.symbol} {round(price,2)}"

        self.signal_label.text = f"Signal: {signal}"

        fig, ax = plt.subplots()

        ax.plot(df["Close"],label="Price")
        ax.plot(df["EMA20"],label="EMA20")
        ax.plot(df["EMA50"],label="EMA50")

        for s in supports[-3:]:
            ax.axhline(s,color="green",linestyle="--")

        for r in resistances[-3:]:
            ax.axhline(r,color="red",linestyle="--")

        ax.legend()

        self.chart_area.clear_widgets()

        self.chart_area.add_widget(
            FigureCanvasKivyAgg(fig)
        )


class TradingApp(App):

    def build(self):

        return TradingLayout()


TradingApp().run()