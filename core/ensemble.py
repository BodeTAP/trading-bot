"""
Ensemble signal combining three independent models via weighted voting.

  Model 1 — Rule-based   (weight 0.3): RSI + MA crossover + MACD + MTF bias
  Model 2 — HMM-based    (weight 0.3): HMM regime state
  Model 3 — Momentum     (weight 0.4): multi-TF price-action score

Voting: each model produces BUY (+1) / HOLD (0) / SELL (-1).
Weighted sum > +0.3 → BUY, < -0.3 → SELL, else HOLD.
"""

import logging
import math
import os

import pandas as pd

logger = logging.getLogger(__name__)

WEIGHTS        = {"rule": 0.3, "hmm": 0.3, "momentum": 0.4}
BUY_THRESHOLD  =  0.3
SELL_THRESHOLD = -0.3
_VOTE_SCORE    = {"BUY": 1, "HOLD": 0, "SELL": -1}


# ── Model 1: Rule-based ───────────────────────────────────────────────────────

def _rule_model(df_1h: pd.DataFrame, tf_biases: dict[str, str]) -> tuple[str, float]:
    """RSI + MA crossover + MACD + MTF bias majority vote."""
    valid = df_1h.dropna(subset=["rsi", "ma50", "macd"])
    if valid.empty:
        return "HOLD", 0.0

    row   = valid.iloc[-1]
    price = row["close"]
    buy_v = sell_v = 0

    # RSI
    if not math.isnan(row["rsi"]):
        if row["rsi"] < 35:
            buy_v += 1
        elif row["rsi"] > 65:
            sell_v += 1

    # MA crossover / price vs MA50
    if not math.isnan(row["ma50"]):
        ma200 = row.get("ma200", float("nan"))
        if not (isinstance(ma200, float) and math.isnan(ma200)):
            buy_v  += 1 if row["ma50"] > ma200 else 0
            sell_v += 0 if row["ma50"] > ma200 else 1
        else:
            buy_v  += 1 if price > row["ma50"] else 0
            sell_v += 0 if price > row["ma50"] else 1

    # MACD
    if not math.isnan(row["macd"]) and not math.isnan(row["macd_signal"]):
        if row["macd"] > row["macd_signal"]:
            buy_v += 1
        else:
            sell_v += 1

    # MTF bias majority
    if tf_biases:
        bull = sum(1 for b in tf_biases.values() if b == "BULLISH")
        bear = sum(1 for b in tf_biases.values() if b == "BEARISH")
        if bull > bear:
            buy_v += 1
        elif bear > bull:
            sell_v += 1

    total = buy_v + sell_v
    if total == 0:
        return "HOLD", 0.0

    if buy_v >= 3:
        return "BUY",  round(buy_v  / (total + 1e-9), 2)
    if sell_v >= 3:
        return "SELL", round(sell_v / (total + 1e-9), 2)
    return "HOLD", round(0.4 + abs(buy_v - sell_v) * 0.05, 2)


# ── Model 2: HMM-based ────────────────────────────────────────────────────────

def _hmm_model(hmm_state: str | None, hmm_confidence: float | None) -> tuple[str, float]:
    """Translate HMM regime to directional signal."""
    if hmm_state is None:
        return "HOLD", 0.0

    conf = hmm_confidence if hmm_confidence is not None else 0.5

    if hmm_state == "BULL":
        return "BUY",  round(conf, 2)
    if hmm_state == "EUPHORIA":
        return "BUY",  round(conf * 0.80, 2)   # discount: reversal risk
    if hmm_state == "BEAR":
        return "SELL", round(conf, 2)
    if hmm_state == "CRASH":
        return "SELL", round(conf * 0.80, 2)
    return "HOLD", round(conf, 2)               # NEUTRAL


# ── Model 3: Momentum ─────────────────────────────────────────────────────────

def _momentum_model(multi_tf: dict) -> tuple[str, float]:
    """
    Multi-TF price-action score (0–5):
      +1  price > MA50  (medium TF)
      +1  price > MA200 (medium TF)
      +1  RSI(medium TF) < 60  (not yet overbought)
      +1  RSI(short TF) rising vs previous candle
      +1  long TF bias BULLISH  (or -1 if BEARISH, capped at min 0)
    """
    from core.market_data import _tf_bias   # local import avoids circular dep at module load

    score     = 0
    max_score = 5

    tf_short  = os.getenv('TIMEFRAME_SHORT',  '15m')
    tf_medium = os.getenv('TIMEFRAME_MEDIUM', '1h')
    tf_long   = os.getenv('TIMEFRAME_LONG',   '4h')

    df_1h  = multi_tf.get(tf_medium)
    df_15m = multi_tf.get(tf_short)
    df_4h  = multi_tf.get(tf_long)

    if df_1h is not None:
        valid_1h = df_1h.dropna(subset=["rsi", "ma50"])
        if not valid_1h.empty:
            row   = valid_1h.iloc[-1]
            price = row["close"]

            if price > row["ma50"]:
                score += 1

            ma200 = row.get("ma200", float("nan"))
            if not (isinstance(ma200, float) and math.isnan(ma200)):
                if price > ma200:
                    score += 1

            if row["rsi"] < 60:
                score += 1

    if df_15m is not None:
        valid_15m = df_15m.dropna(subset=["rsi"])
        if len(valid_15m) >= 2:
            if valid_15m.iloc[-1]["rsi"] > valid_15m.iloc[-2]["rsi"]:
                score += 1

    if df_4h is not None:
        bias_4h = _tf_bias(df_4h)
        if bias_4h == "BULLISH":
            score += 1
        elif bias_4h == "BEARISH":
            score = max(0, score - 1)   # penalise but don't go negative

    conf = score / max_score

    if score >= 4:
        return "BUY",  round(conf, 2)
    if score <= 1:
        return "SELL", round(1.0 - conf, 2)
    return "HOLD", round(0.3 + abs(score - 2.5) * 0.08, 2)


# ── Consensus label ───────────────────────────────────────────────────────────

def _consensus(votes: dict[str, str]) -> str:
    v = list(votes.values())
    if len(set(v)) == 1:
        return "STRONG"
    buy_count  = v.count("BUY")
    sell_count = v.count("SELL")
    if buy_count >= 2 or sell_count >= 2:
        return "WEAK"
    return "SPLIT"


# ── Public API ────────────────────────────────────────────────────────────────

class EnsembleSignal:

    def compute(
        self,
        df_1h:          pd.DataFrame,
        multi_tf:       dict,
        hmm_state:      str | None  = None,
        hmm_confidence: float | None = None,
        tf_biases:      dict | None  = None,
    ) -> dict:
        """
        Returns:
            {
                "signal":      "BUY" | "SELL" | "HOLD",
                "score":       float  in [-1, +1],
                "votes":       {"rule": ..., "hmm": ..., "momentum": ...},
                "confidences": {"rule": ..., "hmm": ..., "momentum": ...},
                "consensus":   "STRONG" | "WEAK" | "SPLIT",
            }
        """
        try:
            rule_sig, rule_conf = _rule_model(df_1h, tf_biases or {})
        except Exception as e:
            logger.warning(f"Ensemble rule model error: {e}")
            rule_sig, rule_conf = "HOLD", 0.0

        try:
            hmm_sig, hmm_conf = _hmm_model(hmm_state, hmm_confidence)
        except Exception as e:
            logger.warning(f"Ensemble HMM model error: {e}")
            hmm_sig, hmm_conf = "HOLD", 0.0

        try:
            mom_sig, mom_conf = _momentum_model(multi_tf)
        except Exception as e:
            logger.warning(f"Ensemble momentum model error: {e}")
            mom_sig, mom_conf = "HOLD", 0.0

        votes       = {"rule": rule_sig, "hmm": hmm_sig, "momentum": mom_sig}
        confidences = {"rule": rule_conf, "hmm": hmm_conf, "momentum": mom_conf}

        # Weighted score
        score = (
            WEIGHTS["rule"]     * _VOTE_SCORE[rule_sig]
            + WEIGHTS["hmm"]    * _VOTE_SCORE[hmm_sig]
            + WEIGHTS["momentum"] * _VOTE_SCORE[mom_sig]
        )
        score = round(score, 4)

        if score > BUY_THRESHOLD:
            signal = "BUY"
        elif score < SELL_THRESHOLD:
            signal = "SELL"
        else:
            signal = "HOLD"

        consensus = _consensus(votes)

        result = {
            "signal":      signal,
            "score":       score,
            "votes":       votes,
            "confidences": confidences,
            "consensus":   consensus,
        }

        logger.info(
            f"Ensemble → {signal} (score={score:+.3f}, {consensus}) | "
            f"rule={rule_sig}({rule_conf:.0%}) "
            f"hmm={hmm_sig}({hmm_conf:.0%}) "
            f"momentum={mom_sig}({mom_conf:.0%})"
        )
        return result
