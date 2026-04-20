import logging
import os
from dotenv import load_dotenv
from core.market_data import get_exchange, get_futures_exchange

load_dotenv()

logger = logging.getLogger(__name__)

MIN_BTC_ORDER  = 0.00001
MIN_USDT_ORDER = 10.0

_REGIME_ATR_MULT: dict[str, float] = {
    "TRENDING_UP":   3.0,
    "TRENDING_DOWN": 1.5,
    "SIDEWAYS":      2.0,
    "VOLATILE":      1.5,
}


# ── Trailing Stop Manager ──────────────────────────────────────────────────────

class TrailingStopManager:
    """
    ATR-based trailing stop loss tracker.

    The stop is set at entry_price - (ATR × multiplier) and ratchets up
    as price rises. It never moves down.
    """

    def __init__(self):
        self._pair:         str | None   = None
        self._entry_price:  float | None = None
        self._highest:      float | None = None
        self._stop:         float | None = None
        self._atr:          float | None = None
        self._multiplier:   float        = float(os.getenv('TRAILING_STOP_ATR_MULTIPLIER', 2.0))

    # ── Public interface ───────────────────────────────────────────────────────

    @property
    def is_active(self) -> bool:
        return self._stop is not None

    @property
    def stop_price(self) -> float | None:
        return self._stop

    @property
    def entry_price(self) -> float | None:
        return self._entry_price

    def track_position(self, pair: str, entry_price: float, atr: float,
                       atr_multiplier: float | None = None) -> None:
        multiplier    = atr_multiplier if atr_multiplier is not None else self._multiplier
        self._pair    = pair
        self._entry_price = entry_price
        self._highest = entry_price
        self._atr     = atr
        self._stop    = entry_price - atr * multiplier
        logger.info(
            f"Trailing stop started: entry=${entry_price:,.2f} "
            f"ATR={atr:.2f} ×{multiplier} → initial stop=${self._stop:,.2f}"
        )

    def update_stop(self, current_price: float) -> bool:
        """
        Ratchet stop up if price made a new high.
        Returns True if the stop was just triggered (price ≤ stop).
        """
        if not self.is_active:
            return False

        if current_price > self._highest:
            new_stop = current_price - self._atr * self._multiplier
            if new_stop > self._stop:
                logger.debug(
                    f"Trailing stop raised: ${self._stop:,.2f} → ${new_stop:,.2f} "
                    f"(high=${current_price:,.2f})"
                )
                self._stop    = new_stop
                self._highest = current_price

        if current_price <= self._stop:
            logger.warning(
                f"Trailing stop TRIGGERED at ${current_price:,.2f} "
                f"(stop=${self._stop:,.2f})"
            )
            return True

        return False

    def get_current_stop(self) -> float | None:
        return self._stop

    def stop_distance_pct(self, current_price: float) -> float | None:
        """Return distance from current price to stop as a positive percentage."""
        if self._stop is None or current_price <= 0:
            return None
        return (current_price - self._stop) / current_price * 100

    def clear_position(self) -> None:
        self._pair        = None
        self._entry_price = None
        self._highest     = None
        self._stop        = None
        self._atr         = None
        logger.info("Trailing stop cleared.")


# ── Short Manager (Futures) ───────────────────────────────────────────────────

class ShortManager:
    """
    Manages short positions via Binance USDT-margined futures.

    Requires TRADING_MODE=futures in .env.
    Only opens short when MA50 < MA200 (confirmed downtrend).
    """

    def __init__(self):
        self._pair:         str | None   = None
        self._entry_price:  float | None = None
        self._btc_amount:   float | None = None
        self._enabled = os.getenv('TRADING_MODE', 'spot').lower() == 'futures'

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    @property
    def is_active(self) -> bool:
        return self._entry_price is not None

    @property
    def entry_price(self) -> float | None:
        return self._entry_price

    def open_short(self, pair: str, size_pct: float, portfolio: dict) -> dict:
        if not self._enabled:
            return {"status": "skipped", "reason": "TRADING_MODE != futures"}
        try:
            exchange    = get_futures_exchange()
            btc_price   = portfolio.get('btc_price', 0)
            usdt_margin = portfolio['usdt_available'] * (size_pct / 100)
            btc_amount  = round(usdt_margin / btc_price, 6)
            if btc_amount < MIN_BTC_ORDER:
                return {"status": "skipped", "reason": f"BTC amount terlalu kecil ({btc_amount})"}
            order = exchange.create_market_sell_order(pair, btc_amount,
                                                       params={"reduceOnly": False})
            self._pair        = pair
            self._entry_price = btc_price
            self._btc_amount  = btc_amount
            logger.info(f"SHORT OPEN: {btc_amount} BTC @ ${btc_price:,.2f} (order {order['id']})")
            return {"status": "success", "order": order}
        except Exception as e:
            logger.error(f"SHORT OPEN gagal: {e}", exc_info=True)
            return {"status": "error", "message": str(e)}

    def close_short(self, portfolio: dict) -> dict:
        if not self._enabled or not self.is_active:
            return {"status": "skipped", "reason": "short tidak aktif"}
        try:
            exchange   = get_futures_exchange()
            order      = exchange.create_market_buy_order(
                self._pair, self._btc_amount, params={"reduceOnly": True}
            )
            current    = portfolio.get('btc_price', 0)
            pnl        = (self._entry_price - current) * self._btc_amount
            logger.info(
                f"SHORT CLOSE: {self._btc_amount} BTC @ ${current:,.2f} | "
                f"PnL estimasi: ${pnl:+,.2f} (order {order['id']})"
            )
            self._pair = self._entry_price = self._btc_amount = None
            return {"status": "success", "order": order}
        except Exception as e:
            logger.error(f"SHORT CLOSE gagal: {e}", exc_info=True)
            return {"status": "error", "message": str(e)}

    def clear(self) -> None:
        self._pair = self._entry_price = self._btc_amount = None


# ── Take Profit Manager ────────────────────────────────────────────────────────

class TakeProfitManager:
    """
    Tracks a take-profit target. When price reaches the target,
    50% of the BTC position is sold and the trailing stop continues
    on the remaining 50%.
    """

    def __init__(self):
        self._entry_price:    float | None = None
        self._target_price:   float | None = None
        self._take_profit_pct: float | None = None

    @property
    def is_active(self) -> bool:
        return self._target_price is not None

    @property
    def target_price(self) -> float | None:
        return self._target_price

    def track_position(self, entry_price: float, take_profit_pct: float) -> None:
        self._entry_price     = entry_price
        self._take_profit_pct = take_profit_pct
        self._target_price    = entry_price * (1 + take_profit_pct / 100)
        logger.info(
            f"Take profit set: entry=${entry_price:,.2f} "
            f"+{take_profit_pct:.1f}% → target=${self._target_price:,.2f}"
        )

    def check_triggered(self, current_price: float) -> bool:
        return self.is_active and current_price >= self._target_price

    def clear_position(self) -> None:
        self._entry_price     = None
        self._target_price    = None
        self._take_profit_pct = None
        logger.info("Take profit tracker cleared.")


# ── Order Executor ─────────────────────────────────────────────────────────────

class Executor:
    def __init__(self):
        self.exchange       = get_exchange()
        self.trailing_stop  = TrailingStopManager()
        self.take_profit    = TakeProfitManager()
        self.short_manager  = ShortManager()

    def execute(self, decision: dict, portfolio: dict,
                atr: float | None = None, regime: str | None = None) -> dict:
        action   = decision['action']
        size_pct = decision.get('size_pct', 0)

        if action == 'HOLD' or size_pct == 0:
            logger.info("Tidak ada order — HOLD")
            return {"status": "hold"}

        try:
            if action == 'BUY':
                result = self._buy(size_pct, portfolio)
                if result.get('status') == 'success':
                    entry = portfolio.get('btc_price', 0)
                    if entry > 0:
                        if atr:
                            mult = _REGIME_ATR_MULT.get(regime, 2.0) if regime else 2.0
                            self.trailing_stop.track_position('BTC/USDT', entry, atr,
                                                              atr_multiplier=mult)
                        tp_pct = decision.get('take_profit_pct', 4.0)
                        self.take_profit.track_position(entry, tp_pct)
                return result
            elif action == 'SELL':
                result = self._sell(size_pct, portfolio)
                if result.get('status') == 'success':
                    self.trailing_stop.clear_position()
                    self.take_profit.clear_position()
                return result
            else:
                logger.warning(f"Action tidak dikenal: {action}")
                return {"status": "skipped", "reason": f"action tidak dikenal: {action}"}
        except Exception as e:
            logger.error(f"Error eksekusi order: {e}", exc_info=True)
            return {"status": "error", "message": str(e)}

    def execute_take_profit_sell(self, portfolio: dict) -> dict:
        """Sell 50% of BTC position when take-profit target is reached."""
        try:
            result = self._sell(50.0, portfolio)
            if result.get('status') == 'success':
                self.take_profit.clear_position()
            return result
        except Exception as e:
            logger.error(f"Take profit SELL gagal: {e}", exc_info=True)
            return {"status": "error", "message": str(e)}

    def execute_trailing_stop_sell(self, portfolio: dict) -> dict:
        """Force-sell 100% of BTC position when trailing stop is triggered."""
        try:
            result = self._sell(100.0, portfolio)
            if result.get('status') == 'success':
                self.trailing_stop.clear_position()
            return result
        except Exception as e:
            logger.error(f"Trailing stop SELL gagal: {e}", exc_info=True)
            return {"status": "error", "message": str(e)}

    def _buy(self, size_pct: float, portfolio: dict) -> dict:
        btc_price = portfolio.get('btc_price', 0)
        if btc_price <= 0:
            raise ValueError("Harga BTC tidak valid, tidak bisa eksekusi BUY")

        usdt_to_use = portfolio['usdt_available'] * (size_pct / 100)

        if usdt_to_use < MIN_USDT_ORDER:
            logger.warning(f"Order BUY terlalu kecil: ${usdt_to_use:.2f} (min ${MIN_USDT_ORDER})")
            return {"status": "skipped", "reason": f"order terlalu kecil (${usdt_to_use:.2f})"}

        btc_amount = round(usdt_to_use / btc_price, 6)
        if btc_amount < MIN_BTC_ORDER:
            logger.warning(f"BTC amount terlalu kecil: {btc_amount}")
            return {"status": "skipped", "reason": f"BTC amount terlalu kecil ({btc_amount})"}

        logger.info(f"BUY {btc_amount} BTC (${usdt_to_use:,.2f} USDT @ ${btc_price:,.2f})")
        order = self.exchange.create_market_buy_order('BTC/USDT', btc_amount)
        logger.info(f"Order BUY berhasil: {order['id']}")
        return {"status": "success", "order": order}

    def _sell(self, size_pct: float, portfolio: dict) -> dict:
        btc_to_sell = round(portfolio['btc_held'] * (size_pct / 100), 6)

        if btc_to_sell < MIN_BTC_ORDER:
            logger.warning(f"BTC to sell terlalu kecil: {btc_to_sell}")
            return {"status": "skipped", "reason": f"BTC amount terlalu kecil ({btc_to_sell})"}

        btc_price  = portfolio.get('btc_price', 0)
        usdt_value = btc_to_sell * btc_price
        if usdt_value < MIN_USDT_ORDER:
            logger.warning(f"Notional SELL terlalu kecil: ${usdt_value:.2f}")
            return {"status": "skipped", "reason": f"notional terlalu kecil (${usdt_value:.2f})"}

        logger.info(f"SELL {btc_to_sell} BTC (≈ ${usdt_value:,.2f} USDT)")
        order = self.exchange.create_market_sell_order('BTC/USDT', btc_to_sell)
        logger.info(f"Order SELL berhasil: {order['id']}")
        return {"status": "success", "order": order}
