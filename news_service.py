import os
import re
import time
from datetime import datetime, timezone
from typing import List, Dict, Optional

import aiohttp
import yfinance as yf


HIGH_TRUST_SOURCES = {
    "Reuters",
    "Bloomberg",
    "CNBC",
    "MarketWatch",
    "The Wall Street Journal",
    "Barrons",
    "Associated Press",
    "Yahoo Finance",
    "The Motley Fool",
    "Seeking Alpha",
    "Benzinga",
}


def _now_ts() -> int:
    return int(time.time())


def _safe_lower(text: Optional[str]) -> str:
    return (text or "").strip().lower()


def _normalize_url(url: str) -> str:
    return (url or "").strip()


def _normalize_title(title: str) -> str:
    return re.sub(r"\s+", " ", (title or "").strip())


def _dedupe_key(item: Dict) -> str:
    title = _safe_lower(item.get("title"))
    source = _safe_lower(item.get("source"))
    return f"{title}|{source}"


def _score_article(article: Dict, symbol: str, company_name: Optional[str]) -> float:
    title = _safe_lower(article.get("title"))
    summary = _safe_lower(article.get("summary"))
    source = article.get("source") or "Unknown"
    published_at = article.get("published_at_ts") or 0

    score = 0.0

    if symbol.lower() in title:
        score += 5
    if symbol.lower() in summary:
        score += 2

    if company_name:
        cname = company_name.lower()
        if cname in title:
            score += 4
        if cname in summary:
            score += 1.5

    if source in HIGH_TRUST_SOURCES:
        score += 2

    age_hours = max((_now_ts() - published_at) / 3600, 0) if published_at else 9999
    if age_hours <= 6:
        score += 3
    elif age_hours <= 24:
        score += 2
    elif age_hours <= 72:
        score += 1

    return score


def _parse_yf_timestamp(article: Dict) -> Optional[int]:
    for key in ("providerPublishTime", "published", "published_at"):
        value = article.get(key)
        if isinstance(value, int):
            return value
    content = article.get("content")
    if isinstance(content, dict):
        value = content.get("pubDate")
        if isinstance(value, int):
            return value
    return None


def _extract_yfinance_articles(raw_articles: List[Dict]) -> List[Dict]:
    articles = []

    for article in raw_articles or []:
        title = article.get("title")
        link = article.get("link") or article.get("url")
        source = article.get("publisher")

        content = article.get("content")
        if isinstance(content, dict):
            title = title or content.get("title")
            if not link:
                canonical = content.get("canonicalUrl")
                if isinstance(canonical, dict):
                    link = canonical.get("url")
            if not source:
                provider = content.get("provider")
                if isinstance(provider, dict):
                    source = provider.get("displayName")

        if not title and isinstance(article.get("headline"), str):
            title = article["headline"]

        title = _normalize_title(title or "")
        link = _normalize_url(link or "")

        if not title or not link:
            continue

        articles.append({
            "title": title,
            "url": link,
            "source": source or "Yahoo Finance",
            "summary": "",
            "published_at_ts": _parse_yf_timestamp(article),
            "source_type": "yfinance",
        })

    return articles


async def _fetch_finnhub_news(symbol: str, finnhub_key: str) -> List[Dict]:
    url = (
        "https://finnhub.io/api/v1/company-news"
        f"?symbol={symbol}&from={(datetime.now().date()).isoformat()}&to={(datetime.now().date()).isoformat()}&token={finnhub_key}"
    )

    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=30) as response:
            data = await response.json()

    articles = []
    for article in data or []:
        title = _normalize_title(article.get("headline", ""))
        link = _normalize_url(article.get("url", ""))
        if not title or not link:
            continue

        articles.append({
            "title": title,
            "url": link,
            "source": article.get("source", "Finnhub"),
            "summary": article.get("summary", "") or "",
            "published_at_ts": article.get("datetime"),
            "source_type": "finnhub",
        })

    return articles


async def _fetch_alpha_vantage_news(symbol: str, alpha_key: str) -> List[Dict]:
    url = (
        "https://www.alphavantage.co/query"
        f"?function=NEWS_SENTIMENT&tickers={symbol}&limit=10&apikey={alpha_key}"
    )

    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=30) as response:
            data = await response.json()

    articles = []
    for article in data.get("feed", []) or []:
        title = _normalize_title(article.get("title", ""))
        link = _normalize_url(article.get("url", ""))
        if not title or not link:
            continue

        published = None
        raw_time = article.get("time_published")
        if isinstance(raw_time, str) and len(raw_time) >= 15:
            try:
                dt = datetime.strptime(raw_time, "%Y%m%dT%H%M%S")
                published = int(dt.replace(tzinfo=timezone.utc).timestamp())
            except Exception:
                published = None

        articles.append({
            "title": title,
            "url": link,
            "source": article.get("source", "Alpha Vantage"),
            "summary": article.get("summary", "") or "",
            "published_at_ts": published,
            "source_type": "alpha_vantage",
        })

    return articles


def _guess_company_name(symbol: str) -> Optional[str]:
    try:
        info = yf.Ticker(symbol).info
        return info.get("shortName") or info.get("longName")
    except Exception:
        return None


async def get_ranked_news(symbol: str, top_n: int = 5) -> List[Dict]:
    symbol = symbol.upper().strip()

    yf_articles = []
    try:
        yf_articles = _extract_yfinance_articles(yf.Ticker(symbol).news)
    except Exception:
        yf_articles = []

    finnhub_key = os.getenv("FINNHUB_API_KEY")
    av_key = os.getenv("ALPHA_VANTAGE_KEY")

    finnhub_articles = []
    if finnhub_key:
        try:
            finnhub_articles = await _fetch_finnhub_news(symbol, finnhub_key)
        except Exception:
            finnhub_articles = []

    av_articles = []
    if av_key:
        try:
            av_articles = await _fetch_alpha_vantage_news(symbol, av_key)
        except Exception:
            av_articles = []

    company_name = _guess_company_name(symbol)

    combined = yf_articles + finnhub_articles + av_articles

    seen = set()
    deduped = []
    for item in combined:
        key = _dedupe_key(item)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)

    for item in deduped:
        item["score"] = _score_article(item, symbol, company_name)

    deduped.sort(
        key=lambda x: (
            x.get("score", 0),
            x.get("published_at_ts") or 0
        ),
        reverse=True
    )

    return deduped[:top_n]