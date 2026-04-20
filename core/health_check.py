"""
Health Checker — checks all external services and system resources every hour.

Saves current state to logs/health.json.
Sends Telegram alert only when a service transitions to DOWN/SLOW.
Sends a daily summary at 08:00 if all services are OK.
"""

import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

_HEALTH_FILE    = Path("logs/health.json")
_CHECK_INTERVAL = 3600   # 1 hour in seconds


def _env_val(key: str) -> str:
    """Read a key from .env file directly (bypasses cached os.environ)."""
    from dotenv import dotenv_values
    return dotenv_values(".env").get(key) or os.getenv(key, "")


class HealthChecker:

    def __init__(self, notifier):
        self._notifier   = notifier
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_alert_key: str | None      = None
        self._daily_summary_date: str | None  = None
        Path("logs").mkdir(exist_ok=True)

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self) -> None:
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop, name="health-checker", daemon=True
        )
        self._thread.start()
        logger.info("HealthChecker: background thread dimulai (interval 1 jam)")

    def stop(self) -> None:
        self._stop_event.set()

    def check_now(self) -> dict:
        return self._run_check()

    # ── Loop ─────────────────────────────────────────────────────────────────

    def _loop(self) -> None:
        self._run_check()   # immediate on start
        while not self._stop_event.wait(_CHECK_INTERVAL):
            self._run_check()
            self._maybe_daily_summary()

    def _maybe_daily_summary(self) -> None:
        now   = datetime.now()
        today = str(now.date())
        if now.hour == 8 and self._daily_summary_date != today:
            self._send_daily_summary()
            self._daily_summary_date = today

    # ── Check ─────────────────────────────────────────────────────────────────

    def _run_check(self) -> dict:
        result = {
            "timestamp": datetime.now().isoformat(),
            "binance":   self._check_binance(),
            "anthropic": self._check_anthropic(),
            "telegram":  self._check_telegram(),
            "system":    self._check_system(),
        }
        _HEALTH_FILE.write_text(
            json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        # Collect problem lines
        problem_lines = []
        for svc in ("binance", "anthropic", "telegram"):
            s = result[svc].get("status", "UNKNOWN")
            if s not in ("OK",):
                icon = "🔴" if s == "DOWN" else "🟡"
                lat  = result[svc].get("latency_ms")
                lat_str = f" ({lat} ms)" if lat else ""
                err_str = f": {result[svc].get('error','')[:60]}" if result[svc].get("error") else ""
                problem_lines.append(
                    f"  {icon} {svc.capitalize()}: {s}{lat_str}{err_str}"
                )

        sys_r = result["system"]
        if sys_r.get("status") == "WARNING":
            warn_parts = []
            if sys_r.get("cpu_pct", 0) > 80:
                warn_parts.append(f"CPU {sys_r['cpu_pct']}%")
            if sys_r.get("mem_pct", 0) > 85:
                warn_parts.append(f"RAM {sys_r['mem_pct']}%")
            if sys_r.get("disk_mb", 0) > 1024:
                warn_parts.append(f"Disk {sys_r['disk_mb']:.0f} MB")
            if warn_parts:
                problem_lines.append(f"  🟡 System: {', '.join(warn_parts)}")

        if problem_lines:
            alert_key = "|".join(problem_lines)
            if alert_key != self._last_alert_key:
                self._last_alert_key = alert_key
                self._send_alert(result, problem_lines)
        else:
            self._last_alert_key = None

        b = result["binance"]["status"]
        a = result["anthropic"]["status"]
        t = result["telegram"]["status"]
        logger.info(f"Health check: Binance={b} Anthropic={a} Telegram={t}")
        return result

    # ── Individual checks ─────────────────────────────────────────────────────

    def _check_binance(self) -> dict:
        try:
            start = time.monotonic()
            r     = requests.get(
                "https://testnet.binance.vision/api/v3/ping", timeout=8
            )
            latency = round((time.monotonic() - start) * 1000)
            status  = "OK" if r.status_code == 200 else "DOWN"
            if status == "OK" and latency > 2000:
                status = "SLOW"
            return {"status": status, "latency_ms": latency}
        except Exception as e:
            return {"status": "DOWN", "error": str(e)[:100]}

    def _check_anthropic(self) -> dict:
        try:
            import anthropic as _anthropic
            api_key = _env_val("ANTHROPIC_API_KEY")
            if not api_key:
                return {"status": "DOWN", "error": "API key tidak ada"}
            client  = _anthropic.Anthropic(api_key=api_key)
            start   = time.monotonic()
            client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=1,
                messages=[{"role": "user", "content": "ping"}],
            )
            latency = round((time.monotonic() - start) * 1000)
            status  = "SLOW" if latency > 5000 else "OK"
            return {"status": status, "latency_ms": latency}
        except Exception as e:
            err = str(e)[:100]
            if any(kw in err.lower() for kw in ("credit", "balance", "quota", "billing")):
                return {"status": "LOW_CREDIT", "error": err}
            return {"status": "DOWN", "error": err}

    def _check_telegram(self) -> dict:
        token = _env_val("TELEGRAM_BOT_TOKEN")
        if not token:
            return {"status": "DOWN", "error": "Token tidak ada"}
        try:
            start = time.monotonic()
            r     = requests.get(
                f"https://api.telegram.org/bot{token}/getMe", timeout=8
            )
            latency = round((time.monotonic() - start) * 1000)
            status  = "OK" if r.status_code == 200 else "DOWN"
            if status == "OK" and latency > 2000:
                status = "SLOW"
            return {"status": status, "latency_ms": latency}
        except Exception as e:
            return {"status": "DOWN", "error": str(e)[:100]}

    def _check_system(self) -> dict:
        try:
            import psutil
            cpu     = round(psutil.cpu_percent(interval=1))
            mem     = round(psutil.virtual_memory().percent)
            disk_mb = round(
                sum(
                    f.stat().st_size for f in Path("logs").rglob("*") if f.is_file()
                ) / 1_048_576,
                1,
            )
            status = "WARNING" if (cpu > 80 or mem > 85 or disk_mb > 1024) else "OK"
            return {
                "status":  status,
                "cpu_pct": cpu,
                "mem_pct": mem,
                "disk_mb": disk_mb,
            }
        except ImportError:
            return {"status": "UNKNOWN", "error": "psutil tidak terinstall"}
        except Exception as e:
            return {"status": "UNKNOWN", "error": str(e)[:100]}

    # ── Notifications ─────────────────────────────────────────────────────────

    def _send_alert(self, result: dict, problem_lines: list[str]) -> None:
        sys_r = result["system"]
        cpu   = sys_r.get("cpu_pct", "?")
        mem   = sys_r.get("mem_pct", "?")
        disk  = sys_r.get("disk_mb", "?")
        self._notifier._send(
            "🔴 <b>Health Check Alert</b>\n\n"
            + "\n".join(problem_lines)
            + f"\n\n💻 CPU: {cpu}% | RAM: {mem}% | Disk: {disk} MB"
        )

    def _send_daily_summary(self) -> None:
        if not _HEALTH_FILE.exists():
            return
        try:
            data = json.loads(_HEALTH_FILE.read_text(encoding="utf-8"))
        except Exception:
            return
        b = data.get("binance",   {})
        a = data.get("anthropic", {})
        t = data.get("telegram",  {})
        s = data.get("system",    {})
        all_ok = all(d.get("status") == "OK" for d in [b, a, t])
        sys_ok = s.get("status") in ("OK", "UNKNOWN")
        if all_ok and sys_ok:
            self._notifier._send(
                "📊 <b>Daily Health Report</b> — Semua sistem normal\n\n"
                f"  • Binance: {b.get('latency_ms','?')} ms\n"
                f"  • Anthropic: {a.get('latency_ms','?')} ms\n"
                f"  • Telegram: {t.get('latency_ms','?')} ms\n"
                f"  • CPU: {s.get('cpu_pct','?')}% | "
                f"RAM: {s.get('mem_pct','?')}% | "
                f"Disk: {s.get('disk_mb','?')} MB"
            )
