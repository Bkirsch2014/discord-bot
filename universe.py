import json
import os

UNIVERSE_FILE = "universe.json"

def _load_universe():
    if os.path.exists(UNIVERSE_FILE):
        with open(UNIVERSE_FILE, "r") as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
    # Fallback: a small hardcoded list (replace with your reliable source)
    return ["AAPL", "MSFT", "NVDA", "AMZN", "TSLA", "META"]

def get_universe():
    # You could add refresh logic to pull from an API or file
    return _load_universe()
