import html
import os
import logging
import threading
import time
import requests
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

_ACTION_EMOJI = {"BUY": "🟢", "SELL": "🔴", "HOLD": "🟡"}
_CONFIDENCE_EMOJI = {"HIGH": "🔥", "MEDIUM": "⚡", "LOW": "❄️"}

class TelegramNotifier:
    def __init__(self):
        self.token = os.getenv('TELEGRAM_BOT_TOKEN')
        self.chat_id = os.getenv('TELEGRAM_CHAT_ID')
        self.enabled = bool(self.token and self.chat_id)

        if not self.enabled:
            logger.warning(
                "TELEGRAM_BOT_TOKEN atau TELEGRAM_CHAT_ID tidak diset — "
                "notifikasi Telegram dinonaktifkan"
            )

    def _send(self, text: str, max_retries: int = 3) -> bool:
        """Kirim pesan ke Telegram dengan retry. Return True jika berhasil."""
        if not self.enabled:
            return False

        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
        }

        for attempt in range(1, max_retries + 1):
            try:
                resp = requests.post(url, json=payload, timeout=15)
                resp.raise_for_status()
                return True
            except requests.exceptions.Timeout:
                logger.warning(f"Telegram: timeout (attempt {attempt}/{max_retries})")
            except requests.exceptions.HTTPError as e:
                status = e.response.status_code
                # 4xx errors (bad token, chat not found) won't recover — stop retrying
                if 400 <= status < 500:
                    logger.warning(f"Telegram HTTP {status} — {e.response.text[:200]}")
                    return False
                logger.warning(f"Telegram HTTP {status} (attempt {attempt}/{max_retries})")
            except requests.exceptions.RequestException as e:
                logger.warning(f"Telegram request gagal (attempt {attempt}/{max_retries}): {e}")

            if attempt < max_retries:
                time.sleep(2 ** attempt)  # 2s, 4s

        logger.error(f"Telegram: pesan gagal dikirim setelah {max_retries} attempt")
        return False

    def notify_decision(self, decision: dict, portfolio: dict) -> None:
        """Notifikasi keputusan trading dari Claude."""
        action = decision.get('action', 'HOLD')
        confidence = decision.get('confidence', 'LOW')
        size_pct = decision.get('size_pct', 0)
        reason = html.escape(str(decision.get('reason', '-')))
        stop_loss = decision.get('stop_loss_pct', 0)
        take_profit = decision.get('take_profit_pct', 0)

        btc_price = portfolio.get('btc_price', 0)
        total_value = portfolio.get('total_value_usdt', 0)
        usdt_available = portfolio.get('usdt_available', 0)
        btc_held = portfolio.get('btc_held', 0)

        action_emoji = _ACTION_EMOJI.get(action, '⚪')
        conf_emoji = _CONFIDENCE_EMOJI.get(confidence, '')

        if action == 'BUY':
            usdt_used = usdt_available * (size_pct / 100)
            size_detail = f"${usdt_used:,.2f} USDT ({size_pct}% dari available)"
        elif action == 'SELL':
            btc_sold = btc_held * (size_pct / 100)
            size_detail = f"{btc_sold:.6f} BTC ({size_pct}% dari held)"
        else:
            size_detail = "—"

        lines = [
            f"{action_emoji} <b>Trading Decision: {action}</b>",
            "",
            f"💰 <b>Harga BTC:</b> ${btc_price:,.2f}",
            f"📊 <b>Confidence:</b> {conf_emoji} {confidence}",
            f"📦 <b>Size:</b> {size_detail}",
        ]

        if action != 'HOLD':
            lines += [
                f"🛑 <b>Stop Loss:</b> -{stop_loss}%",
                f"🎯 <b>Take Profit:</b> +{take_profit}%",
            ]

        lines += [
            "",
            f"💬 <b>Alasan:</b> {reason}",
            "",
            "📂 <b>Portfolio</b>",
            f"  • Total: ${total_value:,.2f}",
            f"  • USDT: ${usdt_available:,.2f}",
            f"  • BTC: {btc_held:.6f} BTC",
        ]

        self._send("\n".join(lines))

    def notify_circuit_breaker(self, current_balance: float, starting_balance: float, max_drawdown_pct: float) -> None:
        """Alert circuit breaker aktif."""
        drawdown = ((starting_balance - current_balance) / starting_balance * 100) if starting_balance else 0
        text = (
            "🚨 <b>CIRCUIT BREAKER AKTIF</b> 🚨\n"
            "\n"
            "Bot telah menghentikan semua aktivitas trading karena drawdown melebihi batas.\n"
            "\n"
            f"📉 <b>Drawdown:</b> <b>{drawdown:.1f}%</b> (max: {max_drawdown_pct}%)\n"
            f"💵 <b>Balance awal:</b> ${starting_balance:,.2f}\n"
            f"💵 <b>Balance sekarang:</b> ${current_balance:,.2f}\n"
            f"💸 <b>Kerugian:</b> ${starting_balance - current_balance:,.2f}"
        )
        self._send(text)

    def notify_error(self, context: str, error: Exception) -> None:
        """Alert error tak terduga."""
        text = (
            "⚠️ <b>Bot Error</b>\n"
            "\n"
            f"📍 <b>Konteks:</b> {html.escape(context)}\n"
            f"❌ <b>Error:</b> <code>{html.escape(f'{type(error).__name__}: {str(error)[:300]}')}</code>"
        )
        self._send(text)

    def notify_startup(self, pair: str, timeframe: str, starting_balance: float) -> None:
        """Notifikasi bot mulai berjalan."""
        text = (
            "✅ <b>Trading Bot Started</b>\n"
            "\n"
            f"📈 <b>Pair:</b> {pair}\n"
            f"⏱ <b>Timeframe:</b> {timeframe}\n"
            f"💰 <b>Starting balance:</b> ${starting_balance:,.2f}"
        )
        self._send(text)

    def notify_shutdown(self, reason: str) -> None:
        """Notifikasi bot berhenti."""
        text = (
            "🛑 <b>Trading Bot Stopped</b>\n"
            "\n"
            f"📋 <b>Alasan:</b> {reason}"
        )
        self._send(text)


class TelegramCommandHandler:
    """
    Poll Telegram getUpdates every 5 s in a background thread.
    Only processes messages from the whitelisted TELEGRAM_CHAT_ID.
    Register callbacks via register(); each callback receives no arguments
    but may call back into the bot state via closures.
    """

    _POLL_INTERVAL = 5  # seconds

    def __init__(self, notifier: TelegramNotifier):
        self._notifier  = notifier
        self._token     = notifier.token
        self._chat_id   = str(notifier.chat_id or "")
        self._enabled   = notifier.enabled
        self._offset    = 0
        self._callbacks: dict[str, callable] = {}
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    # ── Public API ─────────────────────────────────────────────────────────────

    def register(self, command: str, callback) -> None:
        """Register a callback for /command. Strip leading slash."""
        key = command.lstrip("/").lower()
        self._callbacks[key] = callback

    def start(self) -> None:
        if not self._enabled:
            logger.warning("TelegramCommandHandler: Telegram tidak diaktifkan — command handler tidak berjalan")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._poll_loop, name="tg-cmd-handler", daemon=True)
        self._thread.start()
        logger.info("TelegramCommandHandler: background thread dimulai")

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=10)
        logger.info("TelegramCommandHandler: thread berhenti")

    # ── Internal ───────────────────────────────────────────────────────────────

    def _poll_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._fetch_updates()
            except Exception as e:
                logger.debug(f"TelegramCommandHandler poll error: {e}")
            self._stop_event.wait(self._POLL_INTERVAL)

    def _fetch_updates(self) -> None:
        url = f"https://api.telegram.org/bot{self._token}/getUpdates"
        params = {"offset": self._offset, "timeout": 4, "limit": 20}
        try:
            resp = requests.get(url, params=params, timeout=8)
            resp.raise_for_status()
        except requests.exceptions.RequestException as e:
            logger.debug(f"getUpdates gagal: {e}")
            return

        data = resp.json()
        if not data.get("ok"):
            return

        for update in data.get("result", []):
            self._offset = update["update_id"] + 1
            self._handle_update(update)

    def _handle_update(self, update: dict) -> None:
        msg = update.get("message") or update.get("edited_message")
        if not msg:
            return

        sender_id = str(msg.get("chat", {}).get("id", ""))
        if sender_id != self._chat_id:
            logger.warning(f"TelegramCommandHandler: pesan dari chat_id tidak dikenal: {sender_id} — diabaikan")
            self._notifier._send("⛔ Akses ditolak: chat_id tidak dikenali.")
            return

        text = (msg.get("text") or "").strip()
        if not text.startswith("/"):
            return

        # Extract command (strip bot username suffix like /status@mybot)
        parts   = text.split()
        command = parts[0].lstrip("/").split("@")[0].lower()
        args    = parts[1:]

        cb = self._callbacks.get(command)
        if cb:
            try:
                cb(*args)
            except Exception as e:
                logger.error(f"TelegramCommandHandler: error saat menjalankan /{command}: {e}")
                self._notifier._send(f"❌ Error saat menjalankan /{html.escape(command)}: {html.escape(str(e))}")
        else:
            self._notifier._send(
                f"❓ Perintah tidak dikenal: <code>/{html.escape(command)}</code>\n"
                "Ketik /help untuk daftar perintah."
            )
