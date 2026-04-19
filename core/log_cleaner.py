"""
Log Cleaner — runs daily at midnight to archive and compress old log files.

Rules:
  a) decisions.json > 1000 lines → archive oldest, keep 500 latest
  b) *.log > 10 MB → gzip + archive, reset to empty
  c) backtest files → keep 5 latest, archive older
  d) logs/archive/ → delete entries older than 90 days
"""

import gzip
import json
import logging
import shutil
import threading
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

_LOGS_DIR       = Path("logs")
_ARCHIVE_DIR    = Path("logs/archive")
_MAINT_LOG      = Path("logs/maintenance.log")
_DECISIONS_LOG  = Path("logs/decisions.json")

_MAX_DECISIONS  = 1000
_KEEP_DECISIONS = 500
_MAX_LOG_MB     = 10
_KEEP_BACKTEST  = 5
_ARCHIVE_MAX_DAYS = 90


class LogCleaner:

    def __init__(self, notifier):
        self._notifier   = notifier
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self) -> None:
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop, name="log-cleaner", daemon=True
        )
        self._thread.start()
        logger.info(
            f"LogCleaner: background thread dimulai — "
            f"cleanup berikutnya dalam {self._seconds_until_midnight()/3600:.1f} jam"
        )

    def stop(self) -> None:
        self._stop_event.set()

    def run_now(self) -> dict:
        """Run cleanup immediately and return summary dict."""
        return self._cleanup()

    # ── Loop ─────────────────────────────────────────────────────────────────

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            secs = self._seconds_until_midnight()
            if self._stop_event.wait(secs):
                break
            self._cleanup()

    @staticmethod
    def _seconds_until_midnight() -> float:
        now       = datetime.now()
        tomorrow  = (now + timedelta(days=1)).replace(
            hour=0, minute=0, second=10, microsecond=0
        )
        return (tomorrow - now).total_seconds()

    # ── Cleanup ───────────────────────────────────────────────────────────────

    def _cleanup(self) -> dict:
        _ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
        summary = {"decisions_archived": 0, "logs_compressed": 0, "space_freed_mb": 0.0}
        try:
            summary["decisions_archived"]           = self._cleanup_decisions()
            comp, freed                             = self._cleanup_logs()
            summary["logs_compressed"]              = comp
            summary["space_freed_mb"]               = freed
            self._cleanup_old_archives()
            self._cleanup_backtest_files()
            self._write_maint_log(summary)
            self._send_summary(summary)
        except Exception as e:
            logger.error(f"LogCleaner._cleanup error: {e}", exc_info=True)
        return summary

    def _cleanup_decisions(self) -> int:
        if not _DECISIONS_LOG.exists():
            return 0
        with open(_DECISIONS_LOG, encoding="utf-8") as f:
            lines = [ln for ln in f if ln.strip()]
        if len(lines) <= _MAX_DECISIONS:
            return 0

        now      = datetime.now()
        archive  = _ARCHIVE_DIR / f"decisions_{now.strftime('%Y-%m')}.jsonl.gz"
        to_arch  = lines[:-_KEEP_DECISIONS]

        with gzip.open(archive, "at", encoding="utf-8") as gz:
            gz.writelines(to_arch)
        with open(_DECISIONS_LOG, "w", encoding="utf-8") as f:
            f.writelines(lines[-_KEEP_DECISIONS:])

        logger.info(f"LogCleaner: archived {len(to_arch)} decisions → {archive.name}")
        return len(to_arch)

    def _cleanup_logs(self) -> tuple[int, float]:
        count, freed = 0, 0.0
        for log_path in _LOGS_DIR.glob("*.log"):
            size_mb = log_path.stat().st_size / 1_048_576
            if size_mb < _MAX_LOG_MB:
                continue
            now     = datetime.now()
            archive = _ARCHIVE_DIR / f"{log_path.stem}_{now.strftime('%Y-%m-%d')}.log.gz"
            with open(log_path, "rb") as fi, gzip.open(archive, "wb") as fo:
                shutil.copyfileobj(fi, fo)
            log_path.write_text("", encoding="utf-8")
            freed += size_mb
            count += 1
            logger.info(
                f"LogCleaner: compressed {log_path.name} "
                f"({size_mb:.1f} MB) → {archive.name}"
            )
        return count, round(freed, 1)

    def _cleanup_old_archives(self) -> None:
        cutoff = datetime.now() - timedelta(days=_ARCHIVE_MAX_DAYS)
        for f in _ARCHIVE_DIR.rglob("*"):
            if f.is_file() and datetime.fromtimestamp(f.stat().st_mtime) < cutoff:
                f.unlink()
                logger.info(f"LogCleaner: deleted old archive {f.name}")

    def _cleanup_backtest_files(self) -> None:
        bt_dir = _ARCHIVE_DIR / "backtest"
        bt_dir.mkdir(exist_ok=True)
        for pattern in ["equity_curve*.png", "backtest_results*.json"]:
            files = sorted(
                Path(".").glob(pattern),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            for f in files[_KEEP_BACKTEST:]:
                shutil.move(str(f), bt_dir / f.name)
                logger.info(f"LogCleaner: archived old backtest file {f.name}")

    def _write_maint_log(self, summary: dict) -> None:
        entry = {"timestamp": datetime.now().isoformat(), "summary": summary}
        with open(_MAINT_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")

    def _send_summary(self, summary: dict) -> None:
        d, c, mb = (
            summary["decisions_archived"],
            summary["logs_compressed"],
            summary["space_freed_mb"],
        )
        if d == 0 and c == 0:
            return
        parts = []
        if d > 0:
            parts.append(f"{d} decisions diarsipkan")
        if c > 0:
            parts.append(f"{c} log files dikompres ({mb:.1f} MB freed)")
        self._notifier._send(
            "🧹 <b>Log Cleanup Selesai</b>\n\n"
            + "\n".join(f"  • {p}" for p in parts)
        )
