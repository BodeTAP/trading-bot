"""
Standalone backtesting script.

Jalankan dengan:
    python backtest.py
    python backtest.py --capital 20000 --candles 800 --timeframe 4h
    python backtest.py --pair ETH/USDT --candles 500
"""

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("backtest")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Trading Bot Backtester",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Contoh:\n"
            "  python backtest.py\n"
            "  python backtest.py --exchange mainnet --candles 800 --timeframe 4h\n"
            "  python backtest.py --exchange testnet --candles 100 --warmup 40\n"
        ),
    )
    p.add_argument("--capital",   type=float, default=10_000,
                   help="Modal awal USDT (default: 10000)")
    p.add_argument("--pair",      type=str,   default="BTC/USDT",
                   help="Trading pair (default: BTC/USDT)")
    p.add_argument("--timeframe", type=str,   default="1h",
                   help="Timeframe: 15m/1h/4h/1d (default: 1h)")
    p.add_argument("--candles",   type=int,   default=500,
                   help="Jumlah candle historis (default: 500)")
    p.add_argument("--warmup",    type=int,   default=200,
                   help="Candle warmup untuk HMM training (default: 200)")
    p.add_argument("--exchange",  type=str,   default="testnet",
                   choices=["testnet", "mainnet"],
                   help="Sumber data: testnet (terbatas) atau mainnet/public (default: testnet)")
    p.add_argument("--no-chart",  action="store_true",
                   help="Skip menyimpan equity curve chart")
    return p.parse_args()


def print_results(result: dict) -> None:
    m      = result["metrics"]
    params = result["params"]
    period = result["period"]
    trades = result.get("trades", [])

    def _pct(v) -> str:
        if v is None:
            return "N/A"
        sign = "+" if v >= 0 else ""
        return f"{sign}{v:.2f}%"

    def _money(v) -> str:
        return f"${v:,.2f}"

    # ── separator helper ──────────────────────────────────────────────────────
    W = 52

    def sep(char="─"):
        print(char * W)

    def row(label, value, width=28):
        print(f"  {label:<{width}} {value}")

    print()
    sep("═")
    print(f"{'BACKTEST RESULTS':^{W}}")
    sep("═")

    print(f"\n  Pair      : {params['pair']}    Timeframe: {params['timeframe']}")
    print(f"  Modal awal: {_money(params['initial_capital'])}")
    print(f"  Periode   : {period['start'][:10]} → {period['end'][:10]}")
    print(f"  Candles   : {params['n_candles']}  (warmup: {params['warmup_candles']})")

    sep()
    print("  PERFORMANCE METRICS")
    sep()

    ret_pct = m.get("total_return_pct", 0)
    final   = m.get("final_capital", params["initial_capital"])
    ret_color = "\033[92m" if ret_pct >= 0 else "\033[91m"
    reset = "\033[0m"

    row("Total Return",    f"{ret_color}{_pct(ret_pct)}{reset}  ({_money(final)})")
    row("Win Rate",        f"{m.get('win_rate_pct', 0):.1f}%  ({m.get('sell_trades', 0)} sell trades)")
    row("Max Drawdown",    _pct(-abs(m.get("max_drawdown_pct", 0))))
    row("Sharpe Ratio",    f"{m.get('sharpe_ratio', 0):.3f}")
    row("Profit Factor",   f"{m.get('profit_factor', 'N/A')}" if m.get('profit_factor') is not None else "N/A")

    sep()
    print("  TRADING ACTIVITY")
    sep()

    row("Total Trades",    m.get("total_trades", 0))
    row("  BUY orders",    m.get("buy_trades", 0))
    row("  SELL orders",   m.get("sell_trades", 0))
    row("Total Fees",      _money(m.get("total_fees_usdt", 0)))

    sep()

    # Last 5 trades
    if trades:
        print("  LAST 5 TRADES")
        sep()
        print(f"  {'Timestamp':<22} {'Action':<6} {'Price':>10} {'BTC':>10} {'PnL':>10}")
        sep("·")
        for t in trades[-5:]:
            pnl_str = f"${t['pnl']:+.2f}" if t.get("pnl") is not None else "  —"
            print(
                f"  {t['timestamp'][:19]:<22} "
                f"{t['action']:<6} "
                f"${t['price']:>9,.0f} "
                f"{t['btc_amount']:>10.6f} "
                f"{pnl_str:>10}"
            )
        sep()

    print()


def save_equity_chart(result: dict, output_path: Path) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
        import pandas as pd

        equity     = result["equity_curve"]
        timestamps = pd.to_datetime(result["timestamps"])
        initial    = result["params"]["initial_capital"]

        if not equity or len(equity) != len(timestamps):
            logger.warning("Equity curve data tidak lengkap, skip chart.")
            return

        # Color equity line by profit/loss vs initial
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 8), height_ratios=[3, 1])
        fig.patch.set_facecolor("#1e1e2e")
        for ax in (ax1, ax2):
            ax.set_facecolor("#282a36")
            ax.tick_params(colors="#f8f8f2")
            ax.xaxis.label.set_color("#f8f8f2")
            ax.yaxis.label.set_color("#f8f8f2")
            for spine in ax.spines.values():
                spine.set_edgecolor("#44475a")

        # ── Equity curve ──────────────────────────────────────────────────────
        final      = equity[-1]
        line_color = "#50fa7b" if final >= initial else "#ff5555"

        ax1.plot(timestamps, equity, color=line_color, linewidth=1.5, label="Portfolio Value")
        ax1.axhline(initial, color="#f1fa8c", linewidth=0.8, linestyle="--", alpha=0.7,
                    label=f"Initial ${initial:,.0f}")
        ax1.fill_between(timestamps, equity, initial,
                         where=[v >= initial for v in equity],
                         alpha=0.15, color="#50fa7b", interpolate=True)
        ax1.fill_between(timestamps, equity, initial,
                         where=[v < initial for v in equity],
                         alpha=0.15, color="#ff5555", interpolate=True)

        # BUY/SELL markers
        trades = result.get("trades", [])
        ts_idx = {ts.isoformat(): i for i, ts in enumerate(timestamps)}

        for trade in trades:
            trade_ts = trade["timestamp"][:19]
            # find closest timestamp
            matches = [ts for ts in ts_idx if ts[:19] >= trade_ts]
            if not matches:
                continue
            closest = min(matches, key=lambda x: abs(
                pd.Timestamp(x) - pd.Timestamp(trade_ts)
            ))
            idx = ts_idx[closest]
            if idx < len(equity):
                color  = "#50fa7b" if trade["action"] == "BUY" else "#ff5555"
                marker = "^" if trade["action"] == "BUY" else "v"
                ax1.scatter(timestamps[idx], equity[idx], color=color,
                            marker=marker, s=60, zorder=5)

        ax1.set_ylabel("Portfolio Value (USDT)", color="#f8f8f2")
        ax1.legend(facecolor="#44475a", labelcolor="#f8f8f2", fontsize=9)
        ax1.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"${x:,.0f}"))
        ax1.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
        ax1.xaxis.set_major_locator(mdates.AutoDateLocator())

        total_ret = result["metrics"].get("total_return_pct", 0)
        ret_sign  = "+" if total_ret >= 0 else ""
        ax1.set_title(
            f"Equity Curve — {result['params']['pair']}  {result['params']['timeframe']}  "
            f"({ret_sign}{total_ret:.2f}%)",
            color="#f8f8f2", fontsize=12, pad=10,
        )

        # ── Drawdown subplot ──────────────────────────────────────────────────
        peak = equity[0]
        drawdowns = []
        for v in equity:
            if v > peak:
                peak = v
            drawdowns.append(-(peak - v) / peak * 100 if peak > 0 else 0)

        ax2.fill_between(timestamps, drawdowns, 0, color="#ff5555", alpha=0.4)
        ax2.plot(timestamps, drawdowns, color="#ff5555", linewidth=0.8)
        ax2.set_ylabel("Drawdown %", color="#f8f8f2")
        ax2.set_xlabel("Date", color="#f8f8f2")
        ax2.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"{x:.1f}%"))
        ax2.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
        ax2.xaxis.set_major_locator(mdates.AutoDateLocator())

        plt.tight_layout(h_pad=0.5)
        output_path.parent.mkdir(exist_ok=True)
        plt.savefig(output_path, dpi=120, bbox_inches="tight", facecolor=fig.get_facecolor())
        plt.close()
        print(f"  Equity curve tersimpan: {output_path}")

    except Exception as e:
        logger.warning(f"Gagal menyimpan chart: {e}")


def main() -> None:
    args = parse_args()

    use_mainnet = (args.exchange == "mainnet")
    source_label = "Binance mainnet (public, no API key)" if use_mainnet else "Binance testnet"

    from core.backtester import (Backtester, EQUITY_PNG, MIN_CANDLES, MIN_BACKTEST,
                                 MAX_WARMUP_PCT, N_BATCHES, BATCH_SIZE)

    # Peringatan dini jika warmup CLI terlihat terlalu besar vs candles
    max_warmup_hint = max(30, int(args.candles * MAX_WARMUP_PCT))
    if args.warmup > max_warmup_hint:
        print(f"  Info: --warmup ({args.warmup}) akan disesuaikan otomatis menjadi "
              f"maks {max_warmup_hint} ({MAX_WARMUP_PCT:.0%} dari {args.candles} candles).")

    print(f"\n  Memulai backtest: {args.pair} {args.timeframe}, "
          f"{args.candles} candles, modal ${args.capital:,.0f}")
    print(f"  Sumber data: {source_label}")
    if not use_mainnet:
        total_batch_candles = N_BATCHES * BATCH_SIZE
        print(f"  Fetching {total_batch_candles} candles dalam {N_BATCHES} batches "
              f"({BATCH_SIZE} candles/batch)...")

    try:
        bt     = Backtester(
            initial_capital = args.capital,
            pair            = args.pair,
            timeframe       = args.timeframe,
            n_candles       = args.candles,
            warmup_candles  = args.warmup,
            use_mainnet     = use_mainnet,
        )
        result = bt.run()
    except Exception as e:
        logger.error(f"Backtest gagal: {e}", exc_info=True)
        sys.exit(1)

    print_results(result)

    if not args.no_chart:
        save_equity_chart(result, EQUITY_PNG)

    print(f"  Hasil JSON tersimpan: logs/backtest_results.json\n")


if __name__ == "__main__":
    main()
