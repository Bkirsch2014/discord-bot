import os
import asyncio
from dotenv import load_dotenv
import discord
from discord import app_commands
from discord.ext import commands

from universe import get_universe
from news_service import NewsService
from scanner import Scanner

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID")) if os.getenv("GUILD_ID") else None
if not TOKEN or not GUILD_ID:
    raise SystemExit("Set DISCORD_TOKEN and GUILD_ID in environment")

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# Initialize shared services
UNIVERSE_TICKERS = get_universe()  # list of tickers
news_service = NewsService()

# A simple channel id for alerts (optional; you can fetch from environment)
ALERT_CHANNEL_ID = int(os.getenv("ALERT_CHANNEL_ID")) if os.getenv("ALERT_CHANNEL_ID") else None

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    try:
        await tree.sync(guild=discord.Object(id=GUILD_ID))
        print("Commands synced")
    except Exception as e:
        print(f"Error syncing commands: {e}")

# /analyze
@tree.command(name="analyze", description="Live market analysis for a ticker", guild=discord.Object(id=GUILD_ID))
async def analyze(interaction: discord.Interaction, ticker: str):
    await interaction.response.defer()
    # Import here to avoid heavy imports at startup
    import alpaca_trade_api as tradeapi  # if you use Alpaca's REST client
    # You can keep your current Alpaca approach or replace with yfinance as needed
    try:
        # Placeholder: you should replace with your actual data retrieval logic
        # For demonstration, we fetch some data via yfinance
        import yfinance as yf
        t = ticker.upper()
        stock = yf.Ticker(t)
        hist = stock.history(period="2d", interval="1d")
        if hist.empty:
            await interaction.followup.send(f"No data for {t}")
            return
        last = hist.tail(1).iloc<a href="" class="citation-link" target="_blank" style="vertical-align: super; font-size: 0.8em; margin-left: 3px;">[0]</a>
        prev = hist.iloc[-2] if len(hist) >= 2 else last
        live_price = float(last["Close"])
        prev_close = float(prev["Close"])
        change = ((live_price - prev_close) / prev_close) * 100

        today_high = float(last["High"])
        today_low = float(last["Low"])
        volume = int(last["Volume"]) if "Volume" in last else None

        # EMA20 / SMA20 from history
        closes = stock.history(period="60d")["Close"]
        if len(closes) >= 20:
            sma20 = closes.rolling(window=20).mean().iloc[-1]
            ema20 = closes.ewm(span=20, adjust=False).mean().iloc[-1]
        else:
            sma20 = ema20 = None

        embed = discord.Embed(title=f"Analysis for {t}", color=0x1f8b4c)
        embed.add_field(name="Price", value=f"${live_price:.2f} ({change:+.2f}%)", inline=False)
        embed.add_field(name="Today High / Low", value=f"${today_high:.2f} / ${today_low:.2f}", inline=True)
        embed.add_field(name="Prev Close", value=f"${prev_close:.2f}", inline=True)
        if ema20 is not None:
            embed.add_field(name="EMA 20", value=f"${ema20:.2f}", inline=True)
        if sma20 is not None:
            embed.add_field(name="SMA 20", value=f"${sma20:.2f}", inline=True)
        if volume is not None:
            embed.add_field(name="Volume (today)", value=f"{volume:,}", inline=True)

        # Simple bias/trend
        bias = "Neutral"
        if live_price > (ema20 or live_price):
            bias = "Bullish"
        elif live_price < (ema20 or live_price):
            bias = "Bearish"

        embed.add_field(name="Bias", value=bias, inline=True)
        embed.add_field(name="Trend", value="Based on EMA/SMA", inline=False)

        await interaction.followup.send(embed=embed)
    except Exception as e:
        await interaction.followup.send(f"Error analyzing {ticker}: {e}")

# /news
@tree.command(name="news", description="Get top 5 stock news")
async def news_cmd(interaction: discord.Interaction, ticker: str):
    await interaction.response.defer()
    t = ticker.upper()
    articles = await news_service.get_news(t)
    embed = discord.Embed(title=f"Top news for {t}", color=0x2f5b92)
    if not articles:
        embed.add_field(name="No articles", value="No news found.", inline=False)
    else:
        for i, a in enumerate(articles[:5], start=1):
            title = a.get("title", "")
            url = a.get("url", "")
            source = a.get("source", "")
            text = f"[{title}]({url})" if url else title
            embed.add_field(name=f"{i}. {source}", value=text, inline=False)
    await interaction.followup.send(embed=embed)

# Start scanner (background task)
scanner = None

async def start_scanner():
    global scanner
    from scanner import Scanner
    scanner = Scanner(bot, UNIVERSE_TICKERS, ALERT_CHANNEL_ID)
    await scanner.start()

if __name__ == "__main__":
    bot.loop.create_task(start_scanner())
    bot.run(TOKEN)
