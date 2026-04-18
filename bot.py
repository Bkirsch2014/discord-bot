import discord
from discord.ext import commands
from discord import app_commands
import aiohttp

import os
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
API_KEY = os.getenv("ALPHA_VANTAGE_KEY")
GUILD_ID = int(os.getenv("GUILD_ID"))

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)


class MyBot(commands.Bot):
    pass


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    try:
        guild = discord.Object(id=GUILD_ID)
        synced = await bot.tree.sync(guild=guild)
        print(f"Synced {len(synced)} commands")
        for cmd in synced:
            print(f"- {cmd.name}")
    except Exception as e:
        print("Sync error:", e)


@bot.tree.command(name="news", description="Get stock news", guild=discord.Object(id=GUILD_ID))
@app_commands.describe(symbol="Stock ticker")
async def news(interaction: discord.Interaction, symbol: str):
    await interaction.response.defer()

    url = f"https://www.alphavantage.co/query?function=NEWS_SENTIMENT&tickers={symbol.upper()}&apikey={API_KEY}"

    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            data = await response.json()

    articles = data.get("feed", [])[:3]

    if not articles:
        await interaction.followup.send(f"No news found for {symbol.upper()}.")
        return

    msg = ""
    for article in articles:
        title = article.get("title", "No title")
        link = article.get("url", "")
        msg += f"**{title}**\n{link}\n\n"

    await interaction.followup.send(msg)


@bot.tree.command(name="analyze", description="Market data snapshot", guild=discord.Object(id=GUILD_ID))
@app_commands.describe(symbol="Stock ticker")
async def analyze(interaction: discord.Interaction, symbol: str):
    await interaction.response.defer()

    import yfinance as yf
    import pandas as pd
    from datetime import datetime, time

    symbol = symbol.upper().strip()

    try:
        ticker = yf.Ticker(symbol)

        # Daily data for trend / previous day levels
        daily = ticker.history(period="3mo", interval="1d", auto_adjust=False)

        # Intraday data including pre/post market
        intraday = ticker.history(period="1d", interval="1m", prepost=True, auto_adjust=False)

        if daily.empty:
            await interaction.followup.send(f"No data found for {symbol}.")
            return

        # -------- Daily trend data --------
        daily = daily.dropna()

        closes = daily["Close"]
        if len(closes) < 50:
            await interaction.followup.send(f"Not enough daily data to analyze {symbol}.")
            return

        price = float(closes.iloc[-1])
        prev_close = float(closes.iloc[-2])

        ma20 = float(closes.tail(20).mean())
        ma50 = float(closes.tail(50).mean())

        if price > ma20 and price > ma50:
            trend = "Bullish (above 20 & 50 MA)"
        elif price < ma20 and price < ma50:
            trend = "Bearish (below 20 & 50 MA)"
        elif price > ma20 and price < ma50:
            trend = "Mixed (above 20, below 50)"
        else:
            trend = "Mixed (below 20, above 50)"

        pct_change = ((price - prev_close) / prev_close) * 100 if prev_close else 0

        today_high = None
        today_low = None
        premarket_high = None
        premarket_low = None
        today_volume = None

        # Previous full day levels from daily candles
        prev_day_high = float(daily["High"].iloc[-2])
        prev_day_low = float(daily["Low"].iloc[-2])

        # -------- Intraday session data --------
        if not intraday.empty:
            intraday = intraday.dropna().copy()

            # Handle timezone-aware timestamps safely
            intraday_index = intraday.index

            # Split session into premarket and regular market
            regular_start = time(9, 30)
            regular_end = time(16, 0)

            times = pd.Series(intraday_index.time, index=intraday.index)

            regular_mask = (times >= regular_start) & (times <= regular_end)
            premarket_mask = times < regular_start

            regular_df = intraday.loc[regular_mask]
            premarket_df = intraday.loc[premarket_mask]

            if not regular_df.empty:
                today_high = float(regular_df["High"].max())
                today_low = float(regular_df["Low"].min())
                today_volume = int(regular_df["Volume"].sum())

                # Use latest regular-session close as price during market hours
                try:
                    price = float(regular_df["Close"].iloc[-1])
                    pct_change = ((price - prev_close) / prev_close) * 100 if prev_close else 0
                except Exception:
                    pass

            if not premarket_df.empty:
                premarket_high = float(premarket_df["High"].max())
                premarket_low = float(premarket_df["Low"].min())

        def fmt_price(value):
            return f"${value:.2f}" if value is not None else "N/A"

        def fmt_pct(value):
            return f"{value:+.2f}%"

        def fmt_volume(value):
            return f"{value:,}" if value is not None else "N/A"

        message = (
            f"**{symbol}**\n\n"
            f"**Price:** {fmt_price(price)}\n"
            f"**Change:** {fmt_pct(pct_change)}\n"
            f"**Trend:** {trend}\n\n"
            f"**20 MA:** {fmt_price(ma20)}\n"
            f"**50 MA:** {fmt_price(ma50)}\n\n"
            f"**Today High:** {fmt_price(today_high)}\n"
            f"**Today Low:** {fmt_price(today_low)}\n\n"
            f"**Premarket High:** {fmt_price(premarket_high)}\n"
            f"**Premarket Low:** {fmt_price(premarket_low)}\n\n"
            f"**Prev Day High:** {fmt_price(prev_day_high)}\n"
            f"**Prev Day Low:** {fmt_price(prev_day_low)}\n\n"
            f"**Volume:** {fmt_volume(today_volume)}"
        )

        await interaction.followup.send(message)

    except Exception as e:
        await interaction.followup.send(f"Error analyzing {symbol}: {e}")
bot.run(TOKEN)