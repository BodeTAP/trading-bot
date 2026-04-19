"""
Rule-based backtester untuk strategi HMM + teknikal.

Simulasi trading yang ketat tanpa look-ahead bias:
  - Indikator teknikal (RSI, MA, MACD) bersifat causal — setiap nilai pada
    candle t hanya bergantung pada data t-window..t.
  - HMM states dihitung menggunakan forward_sequence() — single O(T·K²)
    forward pass. Nilai pada candle t hanya menggunakan data 0..t.
  - Eksekusi menggunakan harga close candle t + slippage (bukan open t+1)
    agar tidak butuh akses ke candle berikutnya.
  - Training HMM dilakukan pada warmup_candles pertama saja.
"""

import json
import logging
import math
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import SMAIndicator, MACD as MACDIndicator

from core.market_data import get_exchange
from core.hmm_classifier import HMMClassifier

logger = logging.getLogger(__name__)

LOGS_DIR        = Path("logs")
RESULTS_FILE    = LOGS_DIR / "backtest_results.json"
EQUITY_PNG      = LOGS_DIR / "equity_curve.png"

SLIPPAGE        = 0.001   # 0.1 % per side
FEE_RATE        = 0.001   # 0.1 % per trade (Binance spot taker)
MIN_CANDLES     = 50      # minimum candles untuk backtest valid
MIN_WARMUP      = 30      # minimum warmup HMM (sesuai MIN_ROWS di hmm_classifier)
MIN_BACKTEST    = 20      # minimum candles periode backtest (setelah warmup)
MAX_WARMUP_PCT  = 0.40    # warmup maksimal 40% dari total data
BUY_SIZE_PCT    = 0.15    # 15 % of available USDT per BUY
SELL_SIZE_PCT   = 0.50    # 50 % of held BTC per SELL


# ── Data fetching ─────────────────────────────────────────────────────────────

BATCH_SIZE   = 200   # candles per batch request
N_BATCHES    = 5     # number of batches for testnet pagination


def fetch_historical_batched(pair: str = "BTC/USDT",
                              timeframe: str = "1h",
                              n_batches: int = N_BATCHES,
                              batch_size: int = BATCH_SIZE) -> pd.DataFrame:
    """
    Fetch data historis dari Binance testnet dalam beberapa batch.

    Karena testnet membatasi ~100-200 candle per request, fungsi ini
    melakukan beberapa request menggunakan parameter 'since' untuk
    mengambil data lebih jauh ke masa lalu.
    """
    exchange = get_exchange()
    all_ohlcv: list[list] = []

    since: int | None = None  # mulai dari candle terbaru, mundur ke masa lalu

    for batch_num in range(1, n_batches + 1):
        kwargs: dict = {"limit": batch_size}
        if since is not None:
            kwargs["since"] = since

        ohlcv = exchange.fetch_ohlcv(pair, timeframe, **kwargs)
        if not ohlcv:
            logger.warning(f"Batch {batch_num}/{n_batches}: tidak ada data, berhenti.")
            break

        logger.info(f"Batch {batch_num}/{n_batches}: {len(ohlcv)} candles fetched "
                    f"(oldest: {pd.to_datetime(ohlcv[0][0], unit='ms').strftime('%Y-%m-%d %H:%M')})")

        all_ohlcv.extend(ohlcv)

        # Mulai batch berikutnya 1 ms sebelum candle tertua batch ini
        since = ohlcv[0][0] - 1

    if not all_ohlcv:
        raise ValueError(f"Data OHLCV kosong untuk {pair} {timeframe} (semua batch gagal)")

    df = pd.DataFrame(all_ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")

    # Hapus duplikat dan urutkan ascending
    df = df.drop_duplicates(subset="timestamp").sort_values("timestamp").reset_index(drop=True)

    logger.info(f"Total candles setelah dedup: {len(df)} "
                f"({df['timestamp'].iloc[0].strftime('%Y-%m-%d')} → "
                f"{df['timestamp'].iloc[-1].strftime('%Y-%m-%d')})")
    return df


def fetch_historical(pair: str = "BTC/USDT",
                     timeframe: str = "1h",
                     limit: int = 500,
                     use_mainnet: bool = False) -> pd.DataFrame:
    """
    Fetch OHLCV historis dari Binance.

    use_mainnet=False  → Binance testnet, batched fetch (N_BATCHES × BATCH_SIZE)
    use_mainnet=True   → Binance mainnet public endpoint (tanpa API key,
                         data historis jauh lebih banyak tersedia)
    """
    if use_mainnet:
        import ccxt
        exchange = ccxt.binance({
            "options": {"defaultType": "spot"},
        })
        ohlcv = exchange.fetch_ohlcv(pair, timeframe, limit=limit)
        if not ohlcv:
            raise ValueError(f"Data OHLCV kosong untuk {pair} {timeframe}")
        df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        return df.reset_index(drop=True)
    else:
        return fetch_historical_batched(pair, timeframe, n_batches=N_BATCHES, batch_size=BATCH_SIZE)


# ── Indicator computation (fully causal, pre-computed on full series) ─────────

def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["rsi"]        = RSIIndicator(df["close"], window=14).rsi()
    df["ma50"]       = SMAIndicator(df["close"], window=50).sma_indicator()
    df["ma200"]      = SMAIndicator(df["close"], window=200).sma_indicator()
    macd             = MACDIndicator(df["close"])
    df["macd"]       = macd.macd()
    df["macd_signal"]= macd.macd_signal()
    return df


# ── Rule-based signal (simulates Claude's logic) ──────────────────────────────

def _rule_signal(rsi: float, ma50: float, ma200: float | None,
                 hmm_state: str) -> str:
    """
    Three independent signals vote: RSI, MA crossover, HMM.
    Require ≥ 2 votes in same direction to execute; otherwise HOLD.
    """
    votes: list[str] = []

    # RSI
    if not math.isnan(rsi):
        if rsi < 35:
            votes.append("BUY")
        elif rsi > 65:
            votes.append("SELL")

    # MA crossover (only when MA200 is available)
    if ma200 is not None and not math.isnan(ma200) and not math.isnan(ma50):
        votes.append("BUY" if ma50 > ma200 else "SELL")

    # HMM regime
    if hmm_state in ("BULL", "EUPHORIA"):
        votes.append("BUY")
    elif hmm_state in ("CRASH", "BEAR"):
        votes.append("SELL")

    buy_votes  = votes.count("BUY")
    sell_votes = votes.count("SELL")

    if buy_votes >= 2:
        return "BUY"
    if sell_votes >= 2:
        return "SELL"
    return "HOLD"


# ── Portfolio state ───────────────────────────────────────────────────────────

class _Portfolio:
    def __init__(self, initial_capital: float):
        self.usdt       = initial_capital
        self.btc        = 0.0
        self.avg_cost   = 0.0   # weighted average buy price (USDT per BTC)
        self.trades: list[dict] = []
        self.equity_curve: list[float] = []

    def equity(self, price: float) -> float:
        return self.usdt + self.btc * price

    def buy(self, price: float, size_pct: float, timestamp: str) -> dict | None:
        exec_price = price * (1 + SLIPPAGE)
        usdt_spend = self.usdt * size_pct
        fee        = usdt_spend * FEE_RATE
        usdt_spend_net = usdt_spend - fee

        if usdt_spend_net < 10:
            return None

        btc_bought = usdt_spend_net / exec_price
        # Update weighted average cost
        total_btc = self.btc + btc_bought
        self.avg_cost = (self.avg_cost * self.btc + exec_price * btc_bought) / total_btc
        self.btc  += btc_bought
        self.usdt -= usdt_spend

        trade = {
            "timestamp": timestamp,
            "action": "BUY",
            "price": exec_price,
            "btc_amount": btc_bought,
            "usdt_amount": usdt_spend,
            "fee": fee,
            "pnl": None,
        }
        self.trades.append(trade)
        return trade

    def sell(self, price: float, size_pct: float, timestamp: str) -> dict | None:
        btc_sell = self.btc * size_pct
        exec_price = price * (1 - SLIPPAGE)

        if btc_sell < 0.00001:
            return None

        usdt_received = btc_sell * exec_price
        fee           = usdt_received * FEE_RATE
        usdt_net      = usdt_received - fee
        pnl           = (exec_price - self.avg_cost) * btc_sell - fee

        self.btc  -= btc_sell
        self.usdt += usdt_net

        trade = {
            "timestamp": timestamp,
            "action": "SELL",
            "price": exec_price,
            "btc_amount": btc_sell,
            "usdt_amount": usdt_net,
            "fee": fee,
            "pnl": pnl,
        }
        self.trades.append(trade)
        return trade


# ── Metrics ───────────────────────────────────────────────────────────────────

def _compute_metrics(portfolio: _Portfolio,
                     initial_capital: float,
                     timeframe: str) -> dict:
    equity = portfolio.equity_curve
    if not equity or initial_capital <= 0:
        return {}

    final        = equity[-1]
    total_return = (final - initial_capital) / initial_capital * 100

    # Max drawdown
    peak = equity[0]
    max_dd = 0.0
    for v in equity:
        if v > peak:
            peak = v
        dd = (peak - v) / peak * 100 if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd

    # Sharpe ratio (annualised)
    returns = np.diff(equity) / np.array(equity[:-1])
    periods_per_year = {"1m": 525600, "5m": 105120, "15m": 35040,
                        "1h": 8760, "4h": 2190, "1d": 365}.get(timeframe, 8760)
    if len(returns) > 1 and returns.std() > 0:
        sharpe = (returns.mean() / returns.std()) * math.sqrt(periods_per_year)
    else:
        sharpe = 0.0

    # Win rate + profit factor (from SELL trades only)
    sells = [t for t in portfolio.trades if t["action"] == "SELL" and t["pnl"] is not None]
    wins  = [t for t in sells if t["pnl"] > 0]
    gross_profit = sum(t["pnl"] for t in wins)
    gross_loss   = abs(sum(t["pnl"] for t in sells if t["pnl"] <= 0))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")
    win_rate_pct  = len(wins) / len(sells) * 100 if sells else 0.0

    total_trades = len(portfolio.trades)
    total_fees   = sum(t["fee"] for t in portfolio.trades)

    return {
        "total_return_pct": round(total_return, 3),
        "final_capital": round(final, 2),
        "win_rate_pct": round(win_rate_pct, 1),
        "max_drawdown_pct": round(max_dd, 3),
        "sharpe_ratio": round(sharpe, 3),
        "total_trades": total_trades,
        "buy_trades": sum(1 for t in portfolio.trades if t["action"] == "BUY"),
        "sell_trades": len(sells),
        "profit_factor": round(profit_factor, 3) if math.isfinite(profit_factor) else None,
        "total_fees_usdt": round(total_fees, 4),
    }


# ── Main backtest engine ──────────────────────────────────────────────────────

class Backtester:
    def __init__(self,
                 initial_capital: float = 10_000,
                 pair: str = "BTC/USDT",
                 timeframe: str = "1h",
                 n_candles: int = 500,
                 warmup_candles: int = 200,
                 use_mainnet: bool = False):
        self.initial_capital = initial_capital
        self.pair            = pair
        self.timeframe       = timeframe
        self.n_candles       = n_candles
        self.warmup_candles  = warmup_candles
        self.use_mainnet     = use_mainnet

    def run(self) -> dict:
        source = "Binance mainnet (public)" if self.use_mainnet else "Binance testnet"
        logger.info(f"Backtest: fetching {self.n_candles} candles dari {source} "
                    f"({self.pair} {self.timeframe})...")
        raw = fetch_historical(self.pair, self.timeframe, self.n_candles, self.use_mainnet)
        df  = add_indicators(raw)

        n_available = len(df)
        if n_available < MIN_CANDLES:
            raise ValueError(
                f"Data tidak cukup untuk backtesting: hanya {n_available} candles tersedia "
                f"(minimum {MIN_CANDLES}). Coba kurangi --candles atau gunakan --exchange mainnet "
                f"untuk data historis yang lebih banyak."
            )

        # Warmup tidak boleh melebihi MAX_WARMUP_PCT dari total data,
        # dan harus menyisakan minimal MIN_BACKTEST candles untuk periode backtest.
        max_allowed_warmup = max(MIN_WARMUP, int(n_available * MAX_WARMUP_PCT))
        warmup = min(self.warmup_candles, max_allowed_warmup)
        if warmup != self.warmup_candles:
            logger.warning(
                f"Warmup disesuaikan: {self.warmup_candles} -> {warmup} "
                f"(data={n_available}, max={MAX_WARMUP_PCT:.0%} dari total)."
            )

        backtest_period = n_available - warmup
        if backtest_period < MIN_BACKTEST:
            raise ValueError(
                f"Periode backtest terlalu pendek: {backtest_period} candles "
                f"(minimum {MIN_BACKTEST}). Data={n_available}, warmup={warmup}. "
                f"Gunakan --exchange mainnet untuk data lebih banyak."
            )

        logger.info(f"Data tersedia: {n_available} candles, warmup: {warmup} candles, "
                    f"backtest period: {n_available - warmup} candles")

        logger.info(f"Training HMM pada warmup ({warmup} candles)...")
        hmm       = HMMClassifier()
        warmup_df = df.iloc[:warmup]
        hmm.fit(warmup_df)

        # Single forward pass over full dataset — O(T·K²), strictly causal
        logger.info("Computing HMM states for all candles (single forward pass)...")
        all_states = hmm.predict_sequence(df)   # list[(label, conf)]

        portfolio = _Portfolio(self.initial_capital)

        # Walk-forward simulation — candle t: decision on t, execute at close[t]
        for t in range(warmup, len(df)):
            row       = df.iloc[t]
            hmm_label = all_states[t][0]

            rsi  = row["rsi"]
            ma50 = row["ma50"]
            ma200 = None if pd.isna(row.get("ma200", float("nan"))) else row["ma200"]
            price = row["close"]
            ts    = row["timestamp"].isoformat()

            if pd.isna(rsi) or pd.isna(ma50):
                portfolio.equity_curve.append(portfolio.equity(price))
                continue

            action = _rule_signal(rsi, ma50, ma200, hmm_label)

            if action == "BUY":
                portfolio.buy(price, BUY_SIZE_PCT, ts)
            elif action == "SELL":
                portfolio.sell(price, SELL_SIZE_PCT, ts)

            portfolio.equity_curve.append(portfolio.equity(price))

        metrics  = _compute_metrics(portfolio, self.initial_capital, self.timeframe)
        backtest_start = df.iloc[warmup]["timestamp"].isoformat()
        backtest_end   = df.iloc[-1]["timestamp"].isoformat()

        result = {
            "run_at": datetime.now().isoformat(),
            "params": {
                "initial_capital": self.initial_capital,
                "pair": self.pair,
                "timeframe": self.timeframe,
                "n_candles": self.n_candles,
                "warmup_candles": warmup,
                "data_source": "mainnet" if self.use_mainnet else "testnet",
            },
            "period": {"start": backtest_start, "end": backtest_end},
            "metrics": metrics,
            "equity_curve": [round(v, 2) for v in portfolio.equity_curve],
            "timestamps": [
                df.iloc[t]["timestamp"].isoformat()
                for t in range(warmup, len(df))
                if t - warmup < len(portfolio.equity_curve)
            ],
            "trades": portfolio.trades,
        }

        self.save_results(result)
        logger.info(f"Backtest selesai. Return: {metrics.get('total_return_pct', 0):.2f}%")
        return result

    @staticmethod
    def save_results(result: dict) -> None:
        LOGS_DIR.mkdir(exist_ok=True)
        with open(RESULTS_FILE, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        logger.info(f"Hasil disimpan ke {RESULTS_FILE}")

    @staticmethod
    def load_results() -> dict | None:
        if not RESULTS_FILE.exists():
            return None
        with open(RESULTS_FILE, encoding="utf-8") as f:
            return json.load(f)
