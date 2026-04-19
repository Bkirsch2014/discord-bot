import os
from datetime import datetime, timedelta, time, timezone
from zoneinfo import ZoneInfo

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv


from news_service import get_ranked_news
from scanner import MarketScanner

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

SCANNER_CHANNEL_ID_RAW = os.getenv("SCANNER_CHANNEL_ID")
SCANNER_CHANNEL_ID = int(SCANNER_CHANNEL_ID_RAW) if SCANNER_CHANNEL_ID_RAW else None

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
scanner_task_started = False 

# ---------------------------
# Helpers
# ---------------------------
def fmt_price(value):
    return f"${value:.2f}" if value is not None else "N/A"


def fmt_pct(value):
    return f"{value:+.2f}%" if value is not None else "N/A"


def fmt_volume(value):
    return f"{int(value):,}" if value is not None else "N/A"




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


    global scanner_task_started
    if not scanner_task_started and SCANNER_CHANNEL_ID:
        scanner = MarketScanner(bot, SCANNER_CHANNEL_ID)
        bot.loop.create_task(scanner.run_forever())
        scanner_task_started = True
        print("Scanner started.")

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
        articles = await get_ranked_news(symbol, top_n=5)

        if not articles:
            await interaction.followup.send(f"No news found for {symbol}.")
            return

        embed = discord.Embed(
            title=f"{symbol} News",
            description="Top 5 most relevant headlines",
        )

        for article in articles:
            title = article.get("title", "Untitled article")
            source = article.get("source", "Unknown source")
            url = article.get("url", "")

            value = f"**Source:** {source}"
            if url:
                value += f"\n[Read article]({url})"

            embed.add_field(
                name=title[:256],
                value=value[:1024],
                inline=False
            )

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

        # Daily bars for SMA 20, EMA 200, previous day high/low
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

        # SMA 20
        sma20 = sum(closes[-20:]) / 20

        # EMA 200
        ema200 = closes[0]
        multiplier = 2 / (200 + 1)
        for close in closes[1:]:
            ema200 = (close - ema200) * multiplier + ema200

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

        # Trend using SMA 20 and EMA 200
        if live_price > sma20 and live_price > ema200:
            trend_title = "Bullish"
            trend_detail = "Above SMA 20 & EMA 200"
        elif live_price < sma20 and live_price < ema200:
            trend_title = "Bearish"
            trend_detail = "Below SMA 20 & EMA 200"
        elif live_price > sma20 and live_price < ema200:
            trend_title = "Mixed"
            trend_detail = "Above SMA 20, below EMA 200"
        else:
            trend_title = "Mixed"
            trend_detail = "Below SMA 20, above EMA 200"

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
                bias_header = f"Strength above {fmt_price(prev_day_high)} (Previous Day High)"
            else:
                bias_header = f"Constructive above EMA 200, watching {fmt_price(prev_day_high)} reclaim"
            if today_low is not None:
                bias_lines.append(f"• Loss of {fmt_price(today_low)} weakens structure")
                
        elif trend_title == "Bearish":
            if live_price < prev_day_low:
                bias_header = f"Weak below {fmt_price(prev_day_low)} (Previous Day Low)"
            else:
                bias_header = f"Under pressure, watching {fmt_price(prev_day_low)} support"
            if today_low is not None:
                bias_lines.append(f"• Below {fmt_price(today_low)} may extend weakness")
            if premarket_low is not None:
                bias_lines.append(f"• Staying under {fmt_price(premarket_low)} keeps pressure on")
            if today_high is not None:
                bias_lines.append(f"• Recovery above {fmt_price(today_high)} improves tone")
                
        else:
            bias_header = "Mixed / range-bound until key levels break"
            
            if today_high is not None:
                bias_lines.append(f"• Above {fmt_price(today_high)} may improve trend")
            if today_low is not None:
                bias_lines.append(f"• Below {fmt_price(today_low)} may weaken trend")
                
            if premarket_high is not None and premarket_low is not None:
                bias_lines.append(
                    f"• Premarket range: {fmt_price(premarket_low)} - {fmt_price(premarket_high)}"
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

        embed.add_field(name="SMA 20", value=fmt_price(sma20), inline=True)
        embed.add_field(name="EMA 200", value=fmt_price(ema200), inline=True)
        embed.add_field(name="Previous Close", value=fmt_price(prev_close), inline=True)

        embed.add_field(name="Levels", value=levels_text, inline=False)
        embed.add_field(name="Bias", value=bias_text[:1024], inline=False)

        await interaction.followup.send(embed=embed)

    except Exception as e:
        print(f"ANALYZE ERROR: {e}")
        await interaction.followup.send(f"Error analyzing {symbol}: {e}")
        
bot.run(TOKEN)