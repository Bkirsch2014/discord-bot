import os
from typing import List, Dict

import aiohttp
from alpaca.data.requests import StockSnapshotRequest


async def fetch_us_symbols_from_finnhub() -> List[str]:
    api_key = os.getenv("FINNHUB_API_KEY")
    if not api_key:
        return []

    url = f"https://finnhub.io/api/v1/stock/symbol?exchange=US&token={api_key}"

    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=60) as response:
            data = await response.json()

    symbols = []
    for item in data or []:
        symbol = item.get("symbol")
        instrument_type = (item.get("type") or "").lower()
        if not symbol:
            continue
        if instrument_type and instrument_type not in {"common stock", "etf", "etp"}:
            continue
        if "." in symbol or "-" in symbol:
            continue
        symbols.append(symbol.upper())

    return sorted(set(symbols))


def chunked(seq, size):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def build_top_liquid_universe(data_client, feed, symbols: List[str], top_n: int = 1000) -> List[str]:
    ranked: List[Dict] = []

    for chunk in chunked(symbols, 200):
        req = StockSnapshotRequest(symbol_or_symbols=chunk, feed=feed)
        resp = data_client.get_stock_snapshot(req)

        for symbol in chunk:
            snap = resp.get(symbol)
            if not snap or not snap.daily_bar or not snap.latest_trade:
                continue

            price = float(snap.latest_trade.price)
            volume = float(snap.daily_bar.volume or 0)
            if price < 3 or volume < 100_000:
                continue

            dollar_volume = price * volume

            ranked.append({
                "symbol": symbol,
                "price": price,
                "volume": volume,
                "dollar_volume": dollar_volume,
            })

    ranked.sort(key=lambda x: x["dollar_volume"], reverse=True)
    return [x["symbol"] for x in ranked[:top_n]]