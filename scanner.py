import asyncio
import time
from datetime import datetime, timezone, timedelta
import yfinance as yf

class Scanner:
    def __init__(self, bot, universe, channel_id=None, move_threshold=0.03, check_interval=60):
        self.bot = bot
        self.universe = universe
        self.channel_id = channel_id
        self.move_threshold = move_threshold  # 3% by default
        self.check_interval = check_interval
        self._task = None

    async def start(self):
        self._task = asyncio.create_task(self._run())

    async def _run(self):
        await self.bot.wait_until_ready()
        channel = None
        if self.channel_id:
            channel = self.bot.get_channel(self.channel_id)
        while True:
            try:
                await self.scan_once(channel)
            except Exception as e:
                print(f"Scanner error: {e}")
            await asyncio.sleep(self.check_interval)

    async def scan_once(self, channel):
        # Fetch intraday change for universe tickers
        tickers = self.universe or []
        if not tickers:
            return
        # For efficiency, sample a subset
        subset = tickers[:200]  # adjust as needed
        # Use yfinance for quick intraday change
        data = yf.download(subset, period="1d", interval="1m", group_by="ticker", threads=False, auto_adjust=True)
        now = datetime.utcnow().replace(tzinfo=timezone.utc)

        alerts = []
        for t in subset:
            try:
                # Access last available close and first intraday point
                if t in data:
                    series = data[t]
                else:
                    # in some shapes, data is a dict
                    series = data.get(t)
                if series is None or series.empty:
                    continue
                last_row = series.iloc[-1]
                price = float(last_row["Close"])
                # Quick naive check: compare to 1h ago price, if available
                if len(series) >= 2:
                    prev = float(series.iloc[-2]["Close"])
                    change = (price - prev) / prev
                    if abs(change) >= self.move_threshold:
                        alerts.append((t, price, change))

            except Exception:
                continue

        # Emit alerts
        if alerts:
            text = "\n".join([f"{t}: ${p:.2f} ({c:+.2%})" for t, p, c in alerts[:10]])
            msg = f"Top movers: \n{text}"
            if channel:
                await channel.send(msg)
            else:
                print(msg)

