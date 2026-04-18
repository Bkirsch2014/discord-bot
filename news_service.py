import asyncio
import time
import requests
import yfinance as yf
from datetime import datetime, timezone, timedelta
import os

class NewsService:
    def __init__(self):
        self.finnhub_key = os.getenv("FINNHUB_API_KEY") or None
        self.alpaca_key = os.getenv("ALPACA_API_KEY") or None
        self.alpaca_secret = os.getenv("ALPACA_SECRET_KEY") or None

    async def get_news(self, ticker: str):
        # Gather sources asynchronously if you wish; here we do simple sequential fetches
        results = []

        # Yahoo Finance via yfinance (top headlines)
        try:
            ticker_obj = yf.Ticker(ticker)
            articles = ticker_obj.news
            if articles:
                for a in articles[:3]:
                    results.append({
                        "title": a.get("title") or a.get("headline") or "No title",
                        "url": a.get("link") or a.get("url") or "",
                        "source": a.get("source") or "Yahoo"
                    })
        except Exception:
            pass

        # Finnhub News API
        if self.finnhub_key:
            try:
                from urllib.parse import quote_plus
                url = f"https://finnhub.io/api/v1/company-news?symbol={ticker}&from={datetime.utcnow().date()}&to={datetime.utcnow().date()}&token={self.finnhub_key}"
                resp = requests.get(url, timeout=8)
                if resp.status_code == 200:
                    data = resp.json()
                    for item in data[:2]:
                        results.append({
                            "title": item.get("headline") or item.get("summary") or "News",
                            "url": item.get("url") or "",
                            "source": item.get("source") or "Finnhub"
                        })
            except Exception:
                pass

        # Alpaca Company News (fallback)
        if self.alpaca_key and self.alpaca_secret:
            try:
                url = f"https://paper-api.alpaca.markets/v2/news?ticker={ticker}"
                headers = {
                    "APCA-API-KEY-ID": self.alpaca_key,
                    "APCA-API-SECRET-KEY": self.alpaca_secret
                }
                resp = requests.get(url, headers=headers, timeout=8)
                if resp.status_code == 200:
                    data = resp.json()
                    for item in data.get("news", [])[:2]:
                        results.append({
                            "title": item.get("headline") or "News",
                            "url": item.get("url") or "",
                            "source": item.get("source") or "Alpaca"
                        })
            except Exception:
                pass

        # Simple ranking: prefer newer and trusted sources
        # Here we sort by presence of url (prefer) and by a fake "recency" metric.
        results = list({(r["title"], r["url"]): r for r in results}.values())
        results.sort(key=lambda x: (0 if x.get("url") else 1, -len(x.get("title",""))))
        return results[:5]

news_service = NewsService()


