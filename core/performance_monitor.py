"""
Performance Monitor — evaluates rolling 7-day metrics every 6 hours and
auto-adjusts key .env parameters when performance degrades or improves.

Adjustment rules (priority order):
  1. Drawdown > 7%  → switch Conservative profile
  2. 3 consecutive losses → pause trading 2 hours
  3. Win rate < 40% for 3 consecutive days → more conservative params
  4. Win rate > 65% for 3 consecutive days → slightly more aggressive params
"""

import json
import logging
import threading
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from core.profile_manager import _apply_to_env, _read_env

logger = logging.getLogger(__name__)

_DECISIONS_LOG    = Path("logs/decisions.json")
_ADJUSTMENTS_LOG  = Path("logs/adjustments.log")
_STATE_FILE       = Path("logs/perf_state.json")

_CHECK_INTERVAL   = 6 * 3600   # 6 hours

# Safe bounds for auto-adjustments
_MIN_DRAWDOWN = 3.0
_MAX_DRAWDOWN = 15.0
_MIN_TRAILING = 1.0
_MAX_TRAILING = 4.0


# ── Data helpers ──────────────────────────────────────────────────────────────

def _load_decisions_df() -> pd.DataFrame:
    if not _DECISIONS_LOG.exists() or _DECISIONS_LOG.stat().st_size == 0:
        return pd.DataFrame()
    rows = []
    with open(_DECISIONS_LOG, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    return df.sort_values("timestamp").reset_index(drop=True)


def compute_metrics(df: pd.DataFrame) -> dict:
    """Compute rolling metrics from the last 7 days. Exported for dashboard use."""
    if df.empty:
        return {}

    cutoff = datetime.now() - timedelta(days=7)
    recent = df[df["timestamp"] >= cutoff].copy()
    if recent.empty:
        return {}

    # Win rate and per-trade profit
    trades = recent[recent["action"] != "HOLD"].copy()
    wins, total_counted, profits = 0, 0, []
    for idx in trades.index:
        later = df[df.index > idx]
        if later.empty:
            continue
        next_val = later.iloc[0]["total_value"]
        curr_val = df.loc[idx, "total_value"]
        delta_pct = (next_val - curr_val) / curr_val * 100 if curr_val > 0 else 0
        profits.append(delta_pct)
        if next_val > curr_val:
            wins += 1
        total_counted += 1

    win_rate  = (wins / total_counted * 100) if total_counted > 0 else 0.0
    avg_profit = sum(profits) / len(profits) if profits else 0.0

    # 7-day peak drawdown
    peak        = recent["total_value"].max()
    current     = recent["total_value"].iloc[-1]
    drawdown_pct = ((peak - current) / peak * 100) if peak > 0 else 0.0

    # Consecutive losses from latest non-HOLD decisions
    all_trades = df[df["action"] != "HOLD"].copy()
    consec = 0
    for i in range(len(all_trades) - 1, -1, -1):
        idx   = all_trades.index[i]
        later = df[df.index > idx]
        if later.empty:
            break
        if later.iloc[0]["total_value"] < df.loc[idx, "total_value"]:
            consec += 1
        else:
            break

    # Rolling Sharpe (annualized)
    daily_vals    = recent.groupby(recent["timestamp"].dt.date)["total_value"].last()
    daily_returns = daily_vals.pct_change().dropna()
    sharpe = 0.0
    if len(daily_returns) >= 2 and daily_returns.std() > 0:
        sharpe = round(daily_returns.mean() / daily_returns.std() * (365 ** 0.5), 3)

    return {
        "win_rate":           round(win_rate, 1),
        "avg_profit":         round(avg_profit, 3),
        "drawdown_pct":       round(drawdown_pct, 2),
        "consecutive_losses": consec,
        "sharpe":             sharpe,
        "total_trades_7d":    total_counted,
    }


def _compute_daily_win_rates(df: pd.DataFrame, days: int = 7) -> dict[str, float]:
    result = {}
    for d in range(days):
        day       = (datetime.now() - timedelta(days=d)).date()
        day_trades = df[(df["timestamp"].dt.date == day) & (df["action"] != "HOLD")]
        wins, total = 0, 0
        for idx in day_trades.index:
            later = df[df.index > idx]
            if later.empty:
                continue
            if later.iloc[0]["total_value"] > df.loc[idx, "total_value"]:
                wins += 1
            total += 1
        if total > 0:
            result[str(day)] = round(wins / total * 100, 1)
    return result


# ── Main class ────────────────────────────────────────────────────────────────

class PerformanceMonitor:

    def __init__(self, notifier, pause_callback=None):
        """
        notifier       — TelegramNotifier instance
        pause_callback — callable(minutes: int) to pause bot trading
        """
        self._notifier       = notifier
        self._pause_callback = pause_callback
        self._stop_event     = threading.Event()
        self._thread: threading.Thread | None = None
        Path("logs").mkdir(exist_ok=True)

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self) -> None:
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop, name="perf-monitor", daemon=True
        )
        self._thread.start()
        logger.info("PerformanceMonitor: background thread dimulai (interval 6 jam)")

    def stop(self) -> None:
        self._stop_event.set()

    def get_state(self) -> dict:
        return self._load_state()

    def set_auto_adjust(self, enabled: bool) -> None:
        state = self._load_state()
        state["auto_adjust_enabled"] = enabled
        self._save_state(state)

    # ── Loop ─────────────────────────────────────────────────────────────────

    def _loop(self) -> None:
        self._run_check()
        while not self._stop_event.wait(_CHECK_INTERVAL):
            self._run_check()

    def _run_check(self) -> None:
        try:
            df = _load_decisions_df()
            if df.empty:
                return
            metrics    = compute_metrics(df)
            daily_wr   = _compute_daily_win_rates(df)
            state      = self._load_state()
            state["metrics"]    = metrics
            state["daily_wr"]   = daily_wr
            state["last_check"] = datetime.now().isoformat()
            self._save_state(state)

            if state.get("auto_adjust_enabled", True):
                self._apply_rules(metrics, daily_wr, state)
        except Exception as e:
            logger.error(f"PerformanceMonitor._run_check error: {e}", exc_info=True)

    # ── Rules ────────────────────────────────────────────────────────────────

    def _apply_rules(self, metrics: dict, daily_wr: dict, state: dict) -> None:
        if not metrics:
            return

        env      = _read_env()
        max_dd   = float(env.get("MAX_DRAWDOWN_PCT", 10))
        trailing = float(env.get("TRAILING_STOP_ATR_MULTIPLIER", 2.0))

        sorted_days = sorted(daily_wr.keys(), reverse=True)
        last3       = [daily_wr[d] for d in sorted_days[:3]] if len(sorted_days) >= 3 else []

        # Rule d: drawdown > 7% → switch Conservative
        if metrics.get("drawdown_pct", 0) > 7.0:
            try:
                from core.profile_manager import load_profile
                load_profile("conservative")
                self._notify_and_log(
                    f"⚠️ <b>Drawdown mencapai {metrics['drawdown_pct']:.1f}%.</b>\n"
                    f"Switching otomatis ke profile <b>Conservative</b>.\n"
                    f"Pantau posisi dengan hati-hati.",
                    "SWITCH_CONSERVATIVE", metrics,
                )
            except Exception as e:
                logger.warning(f"Gagal load profile conservative: {e}")
            return

        # Rule c: 3 consecutive losses → pause 2 hours
        if metrics.get("consecutive_losses", 0) >= 3:
            if self._pause_callback:
                self._pause_callback(120)
            self._notify_and_log(
                f"🔴 <b>3 loss berturut-turut terdeteksi.</b>\n"
                f"Trading di-pause 2 jam untuk evaluasi.\n"
                f"Win rate 7 hari: {metrics.get('win_rate', 0):.1f}%",
                "PAUSE_CONSEC_LOSS", metrics,
            )
            return

        # Rule a: win rate < 40% for 3 consecutive days → less aggressive
        if len(last3) == 3 and all(wr < 40.0 for wr in last3):
            new_dd       = max(_MIN_DRAWDOWN, max_dd - 2)
            new_trailing = min(_MAX_TRAILING, trailing + 0.5)
            if new_dd != max_dd or abs(new_trailing - trailing) > 0.01:
                _apply_to_env({
                    "MAX_DRAWDOWN_PCT":             str(new_dd),
                    "TRAILING_STOP_ATR_MULTIPLIER": f"{new_trailing:.1f}",
                })
                avg_wr = sum(last3) / len(last3)
                self._notify_and_log(
                    f"⚠️ <b>Win rate turun ke {avg_wr:.1f}%</b> (3 hari berturut-turut).\n"
                    f"Parameter disesuaikan ke lebih konservatif:\n"
                    f"  • MAX_DRAWDOWN: {max_dd:.0f}% → {new_dd:.0f}%\n"
                    f"  • TRAILING_STOP: {trailing:.1f} → {new_trailing:.1f}",
                    "REDUCE_AGGRESSIVE", metrics,
                )
            return

        # Rule b: win rate > 65% for 3 consecutive days → slightly more aggressive
        if len(last3) == 3 and all(wr > 65.0 for wr in last3):
            new_dd       = min(_MAX_DRAWDOWN, max_dd + 1)
            new_trailing = max(_MIN_TRAILING, trailing - 0.2)
            if abs(new_dd - max_dd) > 0.01 or abs(new_trailing - trailing) > 0.01:
                _apply_to_env({
                    "MAX_DRAWDOWN_PCT":             str(new_dd),
                    "TRAILING_STOP_ATR_MULTIPLIER": f"{new_trailing:.1f}",
                })
                avg_wr = sum(last3) / len(last3)
                self._notify_and_log(
                    f"✅ <b>Performa bagus! Win rate {avg_wr:.1f}%</b> (3 hari berturut-turut).\n"
                    f"Parameter sedikit dinaikkan:\n"
                    f"  • MAX_DRAWDOWN: {max_dd:.0f}% → {new_dd:.0f}%\n"
                    f"  • TRAILING_STOP: {trailing:.1f} → {new_trailing:.1f}",
                    "INCREASE_AGGRESSIVE", metrics,
                )

    def _notify_and_log(self, msg: str, reason: str, metrics: dict) -> None:
        self._notifier._send(msg)
        entry = {
            "timestamp": datetime.now().isoformat(),
            "reason":    reason,
            "metrics":   metrics,
            "message":   msg.replace("<b>", "").replace("</b>", ""),
        }
        _ADJUSTMENTS_LOG.parent.mkdir(exist_ok=True)
        with open(_ADJUSTMENTS_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
        state = self._load_state()
        state["last_adjustment"]        = entry["timestamp"]
        state["last_adjustment_reason"] = entry["message"]
        self._save_state(state)
        logger.info(f"PerformanceMonitor: {reason}")

    # ── State I/O ─────────────────────────────────────────────────────────────

    def _load_state(self) -> dict:
        if _STATE_FILE.exists():
            try:
                return json.loads(_STATE_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {"auto_adjust_enabled": True}

    def _save_state(self, state: dict) -> None:
        _STATE_FILE.write_text(
            json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8"
        )
