import os
from datetime import time

import aiohttp
import discord
import pandas as pd
import yfinance as yf
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
API_KEY = os.getenv("ALPHA_VANTAGE_KEY")
guild_id_raw = os.getenv("GUILD_ID")

if not TOKEN:
    raise ValueError("Missing DISCORD_TOKEN environment variable")
if not guild_id_raw:
    raise ValueError("Missing GUILD_ID environment variable")

GUILD_ID = int(guild_id_raw)

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)


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
        print(f"Sync error: {e}")


def fmt_price(value):
    return f"${value:.2f}" if value is not None else "N/A"


def fmt_pct(value):
    return f"{value:+.2f}%" if value is not None else "N/A"


def fmt_volume(value):
    return f"{int(value):,}" if value is not None else "N/A"


def get_trend(price: float, ma20: float, ma50: float) -> str:
    if price > ma20 and price > ma50:
        return "Bullish (above 20 & 50 MA)"
    if price < ma20 and price < ma50:
        return "Bearish (below 20 & 50 MA)"
    if price > ma20 and price < ma50:
        return "Mixed (above 20, below 50)"
    return "Mixed (below 20, above 50)"


@bot.tree.command(name="news", description="Get stock news", guild=discord.Object(id=GUILD_ID))
@app_commands.describe(symbol="Stock ticker")
async def news(interaction: discord.Interaction, symbol: str):
    await interaction.response.defer()

    if not API_KEY:
        await interaction.followup.send("Missing ALPHA_VANTAGE_KEY in environment variables.")
        return

    symbol = symbol.upper().strip()
    url = (
        "https://www.alphavantage.co/query"
        f"?function=NEWS_SENTIMENT&tickers={symbol}&limit=5&apikey={API_KEY}"
    )

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=30) as response:
                data = await response.json()

        articles = data.get("feed", [])[:5]

        if not articles:
            await interaction.followup.send(f"No news found for {symbol}.")
            return

        embed = discord.Embed(
            title=f"{symbol} News",
            description="Latest headlines",
        )

        for article in articles[:5]:
            title = article.get("title", "No title")
            source = article.get("source", "Unknown source")
            article_url = article.get("url", "")
            summary = article.get("summary", "No summary available.")

            value = f"**Source:** {source}\n"
            if article_url:
                value += f"[Read article]({article_url})\n"
            value += f"{summary[:180]}..."

            embed.add_field(
                name=title[:256],
                value=value[:1024],
                inline=False
            )

        await interaction.followup.send(embed=embed)

    except Exception as e:
        await interaction.followup.send(f"Error fetching news for {symbol}: {e}")


@bot.tree.command(name="analyze", description="Market data snapshot", guild=discord.Object(id=GUILD_ID))
@app_commands.describe(symbol="Stock ticker")
async def analyze(interaction: discord.Interaction, symbol: str):
    await interaction.response.defer()

    symbol = symbol.upper().strip()

    try:
        ticker = yf.Ticker(symbol)

        # Daily candles for MA + previous day data
        daily = ticker.history(period="3mo", interval="1d", auto_adjust=False)

        # Intraday with pre/post market
        intraday = ticker.history(period="1d", interval="1m", prepost=True, auto_adjust=False)

        if daily.empty:
            await interaction.followup.send(f"No data found for {symbol}.")
            return

        daily = daily.dropna()
        closes = daily["Close"]

        if len(closes) < 50:
            await interaction.followup.send(f"Not enough data to analyze {symbol}.")
            return

        price = float(closes.iloc[-1])
        prev_close = float(closes.iloc[-2])
        ma20 = float(closes.tail(20).mean())
        ma50 = float(closes.tail(50).mean())
        trend = get_trend(price, ma20, ma50)
        pct_change = ((price - prev_close) / prev_close) * 100 if prev_close else 0

        prev_day_high = float(daily["High"].iloc[-2])
        prev_day_low = float(daily["Low"].iloc[-2])

        today_high = None
        today_low = None
        premarket_high = None
        premarket_low = None
        today_volume = None

        if not intraday.empty:
            intraday = intraday.dropna().copy()

            times = pd.Series(intraday.index.time, index=intraday.index)

            regular_start = time(9, 30)
            regular_end = time(16, 0)

            regular_mask = (times >= regular_start) & (times <= regular_end)
            premarket_mask = times < regular_start

            regular_df = intraday.loc[regular_mask]
            premarket_df = intraday.loc[premarket_mask]

            if not regular_df.empty:
                today_high = float(regular_df["High"].max())
                today_low = float(regular_df["Low"].min())
                today_volume = float(regular_df["Volume"].sum())
                price = float(regular_df["Close"].iloc[-1])
                pct_change = ((price - prev_close) / prev_close) * 100 if prev_close else 0

            if not premarket_df.empty:
                premarket_high = float(premarket_df["High"].max())
                premarket_low = float(premarket_df["Low"].min())

        embed = discord.Embed(
            title=f"{symbol} Analysis",
            description="Live market snapshot",
        )

        embed.add_field(name="Price", value=fmt_price(price), inline=True)
        embed.add_field(name="Change", value=fmt_pct(pct_change), inline=True)
        embed.add_field(name="Trend", value=trend, inline=False)

        embed.add_field(name="20 MA", value=fmt_price(ma20), inline=True)
        embed.add_field(name="50 MA", value=fmt_price(ma50), inline=True)
        embed.add_field(name="Volume", value=fmt_volume(today_volume), inline=True)

        embed.add_field(name="Today High", value=fmt_price(today_high), inline=True)
        embed.add_field(name="Today Low", value=fmt_price(today_low), inline=True)
        embed.add_field(name="Prev Close", value=fmt_price(prev_close), inline=True)

        embed.add_field(name="Premarket High", value=fmt_price(premarket_high), inline=True)
        embed.add_field(name="Premarket Low", value=fmt_price(premarket_low), inline=True)
        embed.add_field(name="Prev Day High", value=fmt_price(prev_day_high), inline=True)

        embed.add_field(name="Prev Day Low", value=fmt_price(prev_day_low), inline=True)

        await interaction.followup.send(embed=embed)

    except Exception as e:
        await interaction.followup.send(f"Error analyzing {symbol}: {e}")


bot.run(TOKEN)