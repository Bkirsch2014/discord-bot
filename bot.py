import os
from datetime import datetime, timedelta, time, timezone
from zoneinfo import ZoneInfo

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

from alpaca.data.enums import DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import (
    StockBarsRequest,
    StockLatestTradeRequest,
    StockSnapshotRequest,
)
from alpaca.data.timeframe import TimeFrame

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
API_KEY = os.getenv("ALPHA_VANTAGE_KEY")
guild_id_raw = os.getenv("GUILD_ID")

ALPACA_API_KEY = os.getenv("ALPACA_API_KEY")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")
ALPACA_FEED_RAW = os.getenv("ALPACA_FEED", "IEX").upper()

if not TOKEN:
    raise ValueError("Missing DISCORD_TOKEN environment variable")
if not guild_id_raw:
    raise ValueError("Missing GUILD_ID environment variable")
if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
    raise ValueError("Missing Alpaca API credentials")

GUILD_ID = int(guild_id_raw)

feed_map = {
    "IEX": DataFeed.IEX,
    "SIP": DataFeed.SIP,
    "DELAYED_SIP": DataFeed.DELAYED_SIP,
}
ALPACA_FEED = feed_map.get(ALPACA_FEED_RAW, DataFeed.IEX)

data_client = StockHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)


def fmt_price(value):
    return f"${value:.2f}" if value is not None else "N/A"


def fmt_pct(value):
    return f"{value:+.2f}%" if value is not None else "N/A"


def fmt_volume(value):
    return f"{int(value):,}" if value is not None else "N/A"


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


@bot.tree.command(name="help", description="Show bot commands", guild=discord.Object(id=GUILD_ID))
async def help_command(interaction: discord.Interaction):
    embed = discord.Embed(
        title="StockBuddyBot Commands",
        description="Available commands",
    )

    embed.add_field(
        name="/analyze [ticker]",
        value="Live market snapshot with trend, levels, bias, and volume.",
        inline=False
    )
    embed.add_field(
        name="/news [ticker]",
        value="Latest news headlines for a ticker.",
        inline=False
    )
    embed.add_field(
        name="/help",
        value="Show this command list.",
        inline=False
    )

    embed.set_footer(text="Example: /analyze NVDA")
    await interaction.response.send_message(embed=embed)


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

        for article in articles:
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


@bot.tree.command(name="analyze", description="Live market data snapshot", guild=discord.Object(id=GUILD_ID))
@app_commands.describe(symbol="Stock ticker")
async def analyze(interaction: discord.Interaction, symbol: str):
    await interaction.response.defer()

    symbol = symbol.upper().strip()
    ny_tz = ZoneInfo("America/New_York")
    now_utc = datetime.now(timezone.utc)
    now_ny = now_utc.astimezone(ny_tz)

    try:
        latest_trade_req = StockLatestTradeRequest(
            symbol_or_symbols=symbol,
            feed=ALPACA_FEED
        )
        latest_trade_resp = data_client.get_stock_latest_trade(latest_trade_req)
        latest_trade = latest_trade_resp[symbol]
        live_price = float(latest_trade.price)

        snapshot_req = StockSnapshotRequest(
            symbol_or_symbols=symbol,
            feed=ALPACA_FEED
        )
        snapshot_resp = data_client.get_stock_snapshot(snapshot_req)
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

        daily_start = (now_ny - timedelta(days=90)).replace(
            hour=0, minute=0, second=0, microsecond=0
        ).astimezone(timezone.utc)

        daily_req = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=TimeFrame.Day,
            start=daily_start,
            end=now_utc,
            feed=ALPACA_FEED
        )
        daily_bars_resp = data_client.get_stock_bars(daily_req)
        daily_bars = daily_bars_resp[symbol]

        if len(daily_bars) < 50:
            await interaction.followup.send(f"Not enough daily data to analyze {symbol}.")
            return

        closes = [float(bar.close) for bar in daily_bars]
        ma20 = sum(closes[-20:]) / 20
        ma50 = sum(closes[-50:]) / 50

        prev_day_high = float(daily_bars[-2].high)
        prev_day_low = float(daily_bars[-2].low)

        avg_volume_20 = sum(float(bar.volume) for bar in daily_bars[-20:]) / 20

        if now_ny.time() < time(4, 0):
    session_start_ny = (now_ny - timedelta(days=1)).replace(
        hour=4, minute=0, second=0, microsecond=0
    )
else:
    session_start_ny = now_ny.replace(
        hour=4, minute=0, second=0, microsecond=0
    )

bars_req = StockBarsRequest(
    symbol_or_symbols=symbol,
    timeframe=TimeFrame.Minute,
    start=session_start_ny.astimezone(timezone.utc),
    end=now_utc,
    feed=ALPACA_FEED
)
        minute_bars_resp = data_client.get_stock_bars(bars_req)
        minute_bars = minute_bars_resp[symbol]

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

        if live_price > ma20 and live_price > ma50:
            trend_title = "Bullish"
            trend_detail = "Above 20 & 50 MA"
        elif live_price < ma20 and live_price < ma50:
            trend_title = "Bearish"
            trend_detail = "Below 20 & 50 MA"
        elif live_price > ma20 and live_price < ma50:
            trend_title = "Mixed"
            trend_detail = "Above 20, below 50 MA"
        else:
            trend_title = "Mixed"
            trend_detail = "Below 20, above 50 MA"

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

        bias_lines = []

        if trend_title == "Bullish":
            if live_price > prev_day_high:
                bias_header = f"Bullish above {fmt_price(prev_day_high)} (PD High)"
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
                bias_header = f"Bearish below {fmt_price(prev_day_low)} (PD Low)"
            else:
                bias_header = f"Bearish, but needs loss of {fmt_price(prev_day_low)}"

            if today_low is not None:
                bias_lines.append(f"→ Watch continuation below {fmt_price(today_low)}")
            if premarket_low is not None:
                bias_lines.append(f"→ Stronger if staying below {fmt_price(premarket_low)}")
            if today_high is not None:
                bias_lines.append(f"→ Weakens above {fmt_price(today_high)}")

        else:
            bias_header = "Choppy / range until key levels break"
            if today_high is not None and today_low is not None:
                bias_lines.append(f"→ Above {fmt_price(today_high)} could trend")
                bias_lines.append(f"→ Below {fmt_price(today_low)} could break down")
            if premarket_high is not None and premarket_low is not None:
                bias_lines.append(
                    f"→ Watch PM range {fmt_price(premarket_low)} - {fmt_price(premarket_high)}"
                )

        bias_text = bias_header
        if bias_lines:
            bias_text += "\n" + "\n".join(bias_lines)

        levels_text = (
            f"**HOD:** {fmt_price(today_high)}\n"
            f"**LOD:** {fmt_price(today_low)}\n"
            f"**PM High:** {fmt_price(premarket_high)}\n"
            f"**PM Low:** {fmt_price(premarket_low)}\n"
            f"**PD High:** {fmt_price(prev_day_high)}\n"
            f"**PD Low:** {fmt_price(prev_day_low)}"
        )

        embed = discord.Embed(
            title=f"{symbol} Analysis",
            description=f"Live Alpaca snapshot ({ALPACA_FEED_RAW})"
        )

        embed.add_field(name="Price", value=fmt_price(live_price), inline=True)
        embed.add_field(name="Change", value=fmt_pct(pct_change), inline=True)
        embed.add_field(name="Volume", value=volume_text, inline=True)

        embed.add_field(
            name="Trend",
            value=f"**{trend_title}**\n{trend_detail}",
            inline=False
        )

        embed.add_field(name="20 MA", value=fmt_price(ma20), inline=True)
        embed.add_field(name="50 MA", value=fmt_price(ma50), inline=True)
        embed.add_field(name="Prev Close", value=fmt_price(prev_close), inline=True)

        embed.add_field(name="Levels", value=levels_text, inline=False)
        embed.add_field(name="Bias", value=bias_text[:1024], inline=False)

        await interaction.followup.send(embed=embed)

    except Exception as e:
        await interaction.followup.send(f"Error analyzing {symbol}: {e}")


bot.run(TOKEN)