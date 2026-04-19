import asyncio
import os
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import discord
from alpaca.data.requests import StockSnapshotRequest
from alpaca.data.enums import DataFeed
from alpaca.data.historical import StockHistoricalDataClient

from universe import fetch_us_symbols_from_finnhub, build_top_liquid_universe


class ScannerState:
    def __init__(self):
        self.last_alerts = {}  # (symbol, condition) -> timestamp
        self.universe = []
        self.last_universe_refresh = 0


class MarketScanner:
    def __init__(self, bot: discord.Client, channel_id: int):
        self.bot = bot
        self.channel_id = channel_id
        self.state = ScannerState()

        alpaca_key = os.getenv("ALPACA_API_KEY")
        alpaca_secret = os.getenv("ALPACA_SECRET_KEY")
        feed_raw = os.getenv("ALPACA_FEED", "IEX").upper()

        feed_map = {
            "IEX": DataFeed.IEX,
            "SIP": DataFeed.SIP,
            "DELAYED_SIP": DataFeed.DELAYED_SIP,
        }
        self.feed = feed_map.get(feed_raw, DataFeed.IEX)
        self.data_client = StockHistoricalDataClient(alpaca_key, alpaca_secret)

    def _in_market_window(self) -> bool:
        now_ny = datetime.now(timezone.utc).astimezone(ZoneInfo("America/New_York"))
        if now_ny.weekday() >= 5:
            return False
        return now_ny.hour >= 9 and (now_ny.hour < 16 or (now_ny.hour == 16 and now_ny.minute == 0))

    def _cooldown_ok(self, symbol: str, condition: str, cooldown_seconds: int = 1800) -> bool:
        key = (symbol, condition)
        last_ts = self.state.last_alerts.get(key, 0)
        return (time.time() - last_ts) >= cooldown_seconds

    def _mark_alert(self, symbol: str, condition: str):
        self.state.last_alerts[(symbol, condition)] = time.time()

    async def refresh_universe(self):
        if time.time() - self.state.last_universe_refresh < 60 * 60:
            return

        symbols = await fetch_us_symbols_from_finnhub()
        if not symbols:
            return

        self.state.universe = build_top_liquid_universe(
            self.data_client,
            self.feed,
            symbols,
            top_n=1000
        )
        self.state.last_universe_refresh = time.time()
        print(f"Scanner universe size: {len(self.state.universe)}")

    async def scan_once(self):
        if not self._in_market_window():
            return

        await self.refresh_universe()

        if not self.state.universe:
            return

        channel = self.bot.get_channel(self.channel_id)
        if not channel:
            return

        batch_size = 200
        for i in range(0, len(self.state.universe), batch_size):
            batch = self.state.universe[i:i + batch_size]
            req = StockSnapshotRequest(symbol_or_symbols=batch, feed=self.feed)
            resp = self.data_client.get_stock_snapshot(req)

            for symbol in batch:
                snap = resp.get(symbol)
                if not snap or not snap.latest_trade or not snap.daily_bar or not snap.previous_daily_bar:
                    continue

                price = float(snap.latest_trade.price)
                today_high = float(snap.daily_bar.high)
                today_low = float(snap.daily_bar.low)
                today_volume = float(snap.daily_bar.volume or 0)
                prev_high = float(snap.previous_daily_bar.high)
                prev_low = float(snap.previous_daily_bar.low)
                prev_close = float(snap.previous_daily_bar.close or 0)

                pct_change = ((price - prev_close) / prev_close) * 100 if prev_close else 0

                # Previous day high break
                if price > prev_high and self._cooldown_ok(symbol, "pd_high_break"):
                    await channel.send(
                        f"🚨 **{symbol}** broke **Previous Day High**\n"
                        f"Price: **${price:.2f}** | Previous Day High: **${prev_high:.2f}** | Change: **{pct_change:+.2f}%**"
                    )
                    self._mark_alert(symbol, "pd_high_break")

                # Previous day low break
                if price < prev_low and self._cooldown_ok(symbol, "pd_low_break"):
                    await channel.send(
                        f"🚨 **{symbol}** broke **Previous Day Low**\n"
                        f"Price: **${price:.2f}** | Previous Day Low: **${prev_low:.2f}** | Change: **{pct_change:+.2f}%**"
                    )
                    self._mark_alert(symbol, "pd_low_break")

                # High-volume strength event
                if pct_change > 2 and today_volume > 500_000 and price >= today_high * 0.995:
                    if self._cooldown_ok(symbol, "strength_event", cooldown_seconds=3600):
                        await channel.send(
                            f"🔥 **{symbol}** showing strength near **Today High**\n"
                            f"Price: **${price:.2f}** | Today High: **${today_high:.2f}** | Change: **{pct_change:+.2f}%** | Volume: **{int(today_volume):,}**"
                        )
                        self._mark_alert(symbol, "strength_event")

            await asyncio.sleep(1.0)

    async def run_forever(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            try:
                await self.scan_once()
            except Exception as e:
                print(f"SCANNER ERROR: {e}")
            await asyncio.sleep(20)