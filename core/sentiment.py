"""
Fear & Greed Index fetcher.

Data source: https://api.alternative.me/fng/
Public API — no key required.
Results are cached for 1 hour to avoid redundant requests on 15m cycles.
"""

import logging
import time
from datetime import datetime

import requests

logger = logging.getLogger(__name__)

_FNG_URL      = "https://api.alternative.me/fng/?limit=7"
_CACHE_TTL    = 3600   # seconds

# Module-level cache
_cache_data: dict | None = None
_cache_ts:   float       = 0.0


# ── Classification ────────────────────────────────────────────────────────────

def _classify(value: int) -> str:
    if value <= 24:
        return "Extreme Fear"
    if value <= 44:
        return "Fear"
    if value <= 55:
        return "Neutral"
    if value <= 74:
        return "Greed"
    return "Extreme Greed"


def _interpret(value: int) -> str:
    if value <= 24:
        return "Sentimen sangat negatif — potensi peluang beli (pasar oversold secara sentimen)"
    if value <= 44:
        return "Pasar masih takut — pertimbangkan akumulasi bertahap"
    if value <= 55:
        return "Sentimen netral — ikuti sinyal teknikal"
    if value <= 74:
        return "Pasar mulai serakah — waspadai potensi koreksi"
    return "Sentimen euforia — kurangi eksposur, risiko koreksi tinggi"


def _trend_label(values: list[int]) -> str:
    """Summarise 7-day direction from oldest→newest."""
    if len(values) < 2:
        return "tidak cukup data"
    delta = values[-1] - values[0]
    if delta > 5:
        return f"naik (dari {values[0]} ke {values[-1]}) ← bullish sentiment"
    if delta < -5:
        return f"turun (dari {values[0]} ke {values[-1]}) ← bearish sentiment"
    return f"stabil (dari {values[0]} ke {values[-1]})"


# ── Fetcher ───────────────────────────────────────────────────────────────────

class SentimentFetcher:
    """Fetches and caches the Fear & Greed Index."""

    def fetch(self) -> dict | None:
        """
        Return sentiment dict or None on failure.

        {
            "current_value":          72,
            "current_label":          "Greed",
            "interpretation":         "...",
            "yesterday_value":        68,
            "yesterday_label":        "Greed",
            "trend_7d":               "naik (dari 45 ke 72) ← bullish sentiment",
            "history": [              # 7 entries, oldest first
                {"date": "2026-04-13", "value": 45, "label": "Neutral"},
                ...
            ],
        }
        """
        global _cache_data, _cache_ts

        # Return cached result if still fresh
        if _cache_data is not None and (time.time() - _cache_ts) < _CACHE_TTL:
            logger.debug("Fear & Greed Index: serving from cache")
            return _cache_data

        try:
            resp = requests.get(_FNG_URL, timeout=10)
            resp.raise_for_status()
            raw = resp.json().get("data", [])

            if not raw:
                logger.warning("Fear & Greed API: response kosong")
                return _cache_data  # return stale cache if available

            # API returns newest-first; reverse to get oldest-first
            entries = list(reversed(raw))
            history = []
            for entry in entries:
                val = int(entry["value"])
                history.append({
                    "date":  datetime.fromtimestamp(int(entry["timestamp"])).strftime("%Y-%m-%d"),
                    "value": val,
                    "label": _classify(val),
                })

            values       = [e["value"] for e in history]
            current      = history[-1]
            yesterday    = history[-2] if len(history) >= 2 else current

            result = {
                "current_value":   current["value"],
                "current_label":   current["label"],
                "interpretation":  _interpret(current["value"]),
                "yesterday_value": yesterday["value"],
                "yesterday_label": yesterday["label"],
                "trend_7d":        _trend_label(values),
                "history":         history,
            }

            _cache_data = result
            _cache_ts   = time.time()
            logger.info(
                f"Fear & Greed Index: {current['value']} — {current['label']} "
                f"(cache updated)"
            )
            return result

        except requests.exceptions.Timeout:
            logger.warning("Fear & Greed API: timeout")
        except requests.exceptions.RequestException as e:
            logger.warning(f"Fear & Greed API: request gagal: {e}")
        except (KeyError, ValueError, TypeError) as e:
            logger.warning(f"Fear & Greed API: parse error: {e}")

        # Return stale cache rather than nothing
        if _cache_data is not None:
            logger.warning("Fear & Greed API: menggunakan cache lama")
            return _cache_data

        return None
