"""
Auto-restart wrapper for main.py.

Rules:
  - Restart on crash (exit code != 0), wait 30 s between attempts.
  - Max 5 crashes within any rolling 1-hour window.
  - If limit reached: send Telegram alert, then exit.
  - Exit code 0 from main.py is treated as intentional stop — no restart.
  - All crashes written to logs/crash.log.

Usage:
    python bot_runner.py
"""

import os
import subprocess
import sys
import time
import logging
from datetime import datetime, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parent / '.env', override=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("logs/bot_runner.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("bot-runner")

_MAX_CRASHES   = 5
_WINDOW_HOURS  = 1
_RESTART_DELAY = 30   # seconds between crash and restart


def _send_telegram(text: str) -> None:
    token   = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not (token and chat_id):
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as e:
        logger.warning(f"Telegram alert gagal: {e}")


def _tail_log(n: int = 20) -> str:
    log = Path("logs/bot.log")
    if not log.exists():
        return "(bot.log tidak ditemukan)"
    lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(lines[-n:])


def _write_crash_log(attempt: int, returncode: int, snippet: str) -> None:
    Path("logs").mkdir(exist_ok=True)
    entry = (
        f"\n{'='*60}\n"
        f"CRASH #{attempt}  —  {datetime.now().isoformat()}\n"
        f"Exit code: {returncode}\n"
        f"--- last log lines ---\n{snippet}\n"
    )
    with open("logs/crash.log", "a", encoding="utf-8") as f:
        f.write(entry)


def run() -> None:
    Path("logs").mkdir(exist_ok=True)
    crash_times: list[datetime] = []   # rolling window of crash timestamps

    logger.info("bot_runner dimulai — menjalankan main.py")
    _send_telegram("🚀 <b>Bot Runner dimulai.</b>\nmain.py akan diawasi dan di-restart otomatis jika crash.")

    attempt = 0
    while True:
        attempt += 1
        start_ts = datetime.now()
        logger.info(f"[attempt {attempt}] Memulai main.py …")

        proc = subprocess.Popen(
            [sys.executable, "main.py"],
            stdout=None,   # inherit — main.py logs to its own file
            stderr=None,
        )

        try:
            proc.wait()
        except KeyboardInterrupt:
            logger.info("bot_runner: Ctrl+C — menghentikan main.py")
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
            _send_telegram("🛑 <b>Bot Runner dihentikan manual (Ctrl+C).</b>")
            sys.exit(0)

        rc = proc.returncode
        duration = (datetime.now() - start_ts).total_seconds()

        if rc == 0:
            logger.info(f"main.py berhenti normal (exit 0) setelah {duration:.0f}s — tidak di-restart.")
            _send_telegram("✅ <b>Bot berhenti normal.</b>\nmain.py keluar dengan kode 0.")
            sys.exit(0)

        # ── Crash path ────────────────────────────────────────────────────────
        snippet = _tail_log(20)
        _write_crash_log(attempt, rc, snippet)

        # Prune crash timestamps older than the rolling window
        now    = datetime.now()
        cutoff = now - timedelta(hours=_WINDOW_HOURS)
        crash_times = [t for t in crash_times if t > cutoff]
        crash_times.append(now)

        crash_count = len(crash_times)
        logger.warning(
            f"main.py crash (exit {rc}) setelah {duration:.0f}s — "
            f"crash ke-{crash_count}/{_MAX_CRASHES} dalam {_WINDOW_HOURS} jam terakhir"
        )

        short_snippet = "\n".join(snippet.splitlines()[-5:])
        _send_telegram(
            f"⚠️ <b>Bot Crash! Restart ke-{crash_count}/{_MAX_CRASHES}</b>\n\n"
            f"Exit code: <code>{rc}</code>\n"
            f"Durasi: {duration:.0f}s\n\n"
            f"📋 <b>Log terakhir:</b>\n<pre>{short_snippet[:800]}</pre>"
        )

        if crash_count >= _MAX_CRASHES:
            msg = (
                f"🚨 <b>Bot berhenti setelah {_MAX_CRASHES} crash dalam {_WINDOW_HOURS} jam.</b>\n\n"
                f"Cek <code>logs/crash.log</code> dan <code>logs/bot.log</code> untuk detail.\n"
                f"Jalankan ulang secara manual setelah masalah diselesaikan."
            )
            logger.critical(f"Batas crash ({_MAX_CRASHES}) tercapai — bot_runner berhenti.")
            _send_telegram(msg)
            sys.exit(1)

        logger.info(f"Menunggu {_RESTART_DELAY}s sebelum restart …")
        time.sleep(_RESTART_DELAY)


if __name__ == "__main__":
    run()
