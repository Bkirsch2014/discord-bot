import os
from datetime import datetime, timedelta, time, timezone
from zoneinfo import ZoneInfo

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
import yfinance as yf

from alpaca.data.enums import DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import (
    StockBarsRequest,
    StockLatestTradeRequest,
    StockSnapshotRequest,
)
from alpaca.data.timeframe import TimeFrame

load_dotenv()

# ---------------------------
# Environment variables
# ---------------------------
TOKEN = os.getenv("DISCORD_TOKEN")
NEWS_API_KEY = os.getenv("ALPHA_VANTAGE_KEY")
GUILD_ID_RAW = os.getenv("GUILD_ID")

ALPACA_API_KEY = os.getenv("ALPACA_API_KEY")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")
ALPACA_FEED_RAW = os.getenv("ALPACA_FEED", "IEX").upper()

if not TOKEN:
    raise ValueError("Missing DISCORD_TOKEN")
if not GUILD_ID_RAW:
    raise ValueError("Missing GUILD_ID")
if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
    raise ValueError("Missing ALPACA_API_KEY or ALPACA_SECRET_KEY")

GUILD_ID = int(GUILD_ID_RAW)

feed_map = {
    "IEX": DataFeed.IEX,
    "SIP": DataFeed.SIP,
    "DELAYED_SIP": DataFeed.DELAYED_SIP,
}
ALPACA_FEED = feed_map.get(ALPACA_FEED_RAW, DataFeed.IEX)

data_client = StockHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)

# ---------------------------
# Discord bot setup
# ---------------------------
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)


# ---------------------------
# Helpers
# ---------------------------
def fmt_price(value):
    return f"${value:.2f}" if value is not None else "N/A"


def fmt_pct(value):
    return f"{value:+.2f}%" if value is not None else "N/A"


def fmt_volume(value):
    return f"{int(value):,}" if value is not None else "N/A"


def get_trend(price: float, ma20: float, ma50: float):
    if price > ma20 and price > ma50:
        return "Bullish", "Above 20 & 50 MA"
    if price < ma20 and price < ma50:
        return "Bearish", "Below 20 & 50 MA"
    if price > ma20 and price < ma50:
        return "Mixed", "Above 20, below 50 MA"
    return "Mixed", "Below 20, above 50 MA"


# ---------------------------
# Events
# ---------------------------
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


# ---------------------------
# /help
# ---------------------------
@bot.tree.command(name="help", description="Show bot commands", guild=discord.Object(id=GUILD_ID))
async def help_command(interaction: discord.Interaction):
    embed = discord.Embed(
        title="StockBuddyBot Commands",
        description="Available commands",
    )
    embed.add_field(
        name="/analyze [ticker]",
        value="Live market snapshot with price, trend, levels, bias, and volume.",
        inline=False
    )
    embed.add_field(
        name="/news [ticker]",
        value="Latest stock headlines.",
        inline=False
    )
    embed.add_field(
        name="/help",
        value="Show this command list.",
        inline=False
    )
    embed.set_footer(text="Example: /analyze NVDA")
    await interaction.response.send_message(embed=embed)


# ---------------------------
# /news
# ---------------------------
@bot.tree.command(name="news", description="Get stock news", guild=discord.Object(id=GUILD_ID))
@app_commands.describe(symbol="Stock ticker")
async def news(interaction: discord.Interaction, symbol: str):
    await interaction.response.defer()

    symbol = symbol.upper().strip()

    try:
        ticker = yf.Ticker(symbol)
        articles = ticker.news

        if not articles:
            await interaction.followup.send(f"No news found for {symbol}.")
            return

        embed = discord.Embed(
            title=f"{symbol} News",
            description="Latest headlines",
        )

        added = 0

        for article in articles:
            title = article.get("title")
            link = article.get("link") or article.get("url")
            publisher = article.get("publisher")

            if not title and isinstance(article.get("content"), dict):
                content = article["content"]
                title = content.get("title")
                if not link:
                    canonical = content.get("canonicalUrl")
                    if isinstance(canonical, dict):
                        link = canonical.get("url")
                if not publisher:
                    provider = content.get("provider")
                    if isinstance(provider, dict):
                        publisher = provider.get("displayName")

            if not title and isinstance(article.get("headline"), str):
                title = article.get("headline")

            if not publisher:
                publisher = "Unknown source"

            if not title:
                title = "Untitled article"

            value = f"**Source:** {publisher}"
            if link:
                value += f"\n[Read article]({link})"

            embed.add_field(
                name=title[:256],
                value=value[:1024],
                inline=False
            )

            added += 1
            if added == 3:
                break

        if added == 0:
            await interaction.followup.send(f"No usable news found for {symbol}.")
            return

        await interaction.followup.send(embed=embed)

    except Exception as e:
        print(f"NEWS ERROR: {e}")
        await interaction.followup.send(f"Error fetching news for {symbol}: {e}")
# ---------------------------
# /analyze
# ---------------------------
@bot.tree.command(name="analyze", description="Live market data snapshot", guild=discord.Object(id=GUILD_ID))
@app_commands.describe(symbol="Stock ticker")
async def analyze(interaction: discord.Interaction, symbol: str):
    await interaction.response.defer()

    symbol = symbol.upper().strip()
    print(f"Running analyze for {symbol}")

    ny_tz = ZoneInfo("America/New_York")
    now_utc = datetime.now(timezone.utc)
    now_ny = now_utc.astimezone(ny_tz)

    try:
        # Latest live trade
        latest_trade_req = StockLatestTradeRequest(
            symbol_or_symbols=[symbol],
            feed=ALPACA_FEED
        )
        latest_trade_resp = data_client.get_stock_latest_trade(latest_trade_req)

        if symbol not in latest_trade_resp:
            await interaction.followup.send(f"No latest trade data found for {symbol}.")
            return

        latest_trade = latest_trade_resp[symbol]
        live_price = float(latest_trade.price)

        # Snapshot
        snapshot_req = StockSnapshotRequest(
            symbol_or_symbols=[symbol],
            feed=ALPACA_FEED
        )
        snapshot_resp = data_client.get_stock_snapshot(snapshot_req)

        if symbol not in snapshot_resp:
            await interaction.followup.send(f"No snapshot data found for {symbol}.")
            return

        snapshot = snapshot_resp[symbol]
        prev_daily_bar = snapshot.previous_daily_bar
        daily_bar = snapshot.daily_bar

        prev_close = float(prev_daily_bar.close) if prev_daily_bar else None
        today_high = float(daily_bar.high) if daily_bar else None
        today_low = float(daily_bar.low) if daily_bar else None
        today_volume = float(daily_bar.volume) if daily_bar else None

        pct_change = None
        if prev_close:
            pct_change = ((live_price - prev_close) / prev_close) * 100

        # Daily bars for EMA 20, SMA 200, previous day high/low
        daily_start = (now_ny - timedelta(days=300)).replace(
            hour=0, minute=0, second=0, microsecond=0
        ).astimezone(timezone.utc)

        daily_req = StockBarsRequest(
            symbol_or_symbols=[symbol],
            timeframe=TimeFrame.Day,
            start=daily_start,
            end=now_utc,
            feed=ALPACA_FEED
        )
        daily_bars_resp = data_client.get_stock_bars(daily_req)

        if symbol not in daily_bars_resp.data:
            await interaction.followup.send(f"No daily bar data found for {symbol}.")
            return

        daily_bars = daily_bars_resp.data[symbol]

        if len(daily_bars) < 200:
            await interaction.followup.send(f"Not enough daily data to analyze {symbol}.")
            return

        closes = [float(bar.close) for bar in daily_bars]

        # EMA 20
        ema20 = closes[0]
        multiplier = 2 / (20 + 1)
        for close in closes[1:]:
            ema20 = (close - ema20) * multiplier + ema20

        # SMA 200
        sma200 = sum(closes[-200:]) / 200

        prev_day_high = float(daily_bars[-2].high)
        prev_day_low = float(daily_bars[-2].low)
        avg_volume_20 = sum(float(bar.volume) for bar in daily_bars[-20:]) / 20

        # Premarket bars
        if now_ny.time() < time(4, 0):
            session_start_ny = (now_ny - timedelta(days=1)).replace(
                hour=4, minute=0, second=0, microsecond=0
            )
        else:
            session_start_ny = now_ny.replace(
                hour=4, minute=0, second=0, microsecond=0
            )

        bars_req = StockBarsRequest(
            symbol_or_symbols=[symbol],
            timeframe=TimeFrame.Minute,
            start=session_start_ny.astimezone(timezone.utc),
            end=now_utc,
            feed=ALPACA_FEED
        )
        minute_bars_resp = data_client.get_stock_bars(bars_req)
        minute_bars = minute_bars_resp.data.get(symbol, [])

        premarket_high = None
        premarket_low = None
        pm_highs = []
        pm_lows = []

        for bar in minute_bars:
            ts_ny = bar.timestamp.astimezone(ny_tz)
            if time(4, 0) <= ts_ny.time() < time(9, 30):
                pm_highs.append(float(bar.high))
                pm_lows.append(float(bar.low))

        if pm_highs:
            premarket_high = max(pm_highs)
            premarket_low = min(pm_lows)

        # Trend using EMA 20 and SMA 200
        if live_price > ema20 and live_price > sma200:
            trend_title = "Bullish"
            trend_detail = "Above EMA 20 & SMA 200"
        elif live_price < ema20 and live_price < sma200:
            trend_title = "Bearish"
            trend_detail = "Below EMA 20 & SMA 200"
        elif live_price > ema20 and live_price < sma200:
            trend_title = "Mixed"
            trend_detail = "Above EMA 20, below SMA 200"
        else:
            trend_title = "Mixed"
            trend_detail = "Below EMA 20, above SMA 200"

        # Volume context
        volume_text = "N/A"
        if today_volume is not None:
            rel_vol = today_volume / avg_volume_20 if avg_volume_20 else 0
            if rel_vol >= 1.5:
                vol_context = "High volume"
            elif rel_vol >= 1.0:
                vol_context = "Normal volume"
            else:
                vol_context = "Light volume"
            volume_text = f"{fmt_volume(today_volume)} ({vol_context})"

        # Bias
        bias_lines = []

        if trend_title == "Bullish":
            if live_price > prev_day_high:
                bias_header = f"Bullish above {fmt_price(prev_day_high)} (Previous Day High)"
            else:
                bias_header = f"Bullish, but needs reclaim of {fmt_price(prev_day_high)}"

            if today_high is not None:
                bias_lines.append(f"→ Watch continuation above {fmt_price(today_high)}")
            if premarket_high is not None:
                bias_lines.append(f"→ Stronger if holding above {fmt_price(premarket_high)}")
            if today_low is not None:
                bias_lines.append(f"→ Weakens below {fmt_price(today_low)}")

        elif trend_title == "Bearish":
            if live_price < prev_day_low:
                bias_header = f"Bearish below {fmt_price(prev_day_low)} (Previous Day Low)"
            else:
                bias_header = f"Bearish, but needs loss of {fmt_price(prev_day_low)}"

            if today_low is not None:
                bias_lines.append(f"→ Watch continuation below {fmt_price(today_low)}")
            if premarket_low is not None:
                bias_lines.append(f"→ Stronger if staying below {fmt_price(premarket_low)}")
            if today_high is not None:
                bias_lines.append(f"→ Weakens above {fmt_price(today_high)}")

        else:
            bias_header = "Range / mixed until key levels break"
            if today_high is not None and today_low is not None:
                bias_lines.append(f"→ Above {fmt_price(today_high)} could trend")
                bias_lines.append(f"→ Below {fmt_price(today_low)} could break down")
            if premarket_high is not None and premarket_low is not None:
                bias_lines.append(
                    f"→ Watch premarket range {fmt_price(premarket_low)} - {fmt_price(premarket_high)}"
                )

        bias_text = bias_header
        if bias_lines:
            bias_text += "\n" + "\n".join(bias_lines)

        levels_text = (
            f"**Today High:** {fmt_price(today_high)}\n"
            f"**Today Low:** {fmt_price(today_low)}\n"
            f"**Premarket High:** {fmt_price(premarket_high)}\n"
            f"**Premarket Low:** {fmt_price(premarket_low)}\n"
            f"**Previous Day High:** {fmt_price(prev_day_high)}\n"
            f"**Previous Day Low:** {fmt_price(prev_day_low)}"
        )

        embed = discord.Embed(
            title=f"{symbol} Analysis",
            description="Reference only — not for entering a trade."
        )

        embed.add_field(name="Price", value=fmt_price(live_price), inline=True)
        embed.add_field(name="Change", value=fmt_pct(pct_change), inline=True)
        embed.add_field(name="Volume", value=volume_text, inline=True)

        embed.add_field(
            name="Trend",
            value=f"**{trend_title}**\n{trend_detail}",
            inline=False
        )

        embed.add_field(name="EMA 20", value=fmt_price(ema20), inline=True)
        embed.add_field(name="SMA 200", value=fmt_price(sma200), inline=True)
        embed.add_field(name="Previous Close", value=fmt_price(prev_close), inline=True)

        embed.add_field(name="Levels", value=levels_text, inline=False)
        embed.add_field(name="Bias", value=bias_text[:1024], inline=False)

        await interaction.followup.send(embed=embed)

    except Exception as e:
        print(f"ANALYZE ERROR: {e}")
        await interaction.followup.send(f"Error analyzing {symbol}: {e}")
bot.run(TOKEN)
