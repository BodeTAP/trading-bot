"""
News fetcher — ambil berita crypto terbaru dari RSS feed publik.

Sumber (tidak memerlukan API key):
  - CoinDesk
  - CoinTelegraph
  - Decrypt

Berita difilter berdasarkan relevansi terhadap pair aktif.
Cache 30 menit agar tidak membebani sumber berita.
"""

import logging
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import requests

logger = logging.getLogger(__name__)

_CACHE_TTL = 1800  # 30 menit

_RSS_SOURCES = [
    ("CoinDesk",      "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("CoinTelegraph", "https://cointelegraph.com/rss"),
    ("Decrypt",       "https://decrypt.co/feed"),
]

# Kata kunci per coin untuk filter relevansi
_COIN_KEYWORDS: dict[str, list[str]] = {
    "BTC":  ["bitcoin", "btc", "satoshi", "crypto market", "crypto"],
    "ETH":  ["ethereum", "eth", "ether", "defi", "smart contract"],
    "BNB":  ["binance", "bnb", "bsc", "bnb chain"],
    "SOL":  ["solana", "sol"],
    "XRP":  ["xrp", "ripple"],
    "ADA":  ["cardano", "ada"],
    "DOGE": ["dogecoin", "doge"],
    "AVAX": ["avalanche", "avax"],
    "DOT":  ["polkadot", "dot"],
    "MATIC":["polygon", "matic"],
}

# Kata kunci market-wide yang selalu relevan
_MARKET_KEYWORDS = [
    "crypto", "bitcoin", "blockchain", "sec", "regulation",
    "fed", "interest rate", "inflation", "etf", "spot etf",
    "exchange hack", "market crash", "bull run", "bear market",
]

# Module-level cache
_cache_data: dict | None = None
_cache_ts:   float       = 0.0


def _coin_keywords(base: str) -> list[str]:
    """Return keyword list for a coin symbol."""
    return _COIN_KEYWORDS.get(base.upper(), [base.lower()])


def _is_relevant(title: str, base: str) -> bool:
    title_low = title.lower()
    keywords  = _coin_keywords(base) + _MARKET_KEYWORDS
    return any(kw in title_low for kw in keywords)


def _parse_rss(xml_text: str, source_name: str, base: str) -> list[dict]:
    items: list[dict] = []
    try:
        root = ET.fromstring(xml_text)
        # Handle both <rss> and <feed> (Atom) formats
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        channel = root.find("channel")
        entries = channel.findall("item") if channel is not None else root.findall("atom:entry", ns)

        for entry in entries[:20]:  # ambil max 20 item per sumber
            # Title
            title_el = entry.find("title")
            if title_el is None or not title_el.text:
                continue
            title = title_el.text.strip()

            if not _is_relevant(title, base):
                continue

            # Publication date
            pub_date = ""
            for tag in ("pubDate", "published", "updated", "{http://www.w3.org/2005/Atom}published"):
                el = entry.find(tag)
                if el is not None and el.text:
                    try:
                        pub_date = parsedate_to_datetime(el.text).strftime("%d %b %H:%M")
                    except Exception:
                        pub_date = el.text[:16]
                    break

            items.append({
                "title":  title,
                "source": source_name,
                "date":   pub_date,
            })

    except ET.ParseError as e:
        logger.debug(f"RSS parse error ({source_name}): {e}")
    return items


class NewsFetcher:
    """Fetch dan cache berita crypto dari RSS feed publik."""

    def fetch(self, pair: str = "BTC/USDT") -> dict | None:
        """
        Return news dict atau None jika semua sumber gagal.

        {
            "headlines": [
                {"title": "...", "source": "CoinDesk", "date": "21 Apr 14:30"},
                ...
            ],
            "count": 8,
            "base": "ETH",
            "fetched_at": "2026-04-21T14:30:00",
        }
        """
        global _cache_data, _cache_ts

        base = pair.split("/")[0].upper()

        # Kembalikan cache jika masih segar
        if (_cache_data is not None
                and _cache_data.get("base") == base
                and (time.time() - _cache_ts) < _CACHE_TTL):
            logger.debug(f"News: serving {len(_cache_data['headlines'])} headlines dari cache")
            return _cache_data

        all_headlines: list[dict] = []

        for source_name, url in _RSS_SOURCES:
            try:
                resp = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
                resp.raise_for_status()
                items = _parse_rss(resp.text, source_name, base)
                all_headlines.extend(items)
                logger.debug(f"News: {len(items)} berita relevan dari {source_name}")
            except requests.exceptions.Timeout:
                logger.warning(f"News: timeout saat fetch {source_name}")
            except Exception as e:
                logger.warning(f"News: gagal fetch {source_name}: {e}")

        if not all_headlines:
            logger.warning("News: tidak ada berita yang berhasil di-fetch")
            return _cache_data  # kembalikan cache lama jika ada

        result = {
            "headlines":   all_headlines[:10],  # max 10 berita untuk konteks Claude
            "count":       len(all_headlines),
            "base":        base,
            "fetched_at":  datetime.now().isoformat(),
        }

        _cache_data = result
        _cache_ts   = time.time()
        logger.info(f"News: {len(all_headlines)} berita relevan untuk {base} "
                    f"dari {len(_RSS_SOURCES)} sumber")
        return result


def format_news_for_claude(news: dict) -> str:
    """Format news dict menjadi teks siap masuk ke konteks Claude."""
    if not news or not news.get("headlines"):
        return ""

    lines = [f"\nBERITA TERKINI ({news['base']} — {len(news['headlines'])} headline):"]
    for i, item in enumerate(news["headlines"], 1):
        date_str = f" [{item['date']}]" if item.get("date") else ""
        lines.append(f"{i}. [{item['source']}]{date_str} {item['title']}")

    lines.append(
        "\nPerhatikan berita di atas saat membuat keputusan: "
        "berita regulasi negatif, hack exchange, atau sentimen bearish kuat "
        "dapat mengesampingkan sinyal teknikal bullish."
    )
    return "\n".join(lines)
