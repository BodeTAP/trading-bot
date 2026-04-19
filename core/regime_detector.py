"""
Market regime detector.

Classifies market into one of four regimes using:
  - ADX(14)          — trend strength
  - Bollinger Band width — volatility squeeze detection
  - ATR ratio        — current ATR vs recent average
  - Higher-high / lower-low pattern over last 10 candles

Priority order: VOLATILE → TRENDING_UP/DOWN → SIDEWAYS
"""

import logging
import math

import numpy as np
import pandas as pd
from ta.trend import ADXIndicator
from ta.volatility import BollingerBands

logger = logging.getLogger(__name__)

# ── Thresholds ────────────────────────────────────────────────────────────────
ADX_TREND_MIN   = 25.0   # ADX above this → trending
ADX_SIDEWAYS_MAX = 20.0  # ADX below this → sideways candidate
ATR_VOLATILE_MULT = 1.5  # current_atr > avg_atr × this → VOLATILE
BB_SQUEEZE_MAX  = 0.05   # BB % width below this → squeeze (sideways)
HH_HL_WINDOW    = 10     # candles to check for higher-high / lower-low

_STRATEGY_HINTS = {
    "TRENDING_UP":   "Trend following — tahan posisi lebih lama, trailing stop longgar (ATR×3)",
    "TRENDING_DOWN": "Proteksi modal — hindari BUY baru, prioritaskan cash atau posisi minimal",
    "SIDEWAYS":      "Mean reversion — beli di oversold, jual di overbought, jangan chase breakout",
    "VOLATILE":      "Tunggu kepastian — posisi sangat kecil, trailing stop ketat (ATR×1.5)",
}

_REGIME_COLORS = {
    "TRENDING_UP":   "#50fa7b",
    "TRENDING_DOWN": "#ff5555",
    "SIDEWAYS":      "#8be9fd",
    "VOLATILE":      "#ffb86c",
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _compute_adx(df: pd.DataFrame) -> float | None:
    try:
        adx_series = ADXIndicator(df["high"], df["low"], df["close"], window=14).adx()
        val = adx_series.dropna().iloc[-1] if not adx_series.dropna().empty else None
        return float(val) if val is not None and not math.isnan(val) else None
    except Exception as e:
        logger.debug(f"ADX compute error: {e}")
        return None


def _compute_bb_width(df: pd.DataFrame) -> float | None:
    try:
        bb    = BollingerBands(df["close"], window=20, window_dev=2)
        width = bb.bollinger_wband()       # % width = (upper-lower)/mavg
        val   = width.dropna().iloc[-1] if not width.dropna().empty else None
        return float(val) if val is not None and not math.isnan(val) else None
    except Exception as e:
        logger.debug(f"BB width compute error: {e}")
        return None


def _atr_ratio(df: pd.DataFrame) -> float | None:
    if "atr" not in df.columns:
        return None
    atr_series = df["atr"].dropna()
    if len(atr_series) < 2:
        return None
    current = float(atr_series.iloc[-1])
    avg     = float(atr_series.tail(14).mean())
    return round(current / avg, 3) if avg > 0 else None


def _higher_highs_lower_lows(df: pd.DataFrame, window: int = HH_HL_WINDOW) -> tuple[bool, bool]:
    """
    Return (is_higher_high_low, is_lower_high_low) based on the last `window` candles.
    Uses simple slope: compare first-half mean vs second-half mean.
    """
    tail   = df.tail(window)
    if len(tail) < 4:
        return False, False
    mid    = len(tail) // 2
    first  = tail.iloc[:mid]
    second = tail.iloc[mid:]

    hh = second["high"].mean() > first["high"].mean() and second["low"].mean() > first["low"].mean()
    ll = second["high"].mean() < first["high"].mean() and second["low"].mean() < first["low"].mean()
    return hh, ll


# ── Regime confidence helpers ─────────────────────────────────────────────────

def _trending_confidence(adx: float, has_pattern: bool, atr_ratio: float | None) -> float:
    adx_score     = min(1.0, max(0.0, (adx - ADX_TREND_MIN) / 20))
    pattern_score = 0.3 if has_pattern else 0.0
    atr_score     = 0.2 if (atr_ratio is not None and 0.8 < atr_ratio < 1.4) else 0.0
    return round(min(1.0, adx_score * 0.5 + pattern_score + atr_score), 2)


def _sideways_confidence(adx: float | None, bb_width: float | None) -> float:
    adx_score = min(1.0, max(0.0, (ADX_SIDEWAYS_MAX - (adx or 20)) / 10)) if adx else 0.4
    bb_score  = min(1.0, max(0.0, (BB_SQUEEZE_MAX - (bb_width or 0.05)) / 0.03)) if bb_width else 0.3
    return round(min(1.0, adx_score * 0.6 + bb_score * 0.4), 2)


def _volatile_confidence(atr_ratio: float) -> float:
    return round(min(1.0, (atr_ratio - ATR_VOLATILE_MULT) / 1.0), 2)


# ── Main detector ─────────────────────────────────────────────────────────────

class RegimeDetector:

    def detect(self, df: pd.DataFrame, df_4h: pd.DataFrame | None = None) -> dict:
        """
        Detect market regime from the primary (1h) dataframe,
        optionally confirmed by the 4h frame.

        Returns:
            {
                "regime":        "TRENDING_UP" | "TRENDING_DOWN" | "SIDEWAYS" | "VOLATILE",
                "confidence":    0.78,
                "adx":           32.4,
                "bb_width":      0.042,
                "atr_ratio":     1.1,
                "strategy_hint": "...",
                "color":         "#50fa7b",
            }
        """
        adx      = _compute_adx(df)
        bb_width = _compute_bb_width(df)
        atr_r    = _atr_ratio(df)
        hh, ll   = _higher_highs_lower_lows(df)

        valid_1h = df.dropna(subset=["rsi", "ma50"])
        price    = float(valid_1h.iloc[-1]["close"]) if not valid_1h.empty else None
        ma50_v   = float(valid_1h.iloc[-1]["ma50"])  if not valid_1h.empty else None

        # ── 1. VOLATILE: ATR blowout takes priority ───────────────────────────
        if atr_r is not None and atr_r > ATR_VOLATILE_MULT:
            regime     = "VOLATILE"
            confidence = _volatile_confidence(atr_r)
        # ── 2. TRENDING: ADX strong + price structure ─────────────────────────
        elif adx is not None and adx > ADX_TREND_MIN:
            # Determine direction
            if price is not None and ma50_v is not None:
                above_ma50 = price > ma50_v
            else:
                above_ma50 = hh   # fallback

            # 4h confirmation (optional)
            tf4_bullish: bool | None = None
            if df_4h is not None:
                valid_4h = df_4h.dropna(subset=["ma50"])
                if not valid_4h.empty:
                    row4 = valid_4h.iloc[-1]
                    tf4_bullish = row4["close"] > row4["ma50"]

            # Confirm direction with price pattern + optional 4h
            if above_ma50 and hh:
                regime = "TRENDING_UP"
            elif not above_ma50 and ll:
                regime = "TRENDING_DOWN"
            elif tf4_bullish is True:
                regime = "TRENDING_UP"
            elif tf4_bullish is False:
                regime = "TRENDING_DOWN"
            elif above_ma50:
                regime = "TRENDING_UP"
            else:
                regime = "TRENDING_DOWN"

            confidence = _trending_confidence(adx, hh if regime == "TRENDING_UP" else ll, atr_r)
        # ── 3. SIDEWAYS: low ADX or BB squeeze ───────────────────────────────
        else:
            regime     = "SIDEWAYS"
            confidence = _sideways_confidence(adx, bb_width)

        result = {
            "regime":        regime,
            "confidence":    confidence,
            "adx":           round(adx, 2) if adx is not None else None,
            "bb_width":      round(bb_width, 4) if bb_width is not None else None,
            "atr_ratio":     atr_r,
            "strategy_hint": _STRATEGY_HINTS[regime],
            "color":         _REGIME_COLORS[regime],
        }

        adx_str = f"ADX={adx:.1f}" if adx else "ADX=N/A"
        atr_str = f"ATR×{atr_r:.2f}" if atr_r else "ATR=N/A"
        logger.info(
            f"Regime: {regime} ({confidence:.0%}) | {adx_str} | {atr_str} | "
            f"HH={hh} LL={ll} BB_w={f'{bb_width:.4f}' if bb_width is not None else 'N/A'}"
        )
        return result
