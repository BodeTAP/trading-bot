import time
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path
import pandas as pd
from dotenv import load_dotenv
import os
from core.market_data import fetch_multi_timeframe, get_portfolio_status, format_context_for_claude
from core.claude_brain import ask_claude
from core.risk_manager import RiskManager
from core.executor import Executor
from core.telegram_notifier import TelegramNotifier, TelegramCommandHandler
from core.hmm_classifier import HMMClassifier
from core.sentiment import SentimentFetcher
from core.ensemble import EnsembleSignal
from core.regime_detector import RegimeDetector
from core.performance_monitor import PerformanceMonitor
from core.log_cleaner import LogCleaner
from core.health_check import HealthChecker

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s — %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('logs/bot.log', encoding='utf-8'),
    ]
)
logger = logging.getLogger('trading-bot')

REQUIRED_ENV = ['BINANCE_API_KEY', 'BINANCE_SECRET_KEY', 'ANTHROPIC_API_KEY']
PAIR      = os.getenv('TRADING_PAIR', 'BTC/USDT')

TF_SHORT  = os.getenv('TIMEFRAME_SHORT',  '15m')
TF_MEDIUM = os.getenv('TIMEFRAME_MEDIUM', '1h')
TF_LONG   = os.getenv('TIMEFRAME_LONG',   '4h')

_TF_TO_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}
INTERVAL_SECONDS = int(os.getenv('INTERVAL_SECONDS') or _TF_TO_SECONDS.get(TF_SHORT, 3600))


def _validate_env():
    missing = [k for k in REQUIRED_ENV if not os.getenv(k)]
    if missing:
        logger.critical(f"Environment variable wajib tidak ditemukan: {missing}")
        sys.exit(1)


class BotState:
    """Shared mutable state between main loop and Telegram command callbacks."""
    def __init__(self):
        self.trading_enabled: bool          = True
        self.pause_until: datetime | None   = None
        self.portfolio: dict | None         = None
        self.regime_result: dict | None     = None
        self.ensemble_result: dict | None   = None
        self.sentiment: dict | None         = None
        self.hmm_state: str | None          = None
        self.hmm_confidence: float | None   = None
        self.next_session_at: datetime | None = None
        self.df: pd.DataFrame | None        = None

    def is_paused(self) -> bool:
        if self.pause_until and datetime.now() < self.pause_until:
            return True
        if self.pause_until and datetime.now() >= self.pause_until:
            self.pause_until = None  # auto-resume
        return False


def _extract_atr(df: pd.DataFrame) -> tuple[float | None, float | None]:
    """Return (current_atr, avg_atr_14) from the medium-TF dataframe."""
    if 'atr' not in df.columns:
        return None, None
    atr_series = df['atr'].dropna()
    if atr_series.empty:
        return None, None
    current_atr = float(atr_series.iloc[-1])
    avg_atr     = float(atr_series.tail(14).mean())
    return current_atr, avg_atr


def run_bot():
    _validate_env()

    logger.info("=" * 50)
    logger.info("Trading Bot Started")
    logger.info(f"Pair: {PAIR} | Timeframes: {TF_SHORT}/{TF_MEDIUM}/{TF_LONG} | Interval: {INTERVAL_SECONDS}s")
    logger.info("=" * 50)

    risk_manager      = RiskManager()
    executor          = Executor()
    notifier          = TelegramNotifier()
    hmm               = HMMClassifier()
    sentiment_fetcher = SentimentFetcher()
    ensemble          = EnsembleSignal()
    regime_detector   = RegimeDetector()
    previous_regime:  str | None = None
    state             = BotState()

    # ── Telegram command callbacks ─────────────────────────────────────────────
    def _cmd_status(*_):
        from core.profile_manager import get_active_profile

        p      = state.portfolio
        r      = state.regime_result
        e      = state.ensemble_result
        s      = state.sentiment
        paused = state.is_paused()

        # ── Bot status line ───────────────────────────────────────────────────
        if paused:
            bot_line = f"⏸ Dijeda hingga {state.pause_until.strftime('%H:%M:%S')}"
        elif state.trading_enabled:
            bot_line = "✅ Running"
        else:
            bot_line = "🛑 Stopped"

        # ── Profile ───────────────────────────────────────────────────────────
        try:
            profile_name = get_active_profile() or "Custom"
        except Exception:
            profile_name = "Custom"

        # ── Portfolio with % change from starting balance ─────────────────────
        total = p.get("total_value_usdt", 0) if p else 0
        btc_price = p.get("btc_price", 0) if p else 0
        if risk_manager.starting_balance and risk_manager.starting_balance > 0:
            pct_change = (total - risk_manager.starting_balance) / risk_manager.starting_balance * 100
            pct_str    = f"{pct_change:+.1f}% dari awal"
        else:
            pct_str = "—"

        # ── BTC price change (last candle close vs previous) ──────────────────
        btc_chg_str = ""
        if state.df is not None and "close" in state.df.columns:
            closes = state.df["close"].dropna()
            if len(closes) >= 2:
                prev_close  = float(closes.iloc[-2])
                last_close  = float(closes.iloc[-1])
                btc_chg_pct = (last_close - prev_close) / prev_close * 100 if prev_close else 0
                btc_chg_str = f" ({btc_chg_pct:+.1f}% 1h)"

        # ── Trailing stop ─────────────────────────────────────────────────────
        ts = executor.trailing_stop
        if ts.is_active:
            stop_price = ts.get_current_stop()
            dist_pct   = ts.stop_distance_pct(btc_price) if btc_price else None
            dist_str   = f" (jarak: {dist_pct:.1f}%)" if dist_pct is not None else ""
            ts_line    = f"🔒 Trailing Stop: <b>${stop_price:,.0f}</b>{dist_str}"
        else:
            ts_line = "🔒 Trailing Stop: tidak aktif"

        # ── Next session countdown ────────────────────────────────────────────
        if state.next_session_at:
            secs_left = max(0, (state.next_session_at - datetime.now()).total_seconds())
            if secs_left >= 60:
                next_str = f"{int(secs_left // 60)} menit lagi"
            else:
                next_str = f"{int(secs_left)} detik lagi"
        else:
            next_str = "—"

        # ── Ensemble ─────────────────────────────────────────────────────────
        ens_line = ""
        if e:
            ens_line = f"🧠 Ensemble: <b>{e['signal']}</b> ({e.get('consensus','?')})"

        # ── Regime ───────────────────────────────────────────────────────────
        regime_line = ""
        if r:
            conf_pct = f"{r['confidence']:.0%}" if r.get("confidence") is not None else "?"
            regime_line = f"📊 Regime: <b>{r['regime']}</b> ({conf_pct})"

        # ── Fear & Greed ──────────────────────────────────────────────────────
        fg_line = ""
        if s:
            fg_line = f"😨 Fear &amp; Greed: {s['current_value']} — {s['current_label']}"

        lines = [
            f"✅ Bot: <b>{bot_line}</b>",
            f"📋 Profile: <b>{profile_name}</b>",
            f"💰 Portfolio: <b>${total:,.0f}</b> ({pct_str})",
        ]
        if regime_line:
            lines.append(regime_line)
        if ens_line:
            lines.append(ens_line)
        if fg_line:
            lines.append(fg_line)
        lines.append(
            f"📈 BTC: <b>${btc_price:,.0f}</b>{btc_chg_str}"
        )
        lines.append(ts_line)
        lines.append(f"⏱ Sesi berikutnya: {next_str}")

        notifier._send("\n".join(lines))

    def _cmd_stop(*_):
        state.trading_enabled = False
        notifier._send("🛑 <b>Trading dihentikan.</b>\nBot akan tetap berjalan (analisis + log) tapi tidak akan eksekusi order.\nGunakan /start untuk mengaktifkan kembali.")

    def _cmd_start(*_):
        state.trading_enabled = True
        state.pause_until = None
        notifier._send("✅ <b>Trading diaktifkan kembali.</b>")

    def _cmd_balance(*_):
        try:
            p = get_portfolio_status()
            state.portfolio = p
            notifier._send(
                f"💰 <b>Balance (Live)</b>\n\n"
                f"  • Total: <b>${p['total_value_usdt']:,.2f}</b>\n"
                f"  • USDT: ${p['usdt_available']:,.2f}\n"
                f"  • BTC: {p['btc_held']:.6f} BTC\n"
                f"  • Harga BTC: ${p['btc_price']:,.2f}"
            )
        except Exception as e:
            notifier._send(f"❌ Gagal mengambil balance: {e}")

    def _cmd_pause(*args):
        minutes = 30
        if args:
            try:
                minutes = max(1, int(args[0]))
            except ValueError:
                pass
        state.pause_until = datetime.now() + timedelta(minutes=minutes)
        notifier._send(
            f"⏸ <b>Trading dijeda selama {minutes} menit.</b>\n"
            f"Resume otomatis pukul {state.pause_until.strftime('%H:%M:%S')}.\n"
            "Gunakan /start untuk resume lebih awal."
        )

    def _cmd_trades(*_):
        from pathlib import Path
        import json as _json
        log_file = Path('logs/decisions.json')
        if not log_file.exists():
            notifier._send("📋 Belum ada history trade.")
            return
        entries = []
        with open(log_file, encoding='utf-8') as f:
            for line in f:
                try:
                    entries.append(_json.loads(line))
                except Exception:
                    pass
        last5 = entries[-5:]
        if not last5:
            notifier._send("📋 Belum ada history trade.")
            return
        lines = ["📋 <b>5 Keputusan Terakhir</b>", ""]
        for e in reversed(last5):
            ts    = e.get('timestamp', '')[:16].replace('T', ' ')
            act   = e.get('action', '?')
            conf  = e.get('confidence', '?')
            emoji = {'BUY': '🟢', 'SELL': '🔴', 'HOLD': '🟡'}.get(act, '⚪')
            lines.append(f"{emoji} <b>{act}</b> [{conf}] — {ts}")
            lines.append(f"   ${e.get('total_value', 0):,.0f} | {e.get('reason', '')[:60]}")
        notifier._send("\n".join(lines))

    def _cmd_help(*_):
        notifier._send(
            "🤖 <b>Perintah Bot Trading</b>\n\n"
            "/status — status bot &amp; portfolio saat ini\n"
            "/balance — ambil balance live dari Binance\n"
            "/pause [menit] — jeda eksekusi (default 30 menit)\n"
            "/start — aktifkan kembali trading\n"
            "/stop — hentikan eksekusi (analisis tetap jalan)\n"
            "/trades — 5 keputusan trading terakhir\n"
            "/help — tampilkan daftar perintah ini"
        )

    cmd_handler = TelegramCommandHandler(notifier)
    cmd_handler.register("status",  _cmd_status)
    cmd_handler.register("stop",    _cmd_stop)
    cmd_handler.register("start",   _cmd_start)
    cmd_handler.register("balance", _cmd_balance)
    cmd_handler.register("pause",   _cmd_pause)
    cmd_handler.register("trades",  _cmd_trades)
    cmd_handler.register("help",    _cmd_help)
    cmd_handler.start()

    # ── Background maintenance services ───────────────────────────────────────
    def _pause_trading_callback(minutes: int) -> None:
        state.pause_until = datetime.now() + timedelta(minutes=minutes)
        logger.warning(f"PerformanceMonitor: trading di-pause {minutes} menit otomatis")

    perf_monitor  = PerformanceMonitor(notifier, pause_callback=_pause_trading_callback)
    log_cleaner   = LogCleaner(notifier)
    health_checker = HealthChecker(notifier)

    perf_monitor.start()
    log_cleaner.start()
    health_checker.start()

    try:
        initial_portfolio = get_portfolio_status()
        risk_manager.set_starting_balance(initial_portfolio['total_value_usdt'])
    except Exception as e:
        logger.critical(f"Gagal mengambil portfolio awal: {e}")
        sys.exit(1)

    notifier.notify_startup(PAIR, TF_MEDIUM, initial_portfolio['total_value_usdt'])

    while True:
        try:
            # ── 1. Trailing stop check (before fetching new data) ─────────────
            if executor.trailing_stop.is_active:
                try:
                    portfolio_ts = get_portfolio_status()
                    current_price = portfolio_ts['btc_price']
                    stop_price    = executor.trailing_stop.get_current_stop()
                    dist_pct      = executor.trailing_stop.stop_distance_pct(current_price)
                    logger.info(
                        f"Trailing stop aktif: harga=${current_price:,.2f} | "
                        f"stop=${stop_price:,.2f} | jarak={dist_pct:.2f}%"
                    )
                    if executor.trailing_stop.update_stop(current_price):
                        logger.warning(
                            f"Trailing stop terkena di ${current_price:,.2f} "
                            f"(stop=${stop_price:,.2f}) — eksekusi SELL"
                        )
                        ts_result = executor.execute_trailing_stop_sell(portfolio_ts)
                        if ts_result.get('status') == 'success':
                            notifier._send(
                                f"🛑 <b>Trailing Stop Terkena</b>\n\n"
                                f"Harga: <b>${current_price:,.2f}</b>\n"
                                f"Stop:  <b>${stop_price:,.2f}</b>\n"
                                f"Seluruh posisi BTC dijual otomatis."
                            )
                        else:
                            notifier.notify_error(
                                "Trailing stop SELL",
                                Exception(ts_result.get('message', 'unknown'))
                            )
                        logger.info("Trailing stop selesai — lanjut ke sesi normal.")
                except Exception as e:
                    logger.warning(f"Trailing stop check gagal: {e}")

            # ── 1b. Take-profit check ─────────────────────────────────────────
            if executor.take_profit.is_active:
                try:
                    portfolio_tp  = get_portfolio_status()
                    current_price = portfolio_tp['btc_price']
                    target_price  = executor.take_profit.target_price
                    logger.info(
                        f"Take profit aktif: harga=${current_price:,.2f} | "
                        f"target=${target_price:,.2f}"
                    )
                    if executor.take_profit.check_triggered(current_price):
                        logger.info(
                            f"Take profit terkena di ${current_price:,.2f} — eksekusi SELL 50%"
                        )
                        tp_result = executor.execute_take_profit_sell(portfolio_tp)
                        if tp_result.get('status') == 'success':
                            notifier._send(
                                f"✅ <b>Take Profit Terkena</b>\n\n"
                                f"Harga : <b>${current_price:,.2f}</b>\n"
                                f"Target: <b>${target_price:,.2f}</b>\n"
                                f"50% posisi BTC dijual. "
                                f"Trailing stop tetap aktif untuk sisa posisi."
                            )
                        else:
                            notifier.notify_error(
                                "Take profit SELL",
                                Exception(tp_result.get('message', 'unknown'))
                            )
                except Exception as e:
                    logger.warning(f"Take profit check gagal: {e}")

            # ── 2. Market data ────────────────────────────────────────────────
            logger.info("Fetching multi-timeframe data...")
            tf_data    = fetch_multi_timeframe(PAIR)
            _df_medium = tf_data.get(TF_MEDIUM)
            df         = _df_medium if _df_medium is not None else next(iter(tf_data.values()))
            portfolio  = get_portfolio_status()
            state.portfolio = portfolio
            state.df        = df

            current_atr, avg_atr = _extract_atr(df)
            if current_atr:
                logger.info(f"ATR(14): {current_atr:.2f} | avg_ATR(14): {avg_atr:.2f}")

            if risk_manager.check_circuit_breaker(portfolio['total_value_usdt']):
                logger.critical("Bot berhenti karena circuit breaker aktif.")
                notifier.notify_circuit_breaker(
                    current_balance=portfolio['total_value_usdt'],
                    starting_balance=risk_manager.starting_balance,
                    max_drawdown_pct=risk_manager.max_drawdown_pct,
                )
                notifier.notify_shutdown("Circuit breaker aktif")
                break

            # ── 3. HMM ────────────────────────────────────────────────────────
            hmm_state      = None
            hmm_confidence = None
            hmm_bias       = None
            hmm_tf_states: dict[str, str] = {}

            try:
                hmm_tf_states = hmm.fit_and_predict_multi(tf_data)
                if hmm_tf_states:
                    hmm_state = hmm_tf_states.get(TF_MEDIUM)
                    if hmm.is_fitted:
                        _, hmm_confidence = hmm.predict(df)
                    hmm_bias = HMMClassifier.get_trading_bias(hmm_state) if hmm_state else None
                    logger.info(
                        f"HMM states — "
                        + " | ".join(f"{tf}: {s}" for tf, s in hmm_tf_states.items())
                        + (f" | confidence: {hmm_confidence * 100:.0f}%" if hmm_confidence else "")
                    )
            except Exception as e:
                logger.warning(f"HMM predict gagal: {e}")

            # Update shared state for command callbacks
            state.hmm_state      = hmm_state
            state.hmm_confidence = hmm_confidence

            # ── 4. Regime detection ───────────────────────────────────────────
            regime_result: dict | None = None
            try:
                df_4h = tf_data.get(TF_LONG)
                regime_result = regime_detector.detect(df, df_4h)
                regime_name   = regime_result["regime"]
                logger.info(
                    f"Regime: {regime_name} ({regime_result['confidence']:.0%}) | "
                    f"ADX={regime_result.get('adx')} | "
                    f"{regime_result['strategy_hint']}"
                )
                # Notify Telegram when regime changes
                state.regime_result = regime_result
                if previous_regime is not None and previous_regime != regime_name:
                    notifier._send(
                        f"🔄 <b>Regime Pasar Berubah</b>\n\n"
                        f"Sebelumnya : <b>{previous_regime}</b>\n"
                        f"Sekarang   : <b>{regime_name}</b> "
                        f"({regime_result['confidence']:.0%})\n\n"
                        f"📋 Strategi: {regime_result['strategy_hint']}"
                    )
                previous_regime = regime_name
            except Exception as e:
                logger.warning(f"Regime detection gagal: {e}")

            # ── 6. Ensemble signal ────────────────────────────────────────────
            ensemble_result: dict | None = None
            try:
                from core.market_data import _tf_bias
                tf_biases = {tf: _tf_bias(df_) for tf, df_ in tf_data.items()}
                ensemble_result = ensemble.compute(
                    df_1h          = df,
                    multi_tf       = tf_data,
                    hmm_state      = hmm_state,
                    hmm_confidence = hmm_confidence,
                    tf_biases      = tf_biases,
                )
                logger.info(
                    f"Ensemble: {ensemble_result['signal']} "
                    f"(score={ensemble_result['score']:+.3f}, "
                    f"consensus={ensemble_result['consensus']})"
                )
                state.ensemble_result = ensemble_result
            except Exception as e:
                logger.warning(f"Ensemble compute gagal: {e}")

            # ── 7. Fear & Greed ───────────────────────────────────────────────
            sentiment = sentiment_fetcher.fetch()
            state.sentiment = sentiment
            if sentiment:
                logger.info(
                    f"Fear & Greed: {sentiment['current_value']} — "
                    f"{sentiment['current_label']} | Tren: {sentiment['trend_7d']}"
                )
            else:
                logger.warning("Fear & Greed Index tidak tersedia, lanjut tanpa sentimen.")

            # ── 5. Build context → ask Claude ─────────────────────────────────
            logger.info("Mengirim data ke Claude...")
            context, confluence_text = format_context_for_claude(
                df, portfolio,
                hmm_state=hmm_state,
                hmm_confidence=hmm_confidence,
                hmm_bias=hmm_bias,
                multi_tf=tf_data,
                hmm_tf_states=hmm_tf_states if hmm_tf_states else None,
                sentiment=sentiment,
                ensemble_result=ensemble_result,
                regime_result=regime_result,
            )

            if confluence_text:
                logger.info(f"Konfluensi multi-timeframe: {confluence_text}")

            decision = ask_claude(context)
            logger.info(f"Claude memutuskan: {decision['action']} (confidence: {decision['confidence']})")

            # Attach metadata to decision for logging + position sizer
            if hmm_state is not None:
                decision["market_state"]   = hmm_state
                decision["hmm_confidence"] = hmm_confidence
            if hmm_tf_states:
                decision["tf_15m_state"] = hmm_tf_states.get(TF_SHORT, "")
                decision["tf_1h_state"]  = hmm_tf_states.get(TF_MEDIUM, "")
                decision["tf_4h_state"]  = hmm_tf_states.get(TF_LONG, "")
            if confluence_text:
                decision["confluence"] = confluence_text
            if sentiment:
                decision["fear_greed_value"] = sentiment["current_value"]
                decision["fear_greed_label"] = sentiment["current_label"]
            if ensemble_result:
                decision["ensemble_signal"]    = ensemble_result["signal"]
                decision["ensemble_score"]     = ensemble_result["score"]
                decision["ensemble_consensus"] = ensemble_result["consensus"]
            if regime_result:
                decision["regime"]             = regime_result["regime"]
                decision["regime_confidence"]  = regime_result["confidence"]

            # ── 9. Validate + dynamic sizing ──────────────────────────────────
            decision = risk_manager.validate_decision(
                decision, portfolio,
                atr=current_atr,
                avg_atr=avg_atr,
            )
            risk_manager.log_decision(decision, portfolio)

            notifier.notify_decision(decision, portfolio)

            # ── 10. Execute (respect trading_enabled / pause) ─────────────────
            if not state.trading_enabled:
                logger.info("Trading dinonaktifkan via /stop — eksekusi dilewati.")
            elif state.is_paused():
                resume = state.pause_until.strftime('%H:%M:%S')
                logger.info(f"Trading dijeda hingga {resume} — eksekusi dilewati.")
            else:
                # ── SHORT open/close (futures mode only) ──────────────────────
                if executor.short_manager.is_enabled:
                    if decision['action'] == 'SHORT':
                        if executor.short_manager.is_active:
                            executor.short_manager.close_short(portfolio)
                        short_result = executor.short_manager.open_short(
                            PAIR, decision.get('size_pct', 10), portfolio
                        )
                        if short_result.get('status') == 'success':
                            notifier._send(
                                f"📉 <b>SHORT Dibuka</b>\n"
                                f"Harga: <b>${portfolio.get('btc_price', 0):,.2f}</b>\n"
                                f"Alasan: {decision.get('reason', '')[:80]}"
                            )
                    elif decision['action'] in ('BUY',) and executor.short_manager.is_active:
                        executor.short_manager.close_short(portfolio)
                        notifier._send("📈 <b>SHORT Ditutup</b> — sinyal BUY terdeteksi")

                # ── Regular BUY/SELL/HOLD ──────────────────────────────────────
                if decision['action'] != 'SHORT':
                    result = executor.execute(
                        decision, portfolio,
                        atr=current_atr,
                        regime=regime_result["regime"] if regime_result else None,
                    )
                    if result.get('status') == 'error':
                        msg = result.get('message', 'unknown')
                        logger.error(f"Eksekusi order gagal: {msg}")
                        notifier.notify_error("Eksekusi order", Exception(msg))

            state.next_session_at = datetime.now() + timedelta(seconds=INTERVAL_SECONDS)
            logger.info(f"Tidur {INTERVAL_SECONDS // 60} menit hingga sesi berikutnya...")
            time.sleep(INTERVAL_SECONDS)

        except KeyboardInterrupt:
            logger.info("Bot dihentikan manual.")
            cmd_handler.stop()
            perf_monitor.stop()
            log_cleaner.stop()
            health_checker.stop()
            notifier.notify_shutdown("Dihentikan manual (KeyboardInterrupt)")
            break
        except Exception as e:
            logger.error(f"Error tidak terduga: {e}", exc_info=True)
            notifier.notify_error("Loop utama bot", e)
            logger.info("Coba lagi dalam 60 detik...")
            time.sleep(60)


def _write_crash_log(exc: BaseException) -> None:
    import traceback
    Path("logs").mkdir(exist_ok=True)
    entry = (
        f"\n{'='*60}\n"
        f"CRASH — {datetime.now().isoformat()}\n"
        f"{traceback.format_exc()}\n"
    )
    with open("logs/crash.log", "a", encoding="utf-8") as f:
        f.write(entry)


if __name__ == "__main__":
    try:
        run_bot()
        sys.exit(0)
    except KeyboardInterrupt:
        sys.exit(0)
    except Exception as exc:
        logger.critical(f"Unhandled exception — bot crash: {exc}", exc_info=True)
        _write_crash_log(exc)
        sys.exit(1)
