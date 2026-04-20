"""
Dynamic position sizer.

Adjusts Claude's recommended size_pct based on four independent factors:
  1. ATR volatility  — high ATR → smaller position
  2. Claude confidence — MEDIUM → 60 %, LOW → HOLD (handled by risk_manager)
  3. HMM regime state — CRASH/BEAR reduce or block; BULL full; EUPHORIA reduce
  4. Fear & Greed    — Extreme Fear bonus; Extreme Greed penalty

All adjustments are additive percentages on top of the base_pct, then
clamped to [MIN_SIZE, MAX_SIZE].
"""

import logging

logger = logging.getLogger(__name__)

MIN_SIZE = 5.0   # % — never go below this for an active trade
MAX_SIZE = 20.0  # % — hard cap (also enforced by risk_manager)

# Regime multipliers
_REGIME_MULT: dict[str, float] = {
    "TRENDING_UP":   1.20,
    "TRENDING_DOWN": 0.40,
    "SIDEWAYS":      0.80,
    "VOLATILE":      0.50,
}

# HMM multipliers (applied to base_pct)
_HMM_MULT: dict[str, float] = {
    "CRASH":    0.0,   # → HOLD, handled before this is called
    "BEAR":     0.60,
    "NEUTRAL":  0.80,
    "BULL":     1.00,
    "EUPHORIA": 0.75,
}

# Confidence multipliers
_CONF_MULT: dict[str, float] = {
    "HIGH":   1.00,
    "MEDIUM": 0.75,
    "LOW":    0.00,   # → HOLD, handled by risk_manager
}


class PositionSizer:

    def calculate(
        self,
        base_pct:          float,
        atr:               float,
        avg_atr:           float,
        confidence:        str,
        hmm_state:         str | None,
        fear_greed_value:  int | None,
        regime:            str | None = None,
    ) -> float:
        """
        Return the final position size as a percentage of available capital.

        Parameters
        ----------
        base_pct          Claude's raw recommendation (0–20)
        atr               Current ATR value
        avg_atr           Historical average ATR (used as reference)
        confidence        Claude's confidence: HIGH / MEDIUM / LOW
        hmm_state         HMM regime label or None
        fear_greed_value  Fear & Greed Index 0–100, or None
        """
        steps: list[str] = [f"base={base_pct:.1f}%"]
        size = base_pct

        # ── 1. ATR volatility adjustment ──────────────────────────────────────
        if atr > 0 and avg_atr > 0:
            atr_ratio = avg_atr / atr          # >1 when calm, <1 when volatile
            atr_ratio = max(0.5, min(1.5, atr_ratio))   # clamp to avoid extremes
            size_after_atr = size * atr_ratio
            delta = size_after_atr - size
            steps.append(f"atr×{atr_ratio:.2f}={delta:+.1f}%")
            size = size_after_atr
        else:
            steps.append("atr=N/A")

        # ── 2. Confidence multiplier ──────────────────────────────────────────
        conf_mult = _CONF_MULT.get(confidence, 1.0)
        if conf_mult != 1.0:
            delta = size * conf_mult - size
            steps.append(f"conf({confidence})={delta:+.1f}%")
            size *= conf_mult

        # ── 3. HMM state multiplier ───────────────────────────────────────────
        if hmm_state:
            hmm_mult = _HMM_MULT.get(hmm_state, 1.0)
            if hmm_mult != 1.0:
                delta = size * hmm_mult - size
                steps.append(f"hmm({hmm_state})={delta:+.1f}%")
                size *= hmm_mult

        # ── 4. Fear & Greed adjustment ────────────────────────────────────────
        if fear_greed_value is not None:
            if fear_greed_value <= 24:
                bonus = size * 0.20
                steps.append(f"fng(ExFear)=+{bonus:.1f}%")
                size += bonus
            elif fear_greed_value >= 75:
                penalty = size * 0.30
                steps.append(f"fng(ExGreed)=-{penalty:.1f}%")
                size -= penalty

        # ── 5. Regime multiplier ─────────────────────────────────────────────
        if regime:
            reg_mult = _REGIME_MULT.get(regime, 1.0)
            if reg_mult != 1.0:
                delta = size * reg_mult - size
                steps.append(f"regime({regime})={delta:+.1f}%")
                size *= reg_mult

        # ── Clamp ─────────────────────────────────────────────────────────────
        # If HMM forced to 0 (CRASH) or confidence forced to 0, don't clamp up
        if size < 0.1:
            final = 0.0
        else:
            final = round(max(MIN_SIZE, min(MAX_SIZE, size)), 1)

        breakdown = " | ".join(steps)
        logger.info(f"PositionSizer: {breakdown} → final={final:.1f}%")
        return final
